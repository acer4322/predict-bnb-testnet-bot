from __future__ import annotations
import bisect, json, math, sqlite3
from collections import defaultdict
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
DB=BASE/'target_eth_btc_strategy_compare_snapshot_v1.db'
ETH_NPZ=BASE/'eth_target_favorable_pair_safe_surplus_v1_full.npz'
ETH_META=BASE/'eth_target_favorable_pair_safe_surplus_v1_full.meta.json'
ETH_MODEL=BASE/'eth_target_economic_lane_value_v2_model.joblib'
BTC_FAIR=BASE/'target_inventory_risk_fairvalue_v1.json'
OUT=BASE/'TARGET_BTC_ETH_DIRECTIONAL_THESIS_AUTHORITY_V1.json'
MODEL_OUT=BASE/'target_btc_eth_directional_thesis_authority_v1_models.joblib'
EPS=1e-9

PARAMS=dict(learning_rate=0.05,max_iter=220,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=3.0,class_weight='balanced',random_state=20260902)


def reconstruct_maker_events(asset:str, market_ids:set[int]):
    """Return per-market Target Maker events with strict-past weak/dominant relation."""
    mids=sorted(market_ids)
    con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True)
    out=defaultdict(list)
    chunk=700
    for j in range(0,len(mids),chunk):
        mm=mids[j:j+chunk]; qs=','.join('?'*len(mm))
        rows=con.execute(f'''select market_id,role,side,first_event_ms,average_price,shares,parent_id
          from target_parent_orders where asset=? and market_id in ({qs}) and first_event_ms is not null
          order by market_id,first_event_ms,parent_id''',[asset,*mm]).fetchall()
        state={}
        for mid,role,side,t,px,qty,pid in rows:
            mid=int(mid); side=str(side).upper(); role=str(role).upper(); t=int(t); qty=float(qty or 0.0)
            if qty<=EPS: continue
            u,d=state.get(mid,(0.0,0.0))
            rel=0
            if role=='MAKER' and abs(u-d)>EPS:
                weak='UP' if u<d else 'DOWN'
                rel=1 if side==weak else -1
            if role=='MAKER': out[mid].append((t,side,rel,pid))
            if side=='UP': u+=qty
            else: d+=qty
            state[mid]=(u,d)
    con.close()
    for mid in out: out[mid].sort(key=lambda z:(z[0],str(z[3])))
    return out


def label_one(events, t:int, side:str):
    """Direction -> future opposite weak-side Maker Repair <=30s -> same-side dominant re-expand <=30s after Repair."""
    if not events: return 0,0,0,None,None
    times=[e[0] for e in events]
    i=bisect.bisect_right(times,t)
    repair=None
    while i<len(events) and events[i][0]<=t+30000:
        et,es,er,_=events[i]
        if es!=side and er==1:
            repair=events[i]; break
        i+=1
    if repair is None: return 0,0,0,None,None
    rt=repair[0]
    j=bisect.bisect_right(times,rt)
    re=None
    while j<len(events) and events[j][0]<=rt+30000:
        et,es,er,_=events[j]
        if es==side and er==-1:
            re=events[j]; break
        j+=1
    return 1,1 if re else 0,1 if re else 0,rt,(re[0] if re else None)


def split_markets(mids):
    ms=sorted(set(int(x) for x in mids)); n=len(ms)
    ntr=max(1,int(n*.70)); nv=max(1,int(n*.15));
    if ntr+nv>=n: nv=max(1,n-ntr-1)
    return set(ms[:ntr]),set(ms[ntr:ntr+nv]),set(ms[ntr+nv:])


def metrics(y,p):
    y=np.asarray(y,dtype=int); p=np.asarray(p,dtype=float)
    if len(y)==0:return {'n':0}
    return {
      'n':int(len(y)),'positive':int(y.sum()),'positiveRate':float(y.mean()),
      'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,
      'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,
      'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))
    }


