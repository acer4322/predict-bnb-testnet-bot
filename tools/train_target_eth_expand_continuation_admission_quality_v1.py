from __future__ import annotations
import argparse,json,sqlite3,math,os,random,joblib,statistics
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
CTX_FEATURES=['has_prior_same_side_expand','post_dominant_side_up','prior_same_side_expand_age_log','prior_same_side_expand_price','repair_block_qty_gross','repair_block_notional_cost','repair_block_count_norm','repair_block_avg_price','anchor_repair_share_of_block','anchor_repair_price_plus_prior_expand_price']
ALL_FEATURES=BASE_FEATURES+CTX_FEATURES
TASK_CONT='expand_continuation_30s'
TASK_QUAL='non_damaging_expand_quality'

def econ_role(side,up,dn):
    if up+dn<=EPS or abs(up-dn)<=EPS:return 0
    weak='UP' if up<dn else 'DOWN'
    return 1 if side==weak else -1

def met(up,dn,cost):
    gross=up+dn;pair=min(up,dn);gap=abs(up-dn);pc=2*pair/gross if gross>EPS else 1.;ab=gap/gross if gross>EPS else 0.;floor=pair-cost;best=max(up,dn)-cost
    return {'gross':gross,'pair':pair,'gap':gap,'pc':pc,'ab':ab,'floor':floor,'best':best}

def safe_log_age(x): return math.log1p(min(max(float(x),0.),120000.))/math.log1p(120000.)

def weighted_avg(xs):
    q=sum(float(x['q']) for x in xs)
    return (sum(float(x['q'])*float(x['px']) for x in xs)/q) if q>EPS else None

def scan_first_expand(evs,start_i,t0,up0,dn0,anchor):
    up=float(up0);dn=float(dn0);future_rep=[]
    for j in range(start_i+1,len(evs)):
        r=evs[j];t=int(r['first_event_ms'])
        if t<=t0: continue
        if t-t0>30000: break
        side=str(r['side']).upper();q=float(r['shares']);px=float(r['average_price']);rel=econ_role(side,up,dn)
        if rel==-1:
            pay='DOWN' if side=='UP' else 'UP'
            reps=[]
            if str(anchor['side']).upper()==pay:
                reps.append({'t':t0,'side':str(anchor['side']).upper(),'q':float(anchor['repair_paid_qty']),'px':float(anchor['price'])})
            reps.extend(x for x in future_rep if x['side']==pay)
            rq=sum(x['q'] for x in reps);rp=weighted_avg(reps);pair=(rp+px) if rp is not None else None;matched=min(rq,q)
            return {'found':True,'delay_ms':t-t0,'side':side,'route':str(r['role']).upper(),'qty':q,'price':px,'repair_qty':rq,'repair_price':rp,'matched_qty':matched,'pair_sum':pair,'quality':None if pair is None or matched<=EPS else int(pair<=1.0+EPS)}
        if rel==1:
            future_rep.append({'t':t,'side':side,'q':q,'px':px})
        if side=='UP':up+=q
        else:dn+=q
    return {'found':False,'delay_ms':None,'side':None,'route':None,'qty':None,'price':None,'repair_qty':0.0,'repair_price':None,'matched_qty':0.0,'pair_sum':None,'quality':None}

