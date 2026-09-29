from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUTDIR=ROOT/'data'/'research'/'r4_v0'/'hourly'
TZ=ZoneInfo('Asia/Taipei'); SEALED='2026-08-16'
VERSION='R4_REALIZED_FILL_PATH_STATE_V1'
FEATURES=['floor_per_gross','coverage','absnet_ratio','pair_edge','weak_realization_15s','surplus_realization_15s','side_realization_gap_15s','weak_nonfill_events_15s','weak_partial_events_15s','one_sided_dwell_s','events_15s','event_index_norm']

def ro(p):
    c=sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def f(x,d=0.):
    try:
        y=float(x); return y if math.isfinite(y) else d
    except: return d

def geom(up,down,cu,cd):
    gross=up+down; paired=min(up,down); cost=cu+cd
    au=cu/up if up>1e-9 else 0.; ad=cd/down if down>1e-9 else 0.
    return {'gross':gross,'floor':paired-cost,'coverage':2*paired/gross if gross>1e-9 else 0.,'absnet':abs(up-down),'edge':1-(au+ad) if up>1e-9 and down>1e-9 else 0.}

def load(n=1000):
    c=ro(DB); meta=[]
    for r in c.execute("select m.market_id,m.window_end_ms from target_markets m join target_market_results x on x.market_id=m.market_id where m.asset='BTC' and m.window_end_ms is not null and x.fill_count>0 order by m.window_end_ms desc limit ?",(n*2,)):
        if datetime.fromtimestamp(int(r['window_end_ms'])/1000,TZ).date().isoformat()==SEALED: continue
        meta.append((int(r['market_id']),int(r['window_end_ms'])))
        if len(meta)>=n: break
    ids=[m for m,_ in meta]; q=','.join('?'*len(ids)); ev=defaultdict(list)
    for r in c.execute(f"select market_id,role,side,last_event_ms,average_price,shares from target_parent_orders where market_id in ({q}) and quote_type='BID'",ids):
        role=str(r['role'] or '').upper(); side=str(r['side'] or '').upper(); px=f(r['average_price'],-1); sh=f(r['shares'])
        if role in {'MAKER','TAKER'} and side in {'UP','DOWN'} and 0<=px<=1 and sh>0:
            ev[int(r['market_id'])].append({'t':int(r['last_event_ms']),'role':role,'side':side,'px':px,'sh':sh})
    c.close(); return sorted(meta,key=lambda z:z[1]),{m:sorted(v,key=lambda z:z['t']) for m,v in ev.items()}

def stress_events(events,variant):
    out=[]; weak_idx=0
    up=down=0.
    for z in events:
        weak='UP' if up<down else 'DOWN' if down<up else None
        intended=float(z['sh']); realized=intended; status='FULL'
        if z['role']=='MAKER' and weak and z['side']==weak:
            weak_idx+=1
            if variant=='WEAK_DROP_ALTERNATE' and weak_idx%2==0: realized=0.; status='NONFILL'
            elif variant=='WEAK_PARTIAL_HALF' and weak_idx%2==0: realized=intended*.5; status='PARTIAL'
            elif variant=='WEAK_MIXED' and weak_idx%3==0: realized=0.; status='NONFILL'
            elif variant=='WEAK_MIXED' and weak_idx%3==2: realized=intended*.5; status='PARTIAL'
        zz=dict(z); zz.update({'intended':intended,'realized':realized,'execStatus':status}); out.append(zz)
        if realized>0:
            if z['side']=='UP': up+=realized
            else: down+=realized
    return out

