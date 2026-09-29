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
BTC_FAIR=BASE/'target_inventory_risk_fairvalue_v1.json'
OUT=BASE/'TARGET_BTC_ETH_DIRECTIONAL_THESIS_MEMORY_V2.json'
MODEL_OUT=BASE/'target_btc_eth_directional_thesis_memory_v2_models.joblib'
EPS=1e-9
PARAMS=dict(learning_rate=0.05,max_iter=220,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=3.0,class_weight='balanced',random_state=20260902)
MEM_NAMES=[
 'same_dom_count_15','same_dom_count_30','same_dom_count_60','opp_dom_count_30','opp_dom_count_60',
 'repair_count_15','repair_count_30','repair_count_60','same_dom_qty_30','same_dom_qty_60','opp_dom_qty_60',
 'repair_qty_30','repair_qty_60','same_dom_count_share_60','same_dom_qty_share_60','sec_since_same_dom',
 'sec_since_opp_dom','sec_since_repair','same_dom_streak','direction_age_since_opp_dom','net_dom_qty_60','repair_to_same_dom_qty_60'
]

def events_for(asset,mids):
    mids=sorted(set(int(x) for x in mids)); con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True)
    out=defaultdict(list); state={}; chunk=700
    for a in range(0,len(mids),chunk):
        mm=mids[a:a+chunk]; qs=','.join('?'*len(mm))
        rows=con.execute(f'''select market_id,role,side,first_event_ms,average_price,shares,parent_id
          from target_parent_orders where asset=? and market_id in ({qs}) and first_event_ms is not null
          order by market_id,first_event_ms,parent_id''',[asset,*mm]).fetchall()
        for mid,role,side,t,px,qty,pid in rows:
            mid=int(mid);role=str(role).upper();side=str(side).upper();t=int(t);qty=float(qty or 0.0);px=float(px or 0.0)
            if qty<=EPS:continue
            u,d=state.get(mid,(0.0,0.0));rel=0
            if role=='MAKER' and abs(u-d)>EPS:
                weak='UP' if u<d else 'DOWN'; rel=1 if side==weak else -1
            if role=='MAKER':out[mid].append((t,side,rel,qty,px,pid))
            if side=='UP':u+=qty
            else:d+=qty
            state[mid]=(u,d)
    con.close()
    for mid in out:out[mid].sort(key=lambda x:(x[0],str(x[5])))
    return out

def label(events,t,side):
    times=[e[0] for e in events];i=bisect.bisect_right(times,t);repair=None
    while i<len(events) and events[i][0]<=t+30000:
        e=events[i]
        if e[1]!=side and e[2]==1:repair=e;break
        i+=1
    if repair is None:return 0
    rt=repair[0];j=bisect.bisect_right(times,rt)
    while j<len(events) and events[j][0]<=rt+30000:
        e=events[j]
        if e[1]==side and e[2]==-1:return 1
        j+=1
    return 0

