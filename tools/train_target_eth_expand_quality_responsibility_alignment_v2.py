from __future__ import annotations
import argparse,json,sqlite3,math,statistics,os
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

SEED=20260904;EPS=1e-9
BASE=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_ratio','gross_log','overflow_gap_ratio','price','route_taker','delta_floor_ratio','delta_best_ratio','recent_repair_frac','recent_expand_frac','recent_taker_frac','prev_age_log','event_count_norm']
CTX=['has_prior_same_side_expand','post_dominant_side_up','prior_same_side_expand_age_log','prior_same_side_expand_price','repair_block_qty_gross','repair_block_notional_cost','repair_block_count_norm','repair_block_avg_price','anchor_repair_share_of_block','anchor_repair_price_plus_prior_expand_price']
FEATURES=BASE+CTX
SEM=['ANCHOR_ONLY','BLOCK_VWAP','MATCHED_LIFO','MATCHED_FIFO']

def role(side,up,dn):
    if up+dn<=EPS or abs(up-dn)<=EPS:return 0
    weak='UP' if up<dn else 'DOWN'
    return 1 if side==weak else -1

def metrics(up,dn,cost):
    g=up+dn;p=min(up,dn);gap=abs(up-dn)
    return {'gross':g,'pair':p,'gap':gap,'pc':2*p/g if g>EPS else 1.,'ab':gap/g if g>EPS else 0.,'floor':p-cost,'best':max(up,dn)-cost}

def safe_age(x):return math.log1p(min(max(float(x),0.),120000.))/math.log1p(120000.)

def vwap(tr):
    q=sum(x['q'] for x in tr)
    return (sum(x['q']*x['px'] for x in tr)/q,q) if q>EPS else (None,0.)

def matched_avg(tr,need,lifo):
    rem=max(float(need),0.);cost=qty=0.
    seq=list(reversed(tr)) if lifo else list(tr)
    for x in seq:
        take=min(rem,float(x['q']))
        if take>EPS:cost+=take*float(x['px']);qty+=take;rem-=take
        if rem<=EPS:break
    return (cost/qty if qty>EPS else None,qty)

def model_fit(X,y):
    y=np.asarray(y,int);p=max(float(y.mean()),1e-6);w=np.where(y==1,.5/p,.5/max(1-p,1e-6))
    m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=SEED,early_stopping=False)
    m.fit(X,y,sample_weight=w);return m

