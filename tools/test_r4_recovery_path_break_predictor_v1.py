from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'hft_forward_paper_v1.db'
OUTDIR=ROOT/'data'/'research'/'r4_v0'/'hourly'
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_RECOVERY_PATH_BREAK_PREDICTOR_V1'
FEATURES=[
 'event_index_norm','floor_per_gross','coverage','absnet_ratio','pair_edge',
 'weak_share_frac_15s','surplus_share_frac_15s','weak_events_15s','surplus_events_15s',
 'maker_frac_15s','taker_frac_15s','age_weak_fill_s','age_surplus_fill_s','fills_15s',
 'weak_avg_px_15s','surplus_avg_px_15s','floor_change_15s_per_gross','absnet_change_15s_per_gross'
]

def ro(p):
 c=sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def f(x,d=0.):
 try:
  y=float(x); return y if math.isfinite(y) else d
 except: return d

def geom(up,down,cu,cd):
 gross=up+down; cost=cu+cd; paired=min(up,down)
 au=cu/up if up>1e-9 else 0.; ad=cd/down if down>1e-9 else 0.
 return {'gross':gross,'floor':paired-cost,'coverage':2*paired/gross if gross>1e-9 else 0.,'absnet':abs(up-down),'edge':1-(au+ad) if up>1e-9 and down>1e-9 else 0.}

def load(strategy='R2',limit=700):
 c=ro(DB)
 runs=[]
 for r in c.execute("select market_id,window_end_ms from hft_forward_runs_v1 where strategy_key=? and status='COMPLETE' and tape_quality_status='COMPLETE_FORWARD_V1' order by window_end_ms",(strategy,)):
  runs.append((int(r['market_id']),int(r['window_end_ms'] or 0)))
 if len(runs)>limit: runs=runs[-limit:]
 ids=[m for m,_ in runs]
 ev=defaultdict(list)
 if ids:
  for i in range(0,len(ids),300):
   part=ids[i:i+300]; q=','.join('?'*len(part))
   for r in c.execute(f"select market_id,fill_seq,channel,side,price,shares,fill_ms from hft_forward_fills_v1 where strategy_key=? and market_id in ({q}) order by market_id,fill_seq",[strategy,*part]):
    side=str(r['side'] or '').upper(); ch=str(r['channel'] or '').upper(); px=f(r['price'],-1); sh=f(r['shares'])
    if side in {'UP','DOWN'} and ch in {'MAKER','TAKER'} and 0<=px<=1 and sh>0:
     ev[int(r['market_id'])].append({'t':int(r['fill_ms']),'ch':ch,'side':side,'px':px,'sh':sh})
 c.close(); return runs,ev

def build_market(mid,end_ms,events):
 up=down=cu=cd=0.; hist=deque(); states=[]; rows=[]; last_weak={'UP':None,'DOWN':None}; last_sur={'UP':None,'DOWN':None}
 for z in events:
  now=z['t']; pre=geom(up,down,cu,cd)
  if pre['gross']<1e-9:
   weak=surplus=None
  elif up<down: weak,surplus='UP','DOWN'
  elif down<up: weak,surplus='DOWN','UP'
  else: weak=surplus=None
  while hist and now-hist[0]['t']>15000: hist.popleft()
  hs=list(hist); tot=sum(h['sh'] for h in hs) or 1.; weak_sh=sum(h['sh'] for h in hs if weak and h['side']==weak); sur_sh=sum(h['sh'] for h in hs if surplus and h['side']==surplus)
  weak_px=sum(h['px']*h['sh'] for h in hs if weak and h['side']==weak)/weak_sh if weak_sh>1e-9 else 0.
  sur_px=sum(h['px']*h['sh'] for h in hs if surplus and h['side']==surplus)/sur_sh if sur_sh>1e-9 else 0.
  old=states[-1][1] if states else pre
  # approximate 15s-old geometry from first state not newer than now-15s
  for tt,gg,*_rest in reversed(states):
   if tt<=now-15000:
    old=gg; break
  vals={
   'event_index_norm':max(0.,min(1.,1-(end_ms-now)/300000.)) if end_ms else 0.,
   'floor_per_gross':pre['floor']/pre['gross'] if pre['gross']>1e-9 else 0.,
   'coverage':pre['coverage'],'absnet_ratio':pre['absnet']/pre['gross'] if pre['gross']>1e-9 else 0.,'pair_edge':pre['edge'],
   'weak_share_frac_15s':weak_sh/tot,'surplus_share_frac_15s':sur_sh/tot,
   'weak_events_15s':sum(1 for h in hs if weak and h['side']==weak),'surplus_events_15s':sum(1 for h in hs if surplus and h['side']==surplus),
   'maker_frac_15s':sum(1 for h in hs if h['ch']=='MAKER')/len(hs) if hs else 0.,'taker_frac_15s':sum(1 for h in hs if h['ch']=='TAKER')/len(hs) if hs else 0.,
   'age_weak_fill_s':min(60.,(now-last_weak.get(weak))/1000.) if weak and last_weak.get(weak) is not None else 60.,
   'age_surplus_fill_s':min(60.,(now-last_sur.get(surplus))/1000.) if surplus and last_sur.get(surplus) is not None else 60.,
   'fills_15s':len(hs),'weak_avg_px_15s':weak_px,'surplus_avg_px_15s':sur_px,
   'floor_change_15s_per_gross':(pre['floor']-old['floor'])/pre['gross'] if pre['gross']>1e-9 else 0.,
   'absnet_change_15s_per_gross':(pre['absnet']-old['absnet'])/pre['gross'] if pre['gross']>1e-9 else 0.,
  }
  if weak and pre['gross']>=20 and pre['absnet']/pre['gross']>=0.05:
   rows.append({'marketId':mid,'t':now,'weak':weak,'pre':pre,'vals':vals})
  # apply current fill after snapshot
  if z['side']=='UP': up+=z['sh']; cu+=z['sh']*z['px']
  else: down+=z['sh']; cd+=z['sh']*z['px']
  post=geom(up,down,cu,cd); states.append((now,post,z['side'],z['sh']))
  last_weak[z['side']]=now; last_sur[z['side']]=now
 # future label: no weak-side fill in 15s AND floor does not improve by >=1 share-equivalent or absnet does not contract >=10%
 for r in rows:
  fut=[s for s in states if r['t']<s[0]<=r['t']+15000]
  weak_fill=sum(s[3] for s in fut if s[2]==r['weak'])
  if not fut:
   r['label']=1
  else:
   max_floor=max(s[1]['floor'] for s in fut); min_abs=min(s[1]['absnet'] for s in fut)
   floor_gain=max_floor-r['pre']['floor']; abs_contract=r['pre']['absnet']-min_abs
   recovered=(weak_fill>0 and (floor_gain>=1.0 or abs_contract>=max(1.0,0.10*r['pre']['absnet'])))
   r['label']=0 if recovered else 1
 return rows