def mem(events,t,side):
    times=[e[0] for e in events];k=bisect.bisect_left(times,t);h=events[:k]
    def w(ms):return [e for e in h if t-e[0]<=ms]
    w15,w30,w60=w(15000),w(30000),w(60000)
    def same_dom(e):return e[2]==-1 and e[1]==side
    def opp_dom(e):return e[2]==-1 and e[1]!=side
    def rep(e):return e[2]==1
    def cnt(xs,f):return sum(1 for e in xs if f(e))
    def qty(xs,f):return sum(e[3] for e in xs if f(e))
    s15,s30,s60=cnt(w15,same_dom),cnt(w30,same_dom),cnt(w60,same_dom)
    o30,o60=cnt(w30,opp_dom),cnt(w60,opp_dom)
    r15,r30,r60=cnt(w15,rep),cnt(w30,rep),cnt(w60,rep)
    sq30,sq60=qty(w30,same_dom),qty(w60,same_dom);oq60=qty(w60,opp_dom);rq30,rq60=qty(w30,rep),qty(w60,rep)
    domc=s60+o60;domq=sq60+oq60
    last_same=next((e for e in reversed(h) if same_dom(e)),None);last_opp=next((e for e in reversed(h) if opp_dom(e)),None);last_rep=next((e for e in reversed(h) if rep(e)),None)
    sec=lambda e: min(120.0,(t-e[0])/1000.0) if e else 120.0
    # Consecutive same-direction dominant objectives ignoring Repair events. An opposite dominant objective ends the streak.
    streak=0
    for e in reversed(h):
        if e[2]!=-1:continue
        if e[1]==side:streak+=1
        else:break
    dir_age=sec(last_opp) if last_opp else 120.0
    return np.asarray([
      s15,s30,s60,o30,o60,r15,r30,r60,math.log1p(sq30),math.log1p(sq60),math.log1p(oq60),math.log1p(rq30),math.log1p(rq60),
      s60/max(domc,1),sq60/max(domq,EPS) if domq>EPS else 0.0,sec(last_same),sec(last_opp),sec(last_rep),streak,dir_age,
      math.copysign(math.log1p(abs(sq60-oq60)),sq60-oq60),rq60/max(sq60,EPS) if sq60>EPS else 0.0
    ],float)

def split(mids):
    ms=sorted(set(int(x) for x in mids));n=len(ms);a=max(1,int(.70*n));b=max(1,int(.15*n));
    if a+b>=n:b=max(1,n-a-1)
    return set(ms[:a]),set(ms[a:a+b]),set(ms[a+b:])

def metr(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float)
    return {'n':len(y),'positive':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])) if len(y) else None}

def eval_model(X,y,mids,cols):
    tr,va,te=split(mids);itr=np.asarray([int(m) in tr for m in mids]);iva=np.asarray([int(m) in va for m in mids]);ite=np.asarray([int(m) in te for m in mids])
    mod=HistGradientBoostingClassifier(**PARAMS);mod.fit(X[itr][:,cols],y[itr])
    return mod,{'markets':{'train':len(tr),'validation':len(va),'test':len(te)},'train':metr(y[itr],mod.predict_proba(X[itr][:,cols])[:,1]),'validation':metr(y[iva],mod.predict_proba(X[iva][:,cols])[:,1]),'test':metr(y[ite],mod.predict_proba(X[ite][:,cols])[:,1])},ite

def desc_memory(M,y,ite):
    out={}
    for j,n in enumerate(MEM_NAMES):
        a=M[ite & (y==1),j];b=M[ite & (y==0),j]
        out[n]={'positiveMedian':float(np.median(a)) if len(a) else None,'negativeMedian':float(np.median(b)) if len(b) else None}
    return out

def eth():
    z=np.load(ETH_NPZ);meta=json.load(open(ETH_META,encoding='utf-8'));X0=z['X'].astype(float);mids0=z['market_id'].astype(int);ends=z['end_ms'].astype(np.int64);rel=z['relation'].astype(int);feat=meta['features']
    q=rel==-1;X0=X0[q];mids=mids0[q];ends=ends[q];t=np.rint(ends-X0[:,feat.index('seconds_left')]*1000).astype(np.int64);sides=np.where(X0[:,feat.index('candidate_side_up')]>=.5,'UP','DOWN')
    ev=events_for('ETH',mids);M=np.vstack([mem(ev.get(int(mid),[]),int(tt),str(ss)) for mid,tt,ss in zip(mids,t,sides)]);y=np.asarray([label(ev.get(int(mid),[]),int(tt),str(ss)) for mid,tt,ss in zip(mids,t,sides)],int)
    X=np.hstack([X0,M]);g={'FULL30':list(range(30)),'THESIS_MEMORY':list(range(30,30+len(MEM_NAMES))),'FULL30_PLUS_MEMORY':list(range(30+len(MEM_NAMES)))};res={};mods={};ite=None
    for n,c in g.items():mods[n],res[n],ite=eval_model(X,y,mids,c)
    return {'coverage':{'rows':len(y),'markets':len(set(mids.tolist()))},'groups':res,'deltas':{'MEMORY_vs_FULL30':res['THESIS_MEMORY']['test']['auc']-res['FULL30']['test']['auc'],'COMBINED_vs_FULL30':res['FULL30_PLUS_MEMORY']['test']['auc']-res['FULL30']['test']['auc'],'COMBINED_vs_MEMORY':res['FULL30_PLUS_MEMORY']['test']['auc']-res['THESIS_MEMORY']['test']['auc']},'memoryAnatomyTest':desc_memory(M,y,ite)},mods