def score(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def build(db):
    con=sqlite3.connect(db);con.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in con.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    raw=list(con.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));con.close()
    by=defaultdict(list)
    for r in raw:by[int(r['market_id'])].append(r)
    ends=sorted(set(mend.values()));c1=ends[int(len(ends)*.60)];c2=ends[int(len(ends)*.80)]
    rows=[]
    for mid,evs in by.items():
        up=dn=cost=0.;hist=[];prev_t=None
        for i,r in enumerate(evs):
            side=str(r['side']).upper();route=str(r['role']).upper();t=int(r['first_event_ms']);q=float(r['shares']);px=float(r['average_price']);pre=metrics(up,dn,cost);rel=role(side,up,dn);gap=pre['gap'];paid=min(q,gap) if rel==1 else 0.;cross=(rel==1 and gap>EPS and q>=gap-EPS);overflow=max(0.,q-gap) if cross else 0.
            if side=='UP':up+=q
            else:dn+=q
            cost+=q*px;post=metrics(up,dn,cost)
            hist.append({'t':t,'rel':rel,'route':route,'side':side,'q':q,'px':px,'repairPaid':paid,'notional':q*px})
            if cross:
                recent=[x for x in hist if t-x['t']<=30000];rr=[x for x in recent if x['rel']==1];ee=[x for x in recent if x['rel']==-1];tt=[x for x in recent if x['route']=='TAKER'];rn=max(1,len(recent));gross=max(post['gross'],EPS);den=max(abs(cost),1.);end=mend.get(mid);sl=0. if end is None else max(-30.,min(330.,(end-t)/1000.))/300.;elapsed=1e6 if prev_t is None else t-prev_t
                if up>dn+EPS:dom='UP';weak='DOWN'
                elif dn>up+EPS:dom='DOWN';weak='UP'
                else:dom=side;weak='DOWN' if dom=='UP' else 'UP'
                prior_ex=[x for x in hist[:-1] if x['rel']==-1 and x['side']==dom];pe=prior_ex[-1] if prior_ex else None;pet=pe['t'] if pe else None;pepx=pe['px'] if pe else 0.
                block=[x for x in hist if x['rel']==1 and x['side']==weak and (pet is None or x['t']>pet) and x['t']<=t and x['repairPaid']>EPS]
                bq=sum(x['repairPaid'] for x in block);bn=sum(x['repairPaid']*x['px'] for x in block);bpx=bn/bq if bq>EPS else 0.
                base=[sl,post['pc'],post['ab'],max(-5.,min(5.,post['floor']/den)),max(-5.,min(5.,post['best']/den)),math.log1p(post['gross'])/math.log1p(500),max(0.,min(6.,overflow/max(gap,EPS))),px,float(route=='TAKER'),max(-5.,min(5.,(post['floor']-pre['floor'])/den)),max(-5.,min(5.,(post['best']-pre['best'])/den)),len(rr)/rn,len(ee)/rn,len(tt)/rn,safe_age(elapsed),math.log1p(len(hist))/math.log1p(128)]
                ctx=[float(pe is not None),float(dom=='UP'),safe_age(1e6 if pet is None else t-pet),pepx,bq/gross,bn/den,min(len(block),12)/12.,bpx,paid/max(bq,EPS) if bq>EPS else 0.,px+pepx if pe else px]
                # Scan to first future economic EXPAND; keep only Repair-paid portions that are responsibility-local to the same weak-side block.
                fu=float(up);fd=float(dn);future_rep=[];found=None
                for j in range(i+1,len(evs)):
                    z=evs[j];zt=int(z['first_event_ms'])
                    if zt<=t:continue
                    if zt-t>30000:break
                    zs=str(z['side']).upper();zq=float(z['shares']);zpx=float(z['average_price']);zr=role(zs,fu,fd);zg=abs(fu-fd);zpaid=min(zq,zg) if zr==1 else 0.
                    if zr==-1:
                        found={'t':zt,'side':zs,'qty':zq,'px':zpx,'delay':zt-t};break
                    if zr==1 and zs==weak and zpaid>EPS:future_rep.append({'q':zpaid,'px':zpx,'t':zt})
                    if zs=='UP':fu+=zq
                    else:fd+=zq
                if found:
                    local=[{'q':x['repairPaid'],'px':x['px'],'t':x['t']} for x in block if x['repairPaid']>EPS]+future_rep
                    labels={};pairs={};supports={}
                    if paid>EPS:
                        pairs['ANCHOR_ONLY']=px+found['px'];supports['ANCHOR_ONLY']=min(paid,found['qty']);labels['ANCHOR_ONLY']=int(pairs['ANCHOR_ONLY']<=1.+EPS)
                    av,aq=vwap(local)
                    if av is not None:
                        pairs['BLOCK_VWAP']=av+found['px'];supports['BLOCK_VWAP']=min(aq,found['qty']);labels['BLOCK_VWAP']=int(pairs['BLOCK_VWAP']<=1.+EPS)
                    for name,lifo in [('MATCHED_LIFO',True),('MATCHED_FIFO',False)]:
                        av,aq=matched_avg(local,found['qty'],lifo)
                        if av is not None:
                            pairs[name]=av+found['px'];supports[name]=aq;labels[name]=int(pairs[name]<=1.+EPS)
                    rows.append({'market':mid,'end':end,'t':t,'x':np.asarray(base+ctx,np.float32),'labels':labels,'pairs':pairs,'supports':supports,'future':found,'localRepairCount':len(local),'localRepairQty':sum(x['q'] for x in local)})
            prev_t=t
    for r in rows:r['split']='train' if int(r['end'] or 0)<c1 else 'validation' if int(r['end'] or 0)<c2 else 'test'
    return rows,ends,c1,c2

