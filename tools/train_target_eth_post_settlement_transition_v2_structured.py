from __future__ import annotations
import argparse,json,sqlite3,math,os,random,joblib
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
from sklearn.inspection import permutation_importance

SEED=20260904
random.seed(SEED);np.random.seed(SEED)
EPS=1e-9
BASE_FEATURES=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_ratio','gross_log','overflow_gap_ratio','price','route_taker','delta_floor_ratio','delta_best_ratio','recent_repair_frac','recent_expand_frac','recent_taker_frac','prev_age_log','event_count_norm']
LEDGER_FEATURES=['physical_qty_gross','repair_paid_gross','overflow_gross','overflow_to_repair','crossing_parent_notional_ratio','recent30_repair_qty_gross','recent30_expand_qty_gross','recent30_repair_notional_ratio','recent30_expand_notional_ratio','recent30_crossing_count_norm','recent30_overflow_gross','last_repair_age_log','last_expand_age_log','role_streak_norm','recent30_transition_rate','recent30_parent_count_norm','side_up','post_weak_side_up','post_floor_to_best_span','post_floor_reserve_pos']
ALL_FEATURES=BASE_FEATURES+LEDGER_FEATURES
TASKS=('next_role_repair','next_composite')

def econ_role(side,up,dn):
    if up+dn<=EPS or abs(up-dn)<=EPS:return 0
    weak='UP' if up<dn else 'DOWN'
    return 1 if side==weak else -1

def met(up,dn,cost):
    gross=up+dn;pair=min(up,dn);gap=abs(up-dn);pc=2*pair/gross if gross>EPS else 1.;ab=gap/gross if gross>EPS else 0.;floor=pair-cost;best=max(up,dn)-cost
    return {'gross':gross,'pair':pair,'gap':gap,'pc':pc,'ab':ab,'floor':floor,'best':best}

def safe_log_age(x): return math.log1p(min(max(float(x),0.),120000.))/math.log1p(120000.)

def build(db):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close()
    by=defaultdict(list)
    for r in raw:by[int(r['market_id'])].append(r)
    ends=sorted(set(mend.values()));cut1=ends[int(len(ends)*.60)];cut2=ends[int(len(ends)*.80)]
    rows=[];crossing_states=0;continued=0
    for mid,evs in by.items():
        up=dn=cost=0.;hist=[];prev_t=None;last_rel=0;role_streak=0;last_rep=None;last_exp=None
        for i,r in enumerate(evs):
            side=str(r['side']).upper();route=str(r['role']).upper();t=int(r['first_event_ms']);q=float(r['shares']);px=float(r['average_price']);pre=met(up,dn,cost);rel=econ_role(side,up,dn);pre_gap=pre['gap'];pre_cost=cost
            if side=='UP':up+=q
            else:dn+=q
            cost+=q*px;post=met(up,dn,cost)
            crossing=(rel==1 and pre_gap>EPS and q>=pre_gap-EPS);repair_paid=min(q,pre_gap) if rel==1 else 0.;overflow=max(0.,q-pre_gap) if crossing else 0.;elapsed=1e6 if prev_t is None else t-prev_t
            if rel!=0:
                role_streak=role_streak+1 if rel==last_rel else 1;last_rel=rel
                if rel==1:last_rep=t
                else:last_exp=t
            hist.append({'t':t,'rel':rel,'route':route,'q':q,'px':px,'crossing':crossing,'overflow':overflow,'notional':q*px})
            if crossing:
                crossing_states+=1;nxt=evs[i+1] if i+1<len(evs) else None;nr=None;nc=None;delay=None
                if nxt is not None:
                    delay=int(nxt['first_event_ms'])-t
                    if delay<=30000:
                        continued+=1;ns=str(nxt['side']).upper();nq=float(nxt['shares']);nr=econ_role(ns,up,dn);ng=post['gap'];nc=int(nr==1 and ng>EPS and nq>=ng-EPS)
                if nr is not None:
                    recent=[x for x in hist if t-int(x['t'])<=30000]
                    rr=[x for x in recent if x['rel']==1];ee=[x for x in recent if x['rel']==-1];tt=[x for x in recent if x['route']=='TAKER'];trans=sum(1 for a,b in zip(recent,recent[1:]) if a['rel']!=0 and b['rel']!=0 and a['rel']!=b['rel'])
                    gross=max(post['gross'],EPS);den_cost=max(abs(cost),1.);end=mend.get(mid);sl=0. if end is None else max(-30.,min(330.,(end-t)/1000.))/300.;recent_n=max(1,len(recent));span=max(abs(post['best']-post['floor']),1.)
                    base=[sl,post['pc'],post['ab'],max(-5.,min(5.,post['floor']/den_cost)),max(-5.,min(5.,post['best']/den_cost)),math.log1p(post['gross'])/math.log1p(500),max(0.,min(6.,overflow/max(pre_gap,EPS))),px,float(route=='TAKER'),max(-5.,min(5.,(post['floor']-pre['floor'])/den_cost)),max(-5.,min(5.,(post['best']-pre['best'])/den_cost)),len(rr)/recent_n,len(ee)/recent_n,len(tt)/recent_n,safe_log_age(elapsed),math.log1p(len(hist))/math.log1p(128)]
                    post_weak='UP' if up<dn-EPS else 'DOWN' if dn<up-EPS else 'FLAT'
                    ledger=[q/gross,repair_paid/gross,overflow/gross,max(0.,min(12.,overflow/max(repair_paid,EPS))),q*px/den_cost,sum(x['q'] for x in rr)/gross,sum(x['q'] for x in ee)/gross,sum(x['notional'] for x in rr)/den_cost,sum(x['notional'] for x in ee)/den_cost,min(1.,sum(bool(x['crossing']) for x in recent)/8.),sum(float(x['overflow']) for x in recent)/gross,safe_log_age(1e6 if last_rep is None else t-last_rep),safe_log_age(1e6 if last_exp is None else t-last_exp),min(role_streak,12)/12.,trans/max(1,len(recent)-1),min(len(recent),32)/32.,float(side=='UP'),float(post_weak=='UP'),max(-5.,min(5.,post['floor']/span)),float(post['floor']>=0.)]
                    rows.append({'market':mid,'end':end,'t':t,'x_base':np.asarray(base,np.float32),'x_all':np.asarray(base+ledger,np.float32),'next_role_repair':int(nr==1),'next_composite':int(nc),'next_delay_ms':delay})
            prev_t=t
    for r in rows:r['split']='train' if int(r['end'] or 0)<cut1 else 'validation' if int(r['end'] or 0)<cut2 else 'test'
    return rows,cut1,cut2,len(ends),{'crossingRepairStates':crossing_states,'continuedWithin30s':continued,'continuationRate':continued/max(1,crossing_states)}

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def sample_weights(y):
    y=np.asarray(y,int);p=max(float(y.mean()),1e-6);return np.where(y==1,0.5/p,0.5/max(1-p,1e-6))