def fit_eval(X,y,mids,cols):
    tr,va,te=split_markets(mids)
    ixtr=np.array([int(m) in tr for m in mids]); ixva=np.array([int(m) in va for m in mids]); ixte=np.array([int(m) in te for m in mids])
    model=HistGradientBoostingClassifier(**PARAMS)
    model.fit(X[ixtr][:,cols],y[ixtr])
    return model,{
      'markets':{'train':len(tr),'validation':len(va),'test':len(te)},
      'train':metrics(y[ixtr],model.predict_proba(X[ixtr][:,cols])[:,1]),
      'validation':metrics(y[ixva],model.predict_proba(X[ixva][:,cols])[:,1]),
      'test':metrics(y[ixte],model.predict_proba(X[ixte][:,cols])[:,1])
    },(ixtr,ixva,ixte)


def eth_task():
    z=np.load(ETH_NPZ,allow_pickle=False); meta=json.load(open(ETH_META,encoding='utf-8'))
    X=z['X'].astype(float); mids=z['market_id'].astype(int); ends=z['end_ms'].astype(np.int64); rel=z['relation'].astype(int)
    feat=meta['features']; sec_ix=feat.index('seconds_left'); side_ix=feat.index('candidate_side_up')
    elig=rel==-1
    X=X[elig]; mids=mids[elig]; ends=ends[elig]
    t=np.rint(ends-X[:,sec_ix]*1000.0).astype(np.int64)
    sides=np.where(X[:,side_ix]>=.5,'UP','DOWN')
    ev=reconstruct_maker_events('ETH',set(mids.tolist()))
    yr=[]; yre=[]; ys=[]; repair_t=[]; re_t=[]
    for mid,tt,ss in zip(mids,t,sides):
        a,b,c,rt,et=label_one(ev.get(int(mid),[]),int(tt),str(ss)); yr.append(a);yre.append(b);ys.append(c);repair_t.append(rt);re_t.append(et)
    yr=np.asarray(yr,int);yre=np.asarray(yre,int);ys=np.asarray(ys,int)
    groups={'BALANCE6':list(range(6)),'ECON26':list(range(26)),'FULL30':list(range(30))}
    models={}; scores={}; split_ix=None
    for name,cols in groups.items():
        m,s,sp=fit_eval(X,ys,mids,cols);models[name]=m;scores[name]=s;split_ix=sp
    # Existing frozen safe-expand teacher score, only where its runtime semantics are valid: pre-action floor>=0.
    bundle=joblib.load(ETH_MODEL); sm=bundle['models']['safe_expand_preserves_floor_30s']['model'];
    floor_ix=feat.index('floor_ratio'); valid=(X[:,floor_ix]>=-1e-9)
    _,_,ixte=split_ix; mask=valid & ixte
    safe_score=sm.predict_proba(X[mask])[:,1] if mask.any() else np.array([])
    existing=metrics(ys[mask],safe_score) if mask.any() else {'n':0}
    # Test-set descriptive label anatomy under FULL split.
    test_anatomy={
      'repairWithin30s':{'n':int(ixte.sum()),'rate':float(yr[ixte].mean()) if ixte.any() else None},
      'sameDirReexpandAfterRepairRateConditional':float(yre[ixte & (yr==1)].mean()) if np.any(ixte & (yr==1)) else None,
      'sandwichRate':float(ys[ixte].mean()) if ixte.any() else None,
      'medianDirectionToRepairMs':float(np.median([rt-tt for rt,tt,k in zip(repair_t,t,ixte) if k and rt is not None])) if any(k and rt is not None for rt,k in zip(repair_t,ixte)) else None,
      'medianRepairToReexpandMs':float(np.median([et-rt for et,rt,k in zip(re_t,repair_t,ixte) if k and et is not None and rt is not None])) if any(k and et is not None and rt is not None for et,rt,k in zip(re_t,repair_t,ixte)) else None
    }
    return {'coverage':{'eligibleRows':int(len(X)),'markets':len(set(mids.tolist()))},'labelAnatomyTest':test_anatomy,'featureGroups':scores,'existingSafeExpandTeacherOnPersistenceTestFloorNonnegative':existing,
            'testAucDeltas':{'ECON26_vs_BALANCE6':(scores['ECON26']['test']['auc']-scores['BALANCE6']['test']['auc']) if scores['ECON26']['test']['auc'] is not None and scores['BALANCE6']['test']['auc'] is not None else None,
                             'FULL30_vs_BALANCE6':(scores['FULL30']['test']['auc']-scores['BALANCE6']['test']['auc']) if scores['FULL30']['test']['auc'] is not None and scores['BALANCE6']['test']['auc'] is not None else None,
                             'FULL30_vs_ECON26':(scores['FULL30']['test']['auc']-scores['ECON26']['test']['auc']) if scores['FULL30']['test']['auc'] is not None and scores['ECON26']['test']['auc'] is not None else None}},models