def evaluate(strategy):
 runs,ev=load(strategy,700); cut=int(len(runs)*.8); train_ids={m for m,_ in runs[:cut]}; test_ids={m for m,_ in runs[cut:]}
 rows=[]
 for mid,end in runs: rows.extend(build_market(mid,end,ev.get(mid,[])))
 tr=[r for r in rows if r['marketId'] in train_ids]; te=[r for r in rows if r['marketId'] in test_ids]
 X=np.asarray([[r['vals'][k] for k in FEATURES] for r in tr],float); y=np.asarray([r['label'] for r in tr],int)
 Xt=np.asarray([[r['vals'][k] for k in FEATURES] for r in te],float); yt=np.asarray([r['label'] for r in te],int)
 model=HistGradientBoostingClassifier(max_iter=60,max_leaf_nodes=15,l2_regularization=1.0,random_state=19).fit(X,y)
 p=model.predict_proba(Xt)[:,1]
 auc=roc_auc_score(yt,p) if len(set(yt))>1 else None; ap=average_precision_score(yt,p) if len(set(yt))>1 else None
 # default 0.5 diagnostic only, not tuned
 pred=p>=0.5; tp=int(((pred==1)&(yt==1)).sum()); fp=int(((pred==1)&(yt==0)).sum()); fn=int(((pred==0)&(yt==1)).sum()); tn=int(((pred==0)&(yt==0)).sum())
 market_scores=[]
 by=defaultdict(list)
 for r,pp,yy in zip(te,p,yt): by[r['marketId']].append((float(pp),int(yy)))
 for mid,v in by.items():
  pos=[pp for pp,yy in v if yy]; neg=[pp for pp,yy in v if not yy]
  market_scores.append({'marketId':mid,'rows':len(v),'breakRows':sum(yy for _,yy in v),'meanPBreak':sum(pp for pp,_ in v)/len(v),'meanPOnBreak':sum(pos)/len(pos) if pos else None,'meanPOnRecovery':sum(neg)/len(neg) if neg else None})
 return {'strategy':strategy,'markets':len(runs),'trainMarkets':len(train_ids),'testMarkets':len(test_ids),'trainRows':len(tr),'testRows':len(te),'positiveRateTest':float(yt.mean()) if len(yt) else None,'testAUC':auc,'testAP':ap,'precisionAt05':tp/max(1,tp+fp),'recallAt05':tp/max(1,tp+fn),'specificityAt05':tn/max(1,tn+fp),'marketCoverageTest':len(by),'marketScores':market_scores}

def main():
 r2=evaluate('R2'); cap=evaluate('CAP100')
 keep=bool((r2['testAUC'] or 0)>=0.70 and (cap['testAUC'] or 0)>=0.68 and r2['testRows']>=500 and cap['testRows']>=500)
 rep={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':{'type':'RECOVERY_PATH_BREAK_PREDICTOR_ONLY','purpose':'Predict whether expected weak-side recovery path will fail in the next 15s from strict-past realized fill-path state. No control action in this experiment.','runtimeInputsStrictPast':True,'futureLabelOfflineOnly':True,'thresholdTuned':False},'features':FEATURES,'R2':r2,'CAP100Replication':cap,'keepGate':{'pass':keep,'rule':'R2 chronological AUC>=0.70, independent CAP100 replication AUC>=0.68, each >=500 test rows'},'guards':{'noNewEchtgeld':True,'frozenEchtgeldOnlyMotivatesExecutionMismatch':True,'noLiveR3Change':True,'no8781Change':True,'noDreamFill':True,'noControlActionTested':True}}
 stamp=datetime.now(TZ).strftime('%Y%m%d_%H%M'); out=OUTDIR/f'r4_hourly_experiment_{stamp}_recovery_path_break_predictor_v1.json'; out.write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'keep':keep,'r2':{k:r2[k] for k in ['markets','testRows','positiveRateTest','testAUC','testAP','precisionAt05','recallAt05']},'cap100':{k:cap[k] for k in ['markets','testRows','positiveRateTest','testAUC','testAP','precisionAt05','recallAt05']}}))
if __name__=='__main__': main()
