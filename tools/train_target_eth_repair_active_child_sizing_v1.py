from __future__ import annotations
import argparse, importlib.util, json, math, sqlite3, statistics
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('haz', HERE/'train_target_eth_repair_taker_escalation_hazard_v1.py')
haz=importlib.util.module_from_spec(spec); spec.loader.exec_module(haz)
core=haz.core; FEATURES=list(haz.FEATURES); EPS=1e-9


def extra_features(inv, pp, cp, f):
    ex=haz.latest_expansion(pp,cp); extra={}
    m3=[e for e in inv.events if e['role']=='MAKER' and cp-3000<int(e['event_ms'])<=cp]
    extra['maker_fills_3s']=float(len(m3)); extra['maker_shares_3s']=float(sum(float(e['shares']) for e in m3))
    for w in (1000,3000,5000,10000):
        rp=haz.rparents(pp,cp,w)
        extra[f'maker_repair_parents_{w//1000}s']=float(len(rp))
        extra[f'maker_repair_shares_{w//1000}s']=float(sum(float(p['shares']) for p in rp))
    if ex is None:
        extra['latest_maker_expansion_age_ms']=math.nan
        extra['latest_maker_expansion_repaid_frac']=math.nan
    else:
        extra['latest_maker_expansion_age_ms']=float(cp-ex['firstEventMs'])
        start=max(float(ex['postMakerAbsNet'])-float(ex['preMakerAbsNet']),EPS)
        cur=float(f['maker_abs_net'])
        extra['latest_maker_expansion_repaid_frac']=float((float(ex['postMakerAbsNet'])-cur)/start)
    return extra