def btc_task():
    d=json.load(open(BTC_FAIR,encoding='utf-8')); rows=[r for r in d['rows'] if r.get('role')=='MAKER' and r.get('purpose')=='DOMINANT_ADD']
    mids=np.asarray([int(r['marketId']) for r in rows],int); ts=np.asarray([int(r['t']) for r in rows],np.int64); sides=np.asarray([str(r['actionSide']).upper() for r in rows])
    ev=reconstruct_maker_events('BTC',set(mids.tolist()))
    yr=[];yre=[];ys=[]
    for mid,tt,ss in zip(mids,ts,sides):
        a,b,c,_,_=label_one(ev.get(int(mid),[]),int(tt),str(ss));yr.append(a);yre.append(b);ys.append(c)
    yr=np.asarray(yr,int); yre=np.asarray(yre,int);ys=np.asarray(ys,int)
    base=['absNet','secondsLeft','rv10MeanBps','avgDominantCost','riskPressure','riskSqrt']
    fair=base+['fairDominantProb','fairEdgePerShare','directionalAlphaValue','directionAligned']
    names=fair; X=np.asarray([[float(r.get(k)) if r.get(k) is not None and math.isfinite(float(r.get(k))) else np.nan for k in names] for r in rows],float)
    groups={'RISK_BASE':list(range(len(base))),'FAIR_VALUE':list(range(len(fair)))}
    models={};scores={};split_ix=None
    for name,cols in groups.items():
        m,s,sp=fit_eval(X,ys,mids,cols);models[name]=m;scores[name]=s;split_ix=sp
    _,_,ixte=split_ix
    anatomy={'repairWithin30sRate':float(yr[ixte].mean()),'sameDirReexpandAfterRepairRateConditional':float(yre[ixte & (yr==1)].mean()) if np.any(ixte&(yr==1)) else None,'sandwichRate':float(ys[ixte].mean())}
    return {'coverage':{'eligibleRows':len(rows),'markets':len(set(mids.tolist()))},'labelAnatomyTest':anatomy,'featureGroups':scores,'testAucDeltaFAIR_vs_RISK':scores['FAIR_VALUE']['test']['auc']-scores['RISK_BASE']['test']['auc'] if scores['FAIR_VALUE']['test']['auc'] is not None and scores['RISK_BASE']['test']['auc'] is not None else None},models


def main():
    eth,em=eth_task();btc,bm=btc_task()
    rep={'version':'TARGET_BTC_ETH_DIRECTIONAL_THESIS_AUTHORITY_V1','researchOnly':True,'actionAuthority':False,'preRegistration':'TARGET_BTC_ETH_DIRECTIONAL_THESIS_AUTHORITY_V1_PREREGISTERED.json','ETH':eth,'BTC':btc,
         'guards':{'winnerFeature':False,'pnlFeature':False,'futureActionFeature':False,'thresholdSweep':False,'behaviorChanged':False}}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    joblib.dump({'version':'TARGET_BTC_ETH_DIRECTIONAL_THESIS_AUTHORITY_V1','researchOnly':True,'actionAuthority':False,'ETH':em,'BTC':bm},MODEL_OUT)
    brief={'ETH':{'coverage':eth['coverage'],'label':eth['labelAnatomyTest'],'test':{k:v['test'] for k,v in eth['featureGroups'].items()},'deltas':eth['testAucDeltas'],'existingSafeExpand':eth['existingSafeExpandTeacherOnPersistenceTestFloorNonnegative']},
           'BTC':{'coverage':btc['coverage'],'label':btc['labelAnatomyTest'],'test':{k:v['test'] for k,v in btc['featureGroups'].items()},'delta':btc['testAucDeltaFAIR_vs_RISK']}}
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'brief':brief},indent=2),flush=True)

if __name__=='__main__': main()