def build_rows(mid,end_ms,events,variant):
    up=down=cu=cd=0.; hist=deque(); rows=[]; last_both_ms=None
    states=[]
    for i,z in enumerate(events):
        pre=geom(up,down,cu,cd); weak='UP' if up<down else 'DOWN' if down<up else None; surplus='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        now=z['t']; hist.append(z)
        while hist and now-hist[0]['t']>15000: hist.popleft()
        wi=sum(h['intended'] for h in hist if weak and h['side']==weak) or 0.; wr=sum(h['realized'] for h in hist if weak and h['side']==weak) or 0.
        si=sum(h['intended'] for h in hist if surplus and h['side']==surplus) or 0.; sr=sum(h['realized'] for h in hist if surplus and h['side']==surplus) or 0.
        weak_real=wr/wi if wi>1e-9 else 1.; sur_real=sr/si if si>1e-9 else 1.
        if up>0 and down>0: last_both_ms=now
        dwell=(now-last_both_ms)/1000. if last_both_ms is not None else min(15.,max(0.,(now-events[0]['t'])/1000.))
        vals={'floor_per_gross':pre['floor']/pre['gross'] if pre['gross']>1e-9 else 0.,'coverage':pre['coverage'],'absnet_ratio':pre['absnet']/pre['gross'] if pre['gross']>1e-9 else 0.,'pair_edge':pre['edge'],'weak_realization_15s':weak_real,'surplus_realization_15s':sur_real,'side_realization_gap_15s':sur_real-weak_real,'weak_nonfill_events_15s':sum(bool(h['execStatus']=='NONFILL' and weak and h['side']==weak) for h in hist),'weak_partial_events_15s':sum(bool(h['execStatus']=='PARTIAL' and weak and h['side']==weak) for h in hist),'one_sided_dwell_s':dwell,'events_15s':len(hist),'event_index_norm':max(0.,min(1.,1-(end_ms-now)/300000.))}
        if z['realized']>0:
            if z['side']=='UP': up+=z['realized']; cu+=z['realized']*z['px']
            else: down+=z['realized']; cd+=z['realized']*z['px']
        states.append((now,geom(up,down,cu,cd)))
        rows.append({'marketId':mid,'i':i,'t':now,'variant':variant,'vals':vals,'preFloor':pre['floor'],'side':z['side'],'realized':z['realized']})
    for r in rows:
        t=r['t']; fut=[g for tt,g in states if t<tt<=t+15000]
        if not fut: r['label']=0
        else:
            # recovery path invalid = no positive floor recovery and floor deteriorates over next 15s
            maxf=max(g['floor'] for g in fut); minf=min(g['floor'] for g in fut)
            r['label']=int(maxf<=max(0.,r['preFloor']) and minf<r['preFloor']-1e-9)
    return rows

def replay_with_gate(events,end_ms,model=None):
    up=down=cu=cd=0.; hist=deque(); last_both=None; pos_ms=0; last_t=None; peak=0.; flags=0
    for i,z in enumerate(events):
        pre=geom(up,down,cu,cd); weak='UP' if up<down else 'DOWN' if down<up else None; surplus='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None; now=z['t']
        hist.append(z)
        while hist and now-hist[0]['t']>15000: hist.popleft()
        wi=sum(h['intended'] for h in hist if weak and h['side']==weak) or 0.; wr=sum(h['realized'] for h in hist if weak and h['side']==weak) or 0.; si=sum(h['intended'] for h in hist if surplus and h['side']==surplus) or 0.; sr=sum(h['realized'] for h in hist if surplus and h['side']==surplus) or 0.
        wrat=wr/wi if wi>1e-9 else 1.; srat=sr/si if si>1e-9 else 1.
        if up>0 and down>0: last_both=now
        dwell=(now-last_both)/1000. if last_both is not None else min(15.,max(0.,(now-events[0]['t'])/1000.))
        vals={'floor_per_gross':pre['floor']/pre['gross'] if pre['gross']>1e-9 else 0.,'coverage':pre['coverage'],'absnet_ratio':pre['absnet']/pre['gross'] if pre['gross']>1e-9 else 0.,'pair_edge':pre['edge'],'weak_realization_15s':wrat,'surplus_realization_15s':srat,'side_realization_gap_15s':srat-wrat,'weak_nonfill_events_15s':sum(bool(h['execStatus']=='NONFILL' and weak and h['side']==weak) for h in hist),'weak_partial_events_15s':sum(bool(h['execStatus']=='PARTIAL' and weak and h['side']==weak) for h in hist),'one_sided_dwell_s':dwell,'events_15s':len(hist),'event_index_norm':max(0.,min(1.,1-(end_ms-now)/300000.))}
        p=float(model.predict_proba(np.asarray([[vals[k] for k in FEATURES]],float))[0,1]) if model is not None else 0.
        # Default classifier boundary only; suppress only surplus-side realized continuation while path is predicted invalid.
        suppress=bool(model is not None and p>=0.5 and pre['floor']>0 and surplus and z['side']==surplus and z['realized']>0)
        if suppress: flags+=1
        else:
            if z['realized']>0:
                if z['side']=='UP': up+=z['realized']; cu+=z['realized']*z['px']
                else: down+=z['realized']; cd+=z['realized']*z['px']
        if last_t is not None and pre['floor']>0: pos_ms+=max(0,now-last_t)
        last_t=now; peak=max(peak,geom(up,down,cu,cd)['floor'])
    g=geom(up,down,cu,cd); return {**g,'positiveDurationSec':pos_ms/1000.,'peakFloor':peak,'flags':flags}