def build(db):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close()
    by=defaultdict(list)
    for r in raw:by[int(r['market_id'])].append(r)
    ends=sorted(set(mend.values()));cut1=ends[int(len(ends)*.60)];cut2=ends[int(len(ends)*.80)]
    rows=[];crossing_states=0;found_expand=0;quality_rows=0;quality_pos=0
    for mid,evs in by.items():
        up=dn=cost=0.;hist=[];prev_t=None;last_rel=0
        for i,r in enumerate(evs):
            side=str(r['side']).upper();route=str(r['role']).upper();t=int(r['first_event_ms']);q=float(r['shares']);px=float(r['average_price']);pre=met(up,dn,cost);rel=econ_role(side,up,dn);pre_gap=pre['gap'];elapsed=1e6 if prev_t is None else t-prev_t
            repair_paid=min(q,pre_gap) if rel==1 else 0.;crossing=(rel==1 and pre_gap>EPS and q>=pre_gap-EPS);overflow=max(0.,q-pre_gap) if crossing else 0.
            if side=='UP':up+=q
            else:dn+=q
            cost+=q*px;post=met(up,dn,cost)
            hist.append({'t':t,'rel':rel,'route':route,'side':side,'q':q,'px':px,'crossing':crossing,'overflow':overflow,'notional':q*px})
            if crossing:
                crossing_states+=1
                recent=[x for x in hist if t-int(x['t'])<=30000];rr=[x for x in recent if x['rel']==1];ee=[x for x in recent if x['rel']==-1];tt=[x for x in recent if x['route']=='TAKER']
                gross=max(post['gross'],EPS);den_cost=max(abs(cost),1.);end=mend.get(mid);sl=0. if end is None else max(-30.,min(330.,(end-t)/1000.))/300.;recent_n=max(1,len(recent))
                base=[sl,post['pc'],post['ab'],max(-5.,min(5.,post['floor']/den_cost)),max(-5.,min(5.,post['best']/den_cost)),math.log1p(post['gross'])/math.log1p(500),max(0.,min(6.,overflow/max(pre_gap,EPS))),px,float(route=='TAKER'),max(-5.,min(5.,(post['floor']-pre['floor'])/den_cost)),max(-5.,min(5.,(post['best']-pre['best'])/den_cost)),len(rr)/recent_n,len(ee)/recent_n,len(tt)/recent_n,safe_log_age(elapsed),math.log1p(len(hist))/math.log1p(128)]
                if up>dn+EPS: dom='UP';weak='DOWN'
                elif dn>up+EPS: dom='DOWN';weak='UP'
                else: dom=side;weak='DOWN' if dom=='UP' else 'UP'
                prior_ex=[x for x in hist[:-1] if x['rel']==-1 and x['side']==dom]
                pe=prior_ex[-1] if prior_ex else None;pe_t=int(pe['t']) if pe else None;pe_px=float(pe['px']) if pe else 0.0
                block=[x for x in hist if x['rel']==1 and x['side']==weak and (pe_t is None or int(x['t'])>pe_t) and int(x['t'])<=t]
                bq=sum(float(x['q']) for x in block);bn=sum(float(x['notional']) for x in block);bpx=bn/bq if bq>EPS else 0.0
                ctx=[float(pe is not None),float(dom=='UP'),safe_log_age(1e6 if pe_t is None else t-pe_t),pe_px,bq/gross,bn/den_cost,min(len(block),12)/12.,bpx,repair_paid/max(bq,EPS) if bq>EPS else 0.0,px+pe_px if pe is not None else px]
                fut=scan_first_expand(evs,i,t,up,dn,{'side':side,'repair_paid_qty':repair_paid,'price':px})
                if fut['found']:found_expand+=1
                if fut['quality'] is not None:
                    quality_rows+=1;quality_pos+=int(fut['quality'])
                rows.append({'market':mid,'end':end,'t':t,'x_base':np.asarray(base,np.float32),'x_all':np.asarray(base+ctx,np.float32),TASK_CONT:int(fut['found']),TASK_QUAL:fut['quality'],'future':fut})
            prev_t=t
    for r in rows:r['split']='train' if int(r['end'] or 0)<cut1 else 'validation' if int(r['end'] or 0)<cut2 else 'test'
    anatomy={'crossingRepairStates':crossing_states,'expandContinuationWithin30s':found_expand,'expandContinuationRate':found_expand/max(1,crossing_states),'qualityLabeledSamples':quality_rows,'nonDamagingQualitySamples':quality_pos,'nonDamagingQualityRate':quality_pos/max(1,quality_rows)}
    return rows,cut1,cut2,len(ends),anatomy

def sample_weights(y):
    y=np.asarray(y,int);p=max(float(y.mean()),1e-6);return np.where(y==1,0.5/p,0.5/max(1-p,1e-6))

def fit_model(X,y):
    m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=SEED,early_stopping=False)
    m.fit(X,y,sample_weight=sample_weights(y));return m

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if len(y) and y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(y) else None}

def task_rows(rows,task):return [r for r in rows if task!=TASK_QUAL or r[TASK_QUAL] is not None]

def arrays(rows,key,task):
    z=task_rows(rows,task);return np.stack([r[key] for r in z]),np.asarray([int(r[task]) for r in z],int),z

def quartile_diag(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float)
    if len(y)<4:return {'n':int(len(y))}
    order=np.argsort(p);parts=np.array_split(order,4);rates=[float(y[ix].mean()) if len(ix) else None for ix in parts];pranges=[{'min':float(p[ix].min()),'max':float(p[ix].max()),'n':int(len(ix))} if len(ix) else None for ix in parts]
    return {'n':int(len(y)),'ratesLowToHigh':rates,'probabilityRanges':pranges,'topMinusBottomRate':rates[-1]-rates[0] if rates[0] is not None and rates[-1] is not None else None}

def fixed_split(rows,task):
    tr=[r for r in rows if r['split']=='train'];va=[r for r in rows if r['split']=='validation'];te=[r for r in rows if r['split']=='test']
    xb,yb,_=arrays(tr,'x_base',task);xa,ya,_=arrays(tr,'x_all',task);b=fit_model(xb,yb);m=fit_model(xa,ya)
    out={'base':{},'enriched':{},'lift':{}}
    for name,z in [('validation',va),('test',te)]:
        Xb,y,_=arrays(z,'x_base',task);Xa,_,_=arrays(z,'x_all',task);pb=b.predict_proba(Xb)[:,1];pm=m.predict_proba(Xa)[:,1];out['base'][name]=metric(y,pb);out['enriched'][name]=metric(y,pm);out['lift'][name]=out['enriched'][name]['auc']-out['base'][name]['auc'];
        if name=='test':out['quartileTest']=quartile_diag(y,pm)
    Xt,yt,_=arrays(te,'x_all',task);pi=permutation_importance(m,Xt,yt,n_repeats=3,random_state=SEED,scoring='roc_auc',n_jobs=1);idx=np.argsort(pi.importances_mean)[::-1][:12];out['importance']=[{'feature':ALL_FEATURES[int(i)],'aucDropMean':float(pi.importances_mean[int(i)]),'aucDropStd':float(pi.importances_std[int(i)])} for i in idx]
    return out