def btc():
    d=json.load(open(BTC_FAIR,encoding='utf-8'));rr=[r for r in d['rows'] if r.get('role')=='MAKER' and r.get('purpose')=='DOMINANT_ADD'];mids=np.asarray([int(r['marketId']) for r in rr]);t=np.asarray([int(r['t']) for r in rr]);sides=np.asarray([str(r['actionSide']).upper() for r in rr]);ev=events_for('BTC',mids)
    fair_names=['absNet','secondsLeft','rv10MeanBps','avgDominantCost','riskPressure','riskSqrt','fairDominantProb','fairEdgePerShare','directionalAlphaValue','directionAligned']
    F=np.asarray([[float(r[k]) if r.get(k) is not None and math.isfinite(float(r[k])) else np.nan for k in fair_names] for r in rr]);M=np.vstack([mem(ev.get(int(mid),[]),int(tt),str(ss)) for mid,tt,ss in zip(mids,t,sides)]);y=np.asarray([label(ev.get(int(mid),[]),int(tt),str(ss)) for mid,tt,ss in zip(mids,t,sides)],int)
    X=np.hstack([F,M]);g={'FAIR_VALUE':list(range(len(fair_names))),'THESIS_MEMORY':list(range(len(fair_names),len(fair_names)+len(MEM_NAMES))),'FAIR_PLUS_MEMORY':list(range(len(fair_names)+len(MEM_NAMES)))};res={};mods={};ite=None
    for n,c in g.items():mods[n],res[n],ite=eval_model(X,y,mids,c)
    return {'coverage':{'rows':len(y),'markets':len(set(mids.tolist()))},'groups':res,'deltas':{'MEMORY_vs_FAIR':res['THESIS_MEMORY']['test']['auc']-res['FAIR_VALUE']['test']['auc'],'COMBINED_vs_FAIR':res['FAIR_PLUS_MEMORY']['test']['auc']-res['FAIR_VALUE']['test']['auc'],'COMBINED_vs_MEMORY':res['FAIR_PLUS_MEMORY']['test']['auc']-res['THESIS_MEMORY']['test']['auc']},'memoryAnatomyTest':desc_memory(M,y,ite)},mods

def main():
    e,em=eth();b,bm=btc();rep={'version':'TARGET_BTC_ETH_DIRECTIONAL_THESIS_MEMORY_V2','researchOnly':True,'actionAuthority':False,'ETH':e,'BTC':b,'memoryFeatures':MEM_NAMES,'guards':{'strictPast':True,'winnerFeature':False,'pnlFeature':False,'futureActionFeature':False,'behaviorChanged':False}}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');joblib.dump({'version':'TARGET_BTC_ETH_DIRECTIONAL_THESIS_MEMORY_V2','ETH':em,'BTC':bm,'memoryFeatures':MEM_NAMES},MODEL_OUT)
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'brief':{'ETH':{'coverage':e['coverage'],'test':{k:v['test'] for k,v in e['groups'].items()},'deltas':e['deltas'],'memory':e['memoryAnatomyTest']},'BTC':{'coverage':b['coverage'],'test':{k:v['test'] for k,v in b['groups'].items()},'deltas':b['deltas'],'memory':b['memoryAnatomyTest']}}},indent=2),flush=True)
if __name__=='__main__':main()