def build(db):
    c=sqlite3.connect(f'file:{Path(db).resolve().as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row
    out=[]; dropped={'staleBook':0,'emptyBook':0,'zeroResidual':0}
    try:
        metas=[dict(r) for r in c.execute('select * from maker_book_inference_markets where window_end_ms is not null order by market_id')]
        for mi,meta in enumerate(metas,1):
            mid=int(meta['market_id']); mend=int(meta['window_end_ms']); ev,pp=haz.parents(c,mid)
            cand=[]
            for p in pp:
                if p['role']!='TAKER' or p.get('effect')!='REPAIR_EFFECT': continue
                pre=float(p.get('preAbsNet') or 0.0)
                if pre<=EPS: dropped['zeroResidual']+=1; continue
                cand.append({'cp':int(p['firstEventMs'])-1,'p':p})
            if not cand: continue
            cand.sort(key=lambda z:z['cp'])
            inv=core.Inventory(); ei=0; ci=0; state={'bids':{},'asks':{}}; last_update=None
            for u in c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)):
                ut=int(u['source_timestamp_ms'])
                while ci<len(cand) and int(cand[ci]['cp'])<ut:
                    z=cand[ci]; cp=int(z['cp']); p=z['p']
                    while ei<len(ev) and int(ev[ei]['event_ms'])<=cp:
                        e=ev[ei]; inv.apply({'event_ms':int(e['event_ms']),'role':str(e['role']),'side':str(e['side']),'price':float(e['price']),'shares':float(e['shares'])}); ei+=1
                    age=cp-last_update if last_update is not None else 10**9
                    if not (0<=age<=2000): dropped['staleBook']+=1; ci+=1; continue
                    f=inv.features(cp); cn=float(f.pop('_combined_net')); dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None; bf=core.outcome_book(state,dom)
                    if bf is None: dropped['emptyBook']+=1; ci+=1; continue
                    ex=extra_features(inv,pp,cp,f)
                    residual=float(p['preAbsNet']); label=min(float(p['shares']),residual)/residual
                    row={'market_id':mid,'market_end_ms':mend,'checkpoint_ms':cp,'book_age_ms':age,'seconds_left':(mend-cp)/1000.0,**f,**bf,**ex,
                         'parentId':str(p['orderHash']),'repairSide':str(p['side']),'preResidualAbsNet':residual,'takerParentShares':float(p['shares']),
                         'repairFractionLabel':float(min(1.0,max(0.0,label))),'overResidual':bool(float(p['shares'])>residual+EPS)}
                    out.append(row); ci+=1
                if int(u['is_checkpoint']):
                    state={'bids':{float(k):float(v) for k,v in (core.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (core.dec(u['native_asks_z']) or {}).items()}}
                else: core.apply_changes(state,core.dec(u['changes_z']) or {})
                last_update=ut
            if mi%50==0: print(json.dumps({'markets':mi,'of':len(metas),'rows':len(out)}),flush=True)
        return out,dropped
    finally: c.close()


def split(rows):
    mids=sorted({int(r['market_id']) for r in rows}); a=max(1,int(len(mids)*.6)); b=max(a+1,int(len(mids)*.8)); tr=set(mids[:a]); va=set(mids[a:b]); te=set(mids[b:])
    parts={k:[r for r in rows if int(r['market_id']) in s] for k,s in [('train',tr),('validation',va),('test',te)]}
    return parts,{'trainMarkets':len(tr),'validationMarkets':len(va),'testMarkets':len(te),'trainMax':max(tr) if tr else None,'validationRange':[min(va),max(va)] if va else None,'testMin':min(te) if te else None}


def matrix(rr):
    X=np.asarray([[np.nan if r.get(k) is None else float(r.get(k)) for k in FEATURES] for r in rr],np.float32)
    y=np.asarray([float(r['repairFractionLabel']) for r in rr],float)
    return X,y


def ranks(x):
    a=np.asarray(x,float); order=np.argsort(a,kind='mergesort'); ranks=np.empty(len(a),float); i=0
    while i<len(a):
        j=i
        while j+1<len(a) and a[order[j+1]]==a[order[i]]: j+=1
        val=(i+j)/2.0+1.0
        for k in range(i,j+1): ranks[order[k]]=val
        i=j+1
    return ranks


def spearman(y,p):
    if len(y)<2:return None
    ry,rp=ranks(y),ranks(p)
    if np.std(ry)<=EPS or np.std(rp)<=EPS:return None
    return float(np.corrcoef(ry,rp)[0,1])


def metrics(y,p):
    y=np.asarray(y,float); p=np.clip(np.asarray(p,float),0.0,1.0); err=np.abs(y-p)
    return {'n':int(len(y)),'mae':float(mean_absolute_error(y,p)) if len(y) else None,'rmse':float(math.sqrt(mean_squared_error(y,p))) if len(y) else None,
            'spearman':spearman(y,p),'meanY':float(np.mean(y)) if len(y) else None,'medianY':float(np.median(y)) if len(y) else None,
            'meanPred':float(np.mean(p)) if len(p) else None,'medianPred':float(np.median(p)) if len(p) else None,'withinAbs015':float(np.mean(err<=.15)) if len(err) else None}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--output',required=True); ap.add_argument('--model-output',required=True); a=ap.parse_args()
    rows,dropped=build(a.db); parts,sp=split(rows); Xtr,ytr=matrix(parts['train']); Xv,yv=matrix(parts['validation']); Xt,yt=matrix(parts['test'])
    baseline=float(np.median(ytr))
    model=HistGradientBoostingRegressor(max_iter=260,learning_rate=.045,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=3.0,random_state=9261).fit(Xtr,ytr)
    ptr=np.clip(model.predict(Xtr),0,1); pv=np.clip(model.predict(Xv),0,1); pt=np.clip(model.predict(Xt),0,1)
    btr=np.full(len(ytr),baseline); bv=np.full(len(yv),baseline); bt=np.full(len(yt),baseline)
    mm={'train':metrics(ytr,ptr),'validation':metrics(yv,pv),'test':metrics(yt,pt)}; bb={'train':metrics(ytr,btr),'validation':metrics(yv,bv),'test':metrics(yt,bt)}
    gate={'validationMaeImproves':mm['validation']['mae']<bb['validation']['mae'],'testMaeImproves':mm['test']['mae']<bb['test']['mae']}
    gate['promote']=bool(gate['validationMaeImproves'] and gate['testMaeImproves'])
    joblib.dump({'version':'TARGET_ETH_REPAIR_ACTIVE_CHILD_SIZING_V1','features':FEATURES,'model':model,'baselineTrainMedian':baseline,'label':'bounded repair component / strict-past residual'},a.model_output)
    ys=[float(r['repairFractionLabel']) for r in rows]
    out={'version':'TARGET_ETH_REPAIR_ACTIVE_CHILD_SIZING_V1','researchOnly':True,'coverage':{'rows':len(rows),'markets':len({r['market_id'] for r in rows}),'dropped':dropped},'split':sp,
         'labelDistribution':{'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'fullResidualRate':sum(y>=1-EPS for y in ys)/len(ys) if ys else None},
         'features':FEATURES,'baselineTrainMedian':baseline,'modelMetrics':mm,'baselineMetrics':bb,'promotionGate':gate,'modelOutput':a.model_output,
         'boundary':['Fresh ETH REPAIR_EFFECT Taker parents only.','Strict-past state at first_event_ms-1.','Label clips shares above residual to 1; ADD excess is excluded.','No winner/PnL/future side/price/shares in features.','No BTC weights or numeric sizing transferred.','No runtime Taker authority.']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