def med(v):
    a=sorted(v); return a[len(a)//2] if a else None

def main():
    meta,base=load(80); cut=int(len(meta)*.8); train_ids={m for m,_ in meta[:cut]}; test_meta=meta[cut:]
    variants=['WEAK_DROP_ALTERNATE']
    train=[]; test=[]
    for mid,end in meta:
        for v in variants:
            se=stress_events(base.get(mid,[]),v); rs=build_rows(mid,end,se,v)
            (train if mid in train_ids else test).extend(rs)
    X=np.asarray([[r['vals'][k] for k in FEATURES] for r in train],float); y=np.asarray([r['label'] for r in train],int)
    model=HistGradientBoostingClassifier(max_iter=30,max_leaf_nodes=15,l2_regularization=1.0,random_state=7).fit(X,y)
    Xt=np.asarray([[r['vals'][k] for k in FEATURES] for r in test],float); yt=np.asarray([r['label'] for r in test],int); pt=model.predict_proba(Xt)[:,1]
    auc=roc_auc_score(yt,pt) if len(set(yt))>1 else None
    market_rows=[]
    for mid,end in test_meta:
        for v in variants:
            se=stress_events(base.get(mid,[]),v); b=replay_with_gate(se,end,None); g=replay_with_gate(se,end,model)
            if g['flags']:
                market_rows.append({'marketId':mid,'variant':v,'flags':g['flags'],'deltaFinalFloor':g['floor']-b['floor'],'deltaPositiveDurationSec':g['positiveDurationSec']-b['positiveDurationSec'],'deltaCoverage':g['coverage']-b['coverage'],'deltaAbsNet':g['absnet']-b['absnet'],'deltaPeakDrawdown':(g['peakFloor']-g['floor'])-(b['peakFloor']-b['floor'])})
    m={'activeMarketVariants':len(market_rows),'medianDeltaFinalFloor':med([r['deltaFinalFloor'] for r in market_rows]),'meanDeltaFinalFloor':sum(r['deltaFinalFloor'] for r in market_rows)/len(market_rows) if market_rows else None,'improved':sum(r['deltaFinalFloor']>1e-9 for r in market_rows),'worsened':sum(r['deltaFinalFloor']<-1e-9 for r in market_rows),'medianDeltaPositiveDurationSec':med([r['deltaPositiveDurationSec'] for r in market_rows]),'medianDeltaCoverage':med([r['deltaCoverage'] for r in market_rows]),'medianDeltaAbsNet':med([r['deltaAbsNet'] for r in market_rows]),'medianDeltaPeakDrawdown':med([r['deltaPeakDrawdown'] for r in market_rows])}
    keep=bool(auc is not None and auc>=0.70 and len(market_rows)>=20 and (m['medianDeltaFinalFloor'] or 0)>0 and (m['medianDeltaPositiveDurationSec'] or 0)>=0 and (m['medianDeltaCoverage'] or 0)>=-0.05 and (m['medianDeltaAbsNet'] or 0)<=5 and (m['medianDeltaPeakDrawdown'] or 0)<=0)
    rep={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':{'type':'REALIZED_FILL_PATH_STATE_EXECUTION_AWARE_BASE_FORMATION','runtimeInputsStrictPast':True,'classifierThreshold':0.5,'thresholdTuned':False,'action':'when predicted recovery invalid AND pre-floor positive, suppress surplus-side continuation only; weak-side/base-deepening remains allowed'},'training':{'ordinaryTargetMarkets':len(meta),'trainMarkets':len(train_ids),'testMarkets':len(test_meta),'stressVariants':variants,'features':FEATURES,'testAUC':auc,'trainRows':len(train),'testRows':len(test),'sealed20260816':True,'futureLabelOfflineOnly':True},'metrics':m,'keepGate':{'pass':keep,'rule':'AUC>=0.70; >=20 active market-variants; median final floor>0; positive duration non-worse; coverage>=-0.05; abs-net not materially worse; peak-to-final drawdown non-worse'},'rows':market_rows,'guards':{'noNewEchtgeld':True,'frozenEchtgeldOnlyDefinedStressDimensions':True,'noLiveR3Change':True,'no8781Change':True,'noDreamFill':True}}
    stamp=datetime.now(TZ).strftime('%Y%m%d_%H%M'); out=OUTDIR/f'r4_hourly_experiment_{stamp}_realized_fill_path_state_v1.json'; out.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'auc':auc,'metrics':m,'keep':keep}))
if __name__=='__main__': main()