def fit_model(X,y):
    m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=SEED,early_stopping=False)
    m.fit(X,y,sample_weight=sample_weights(y));return m

def score_model(m,X,y):return metric(y,m.predict_proba(X)[:,1])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();rows,c1,c2,nwin,anatomy=build(a.db)
    tr=[r for r in rows if r['split']=='train'];va=[r for r in rows if r['split']=='validation'];te=[r for r in rows if r['split']=='test']
    def arr(z,key,task):return np.stack([r[key] for r in z]),np.asarray([r[task] for r in z],int)
    result={'baseline':{},'structured':{},'lift':{},'importance':{}}
    models={}
    for task in TASKS:
        xb,yb=arr(tr,'x_base',task);xa,ya=arr(tr,'x_all',task);b=fit_model(xb,yb);s=fit_model(xa,ya);models[task]=s
        result['baseline'][task]={};result['structured'][task]={};result['lift'][task]={}
        for split,z in [('validation',va),('test',te)]:
            Xb,y=arr(z,'x_base',task);Xa,_=arr(z,'x_all',task);bm=score_model(b,Xb,y);sm=score_model(s,Xa,y);result['baseline'][task][split]=bm;result['structured'][task][split]=sm;result['lift'][task][split]=sm['auc']-bm['auc']
        # Fixed test permutation diagnostic; no feature-selection loop.
        Xt,yt=arr(te,'x_all',task);pi=permutation_importance(s,Xt,yt,n_repeats=3,random_state=SEED,scoring='roc_auc',n_jobs=1);idx=np.argsort(pi.importances_mean)[::-1][:12];result['importance'][task]=[{'feature':ALL_FEATURES[int(i)],'aucDropMean':float(pi.importances_mean[int(i)]),'aucDropStd':float(pi.importances_std[int(i)])} for i in idx]
    gates={
      'roleValidationNoRegression':result['lift']['next_role_repair']['validation']>=-.005,
      'roleTestLift':result['lift']['next_role_repair']['test']>=.015,
      'roleTestAuc':result['structured']['next_role_repair']['test']['auc']>=.625,
      'compositeValidationNoRegression':result['lift']['next_composite']['validation']>=-.005,
      'compositeTestLift':result['lift']['next_composite']['test']>=.010,
      'compositeTestAuc':result['structured']['next_composite']['test']['auc']>=.65,
    }
    out={'version':'TARGET_ETH_POST_SETTLEMENT_TRANSITION_V2_STRUCTURED','date':'2026-09-04','researchOnly':True,'sourceDb':os.path.abspath(a.db),'windows':nwin,'datasetAnatomy':anatomy,'splitCutoffs':{'trainEndExclusiveMs':c1,'validationEndExclusiveMs':c2},'rows':len(rows),'trainRows':len(tr),'validationRows':len(va),'testRows':len(te),'features':{'base':BASE_FEATURES,'ledger':LEDGER_FEATURES,'all':ALL_FEATURES},'tasks':list(TASKS),'metrics':result,'gates':gates,'decision':'KEEP_V2_STRUCTURED_AS_SHADOW' if all(gates.values()) else 'REJECT_OR_PARTIAL_KEEP_V2_STRUCTURED','boundary':['fixed chronology 60/20/20 split','same fixed HistGradientBoosting hyperparameters for base vs structured feature A/B','no threshold sweep','Target future next-parent role/composite is offline label only','no winner/future PnL feature','information/shadow only; no action authority','ETH-only gradients']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'models':models,'features':ALL_FEATURES,'boundary':out['boundary']},a.model_out);print(json.dumps({'ok':True,'decision':out['decision'],'rows':[len(tr),len(va),len(te)],'gates':gates,'metrics':result},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