def rolling(rows,task):
    zs=task_rows(rows,task);ends=sorted(set(int(r['end'] or 0) for r in rows));spec=[(.5,.6),(.6,.7),(.7,.8),(.8,1.0)];out=[]
    for k,(a,b) in enumerate(spec,1):
        c1=ends[min(len(ends)-1,int(len(ends)*a))];c2=ends[min(len(ends)-1,int(len(ends)*b)-1)] if b<1 else ends[-1]
        tr=[r for r in zs if int(r['end'] or 0)<c1];te=[r for r in zs if int(r['end'] or 0)>=c1 and int(r['end'] or 0)<=c2]
        if len(tr)<100 or len(te)<50:out.append({'fold':k,'trainN':len(tr),'testN':len(te),'auc':None});continue
        X,y,_=arrays(tr,'x_all',task);Xt,yt,_=arrays(te,'x_all',task);m=fit_model(X,y);p=m.predict_proba(Xt)[:,1];mm=metric(yt,p);out.append({'fold':k,'trainN':len(tr),'testN':len(te),**mm})
    return out

def final_model(rows,task):
    tr=[r for r in task_rows(rows,task) if r['split'] in ('train','validation')];X,y,_=arrays(tr,'x_all',task);return fit_model(X,y),len(tr)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args()
    rows,c1,c2,nwin,an=build(a.db);res={};roll={}
    for task in (TASK_CONT,TASK_QUAL):res[task]=fixed_split(rows,task);roll[task]=rolling(rows,task)
    qaucs=[x['auc'] for x in roll[TASK_QUAL] if x.get('auc') is not None];qmed=float(statistics.median(qaucs)) if qaucs else None;qmin=min(qaucs) if qaucs else None
    gates={'minimumQualitySamples':an['qualityLabeledSamples']>=500,'continuationValidationAuc':res[TASK_CONT]['enriched']['validation']['auc']>=.60,'continuationTestAuc':res[TASK_CONT]['enriched']['test']['auc']>=.60,'qualityValidationAuc':res[TASK_QUAL]['enriched']['validation']['auc']>=.60,'qualityTestAuc':res[TASK_QUAL]['enriched']['test']['auc']>=.60,'qualityRollingMedianAuc':qmed is not None and qmed>=.60,'qualityRollingMinAuc':qmin is not None and qmin>=.55,'qualityQuartileSeparation':(res[TASK_QUAL]['quartileTest'].get('topMinusBottomRate') or -9)>=.10}
    models={};nfinal={}
    for task in (TASK_CONT,TASK_QUAL):models[task],nfinal[task]=final_model(rows,task)
    decision='KEEP_CONTINUATION_AND_QUALITY_HEADS_AS_SHADOW_CANDIDATES' if all(gates.values()) else 'PARTIAL_KEEP_OR_REJECT_BEFORE_RUNTIME_ADMISSION'
    out={'version':'TARGET_ETH_EXPAND_CONTINUATION_ADMISSION_QUALITY_V1','date':'2026-09-04','researchOnly':True,'actionAuthority':False,'sourceDb':os.path.abspath(a.db),'windows':nwin,'datasetAnatomy':an,'splitCutoffs':{'trainEndExclusiveMs':c1,'validationEndExclusiveMs':c2},'features':{'base':BASE_FEATURES,'continuationContext':CTX_FEATURES,'all':ALL_FEATURES},'fixedSplit':res,'rolling':roll,'rollingQualitySummary':{'aucs':qaucs,'medianAuc':qmed,'minAuc':qmin},'gates':gates,'decision':decision,'stableNextCompositeShadow':{'status':'KEEP_UNCHANGED','source':'TARGET_ETH_POST_SETTLEMENT_HGB_ROLLING_V1_20260904.json','rollingAucs':[0.6506927158682658,0.6421947045991876,0.6621847857189008,0.654648718099486],'actionAuthority':False},'finalShadowTrainRows':nfinal,'boundary':['ResponsibilityTransition correctness frozen PASS','post-crossing state only','future Target chronology/price/role used as offline labels only','pair sum 1.0 structural boundary; no threshold sweep','no winner/PnL feature','no V83 behavior change','no next-composite authority','ETH-only gradients','shadow information only']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'models':models,'features':ALL_FEATURES,'tasks':[TASK_CONT,TASK_QUAL],'actionAuthority':False,'boundary':out['boundary']},a.model_out)
    print(json.dumps({'ok':True,'decision':decision,'anatomy':an,'gates':gates,'fixed':{t:{'val':res[t]['enriched']['validation'],'test':res[t]['enriched']['test'],'lift':res[t]['lift'],'quartile':res[t]['quartileTest']} for t in (TASK_CONT,TASK_QUAL)},'rollingQuality':out['rollingQualitySummary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