def subset(rows,sem,split=None):return [r for r in rows if sem in r['labels'] and (split is None or r['split']==split)]
def arr(z,sem):return np.stack([r['x'] for r in z]),np.asarray([r['labels'][sem] for r in z],int)

def evaluate(rows,ends,sem):
    tr=subset(rows,sem,'train');va=subset(rows,sem,'validation');te=subset(rows,sem,'test');X,y=arr(tr,sem);m=model_fit(X,y);out={}
    for name,z in [('validation',va),('test',te)]:
        Xt,yt=arr(z,sem);out[name]=score(yt,m.predict_proba(Xt)[:,1])
    rolls=[]
    for k,(a,b) in enumerate([(.5,.6),(.6,.7),(.7,.8),(.8,1.)],1):
        c1=ends[min(len(ends)-1,int(len(ends)*a))];c2=ends[min(len(ends)-1,int(len(ends)*b)-1)] if b<1 else ends[-1]
        rz=[r for r in rows if sem in r['labels']];rtr=[r for r in rz if int(r['end'] or 0)<c1];rte=[r for r in rz if int(r['end'] or 0)>=c1 and int(r['end'] or 0)<=c2]
        if len(rtr)<100 or len(rte)<50:rolls.append({'fold':k,'trainN':len(rtr),'testN':len(rte),'auc':None});continue
        X,y=arr(rtr,sem);Xt,yt=arr(rte,sem);mm=model_fit(X,y);rolls.append({'fold':k,'trainN':len(rtr),'testN':len(rte),**score(yt,mm.predict_proba(Xt)[:,1])})
    aucs=[x['auc'] for x in rolls if x.get('auc') is not None];out['rolling']=rolls;out['rollingMedianAuc']=float(statistics.median(aucs)) if aucs else None;out['rollingMinAuc']=min(aucs) if aucs else None
    z=subset(rows,sem);out['support']={'n':len(z),'positiveRate':sum(r['labels'][sem] for r in z)/max(1,len(z)),'medianLocalRepairCount':float(statistics.median([r['localRepairCount'] for r in z])) if z else None,'medianLocalRepairQty':float(statistics.median([r['localRepairQty'] for r in z])) if z else None}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows,ends,c1,c2=build(a.db);res={s:evaluate(rows,ends,s) for s in SEM}
    gate={s:{'minimumSamples':res[s]['support']['n']>=500,'testAuc':(res[s]['test']['auc'] or 0)>=.60,'rollingMedian':(res[s]['rollingMedianAuc'] or 0)>=.60,'rollingMin':(res[s]['rollingMinAuc'] or 0)>=.55} for s in SEM}
    candidates=[s for s in SEM if all(gate[s].values())]
    out={'version':'TARGET_ETH_EXPAND_QUALITY_RESPONSIBILITY_ALIGNMENT_V2','date':'2026-09-04','researchOnly':True,'actionAuthority':False,'sourceDb':os.path.abspath(a.db),'rowsWithFutureExpand':len(rows),'splitCutoffs':{'trainEndExclusiveMs':c1,'validationEndExclusiveMs':c2},'semantics':SEM,'results':res,'gates':gate,'researchCandidatesOnly':candidates,'stableNextCompositeShadow':'KEEP_UNCHANGED','boundary':['ResponsibilityTransition frozen PASS','same V1 strict-past anchor features','same HGB hyperparameters','no threshold sweep','no PnL/winner feature','no runtime promotion','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':len(rows),'candidates':candidates,'summary':{s:{'testAuc':res[s]['test']['auc'],'rollingMedian':res[s]['rollingMedianAuc'],'rollingMin':res[s]['rollingMinAuc'],'n':res[s]['support']['n'],'positiveRate':res[s]['support']['positiveRate']} for s in SEM}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
