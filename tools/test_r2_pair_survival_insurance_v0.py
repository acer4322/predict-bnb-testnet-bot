from __future__ import annotations
import json, math, sqlite3, statistics, sys
from pathlib import Path
from bisect import bisect_right
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
from tools.hftbacktest_r2_residual_repeat_repair_escalation_v0 import run_recovery

DB=ROOT/'data/hft_forward_paper_v1.db'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FEATURES=['first_price','first_shares','side_up','seconds_left10','maker_abs_net10','maker_pair_coverage10','maker_fills10s','recovery_bid10','recovery_ask10','recovery_ask_drift10']

def ro():
 c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row; return c

def snap_index(mid):
 ss=load_public_snapshots(mid); ts=[int(x['sampledAtMs']) for x in ss]
 return ss,ts

def snap_at(ss,ts,t):
 i=bisect_right(ts,int(t))-1
 return ss[i] if i>=0 else None

def book_vals(s,side):
 if not s:return None,None
 if side=='UP': return s.get('predictUpBid'),s.get('predictUpAsk')
 return s.get('predictDownBid'),s.get('predictDownAsk')

def state_until(fills,t):
 up=dn=0.0; nf=0
 for f in fills:
  if int(f['fill_ms'])>t: break
  if str(f['channel']).upper()!='MAKER': continue
  if f['side']=='UP': up+=float(f['shares'])
  else: dn+=float(f['shares'])
  nf+=1
 gross=up+dn; cov=(2*min(up,dn)/gross) if gross>1e-9 else 0.0
 return up,dn,cov,nf

def event_rows():
 c=ro()
 runs=[dict(r) for r in c.execute("select market_id,window_end_ms from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by window_end_ms,market_id")]
 rows=[]; market_order=[]
 for rr in runs:
  mid=int(rr['market_id'])
  fs=[dict(x) for x in c.execute("select * from hft_forward_fills_v1 where strategy_key='R2' and market_id=? order by fill_ms,fill_seq",(mid,))]
  maker=[f for f in fs if str(f['channel']).upper()=='MAKER']
  if len(maker)<2: continue
  try:ss,ts=snap_index(mid)
  except Exception: continue
  up=dn=0.0
  for i,f in enumerate(maker):
   pre=abs(up-dn)
   if f['side']=='UP': up+=float(f['shares'])
   else: dn+=float(f['shares'])
   post=abs(up-dn)
   if post<=pre+1e-9: continue
   t0=int(f['fill_ms']); t10=t0+10000; t60=t0+60000
   # unresolved through first 10s = no later maker state drops below post-first level
   u,d=up,dn; resolved10=False
   for g in maker[i+1:]:
    if int(g['fill_ms'])>t10: break
    if g['side']=='UP':u+=float(g['shares'])
    else:d+=float(g['shares'])
    if abs(u-d)<post-1e-9: resolved10=True; break
   if resolved10: continue
   # label completion during remaining 50s
   u,d=up,dn; complete=0
   for g in maker[i+1:]:
    gt=int(g['fill_ms'])
    if gt<=t10:
     if g['side']=='UP':u+=float(g['shares'])
     else:d+=float(g['shares'])
     continue
    if gt>t60: break
    if g['side']=='UP':u+=float(g['shares'])
    else:d+=float(g['shares'])
    if abs(u-d)<post-1e-9: complete=1;break
   recovery='DOWN' if up>dn else 'UP'
   s0=snap_at(ss,ts,t0); s10=snap_at(ss,ts,t10)
   if not s0 or not s10: continue
   b0,a0=book_vals(s0,recovery); b10,a10=book_vals(s10,recovery)
   if any(v is None for v in [a0,b10,a10]): continue
   u10,d10,cov10,nf10=state_until(maker,t10)
   recent=sum(1 for g in maker if t0<=int(g['fill_ms'])<=t10)
   rows.append({'market_id':mid,'window_end_ms':int(rr['window_end_ms']),'t0':t0,'label':complete,
    'first_price':float(f['price']),'first_shares':float(f['shares']),'side_up':1.0 if f['side']=='UP' else 0.0,
    'seconds_left10':float(s10.get('secondsLeft') or 0.0),'maker_abs_net10':abs(u10-d10),'maker_pair_coverage10':cov10,
    'maker_fills10s':float(recent),'recovery_bid10':float(b10),'recovery_ask10':float(a10),'recovery_ask_drift10':float(a10)-float(a0)})
  market_order.append((int(rr['window_end_ms']),mid))
 c.close(); return pd.DataFrame(rows)

def candidate_features(mid,b):
 t=int(b.get('candidateAtMs') or 0); since=int(b.get('candidateAsymmetrySinceMs') or 0)
 if not t or not since:return None
 fl=[x for x in b['fillLog'] if x.get('role')=='MAKER' and int(x['eventMs'])<=t]
 if not fl:return None
 # choose the fill nearest asymmetry start as first leg
 first=min(fl,key=lambda x:abs(int(x['eventMs'])-since))
 recovery=str(b.get('candidateRecoverySide') or '')
 if recovery not in ('UP','DOWN'):return None
 ss,ts=snap_index(mid); s0=snap_at(ss,ts,int(first['eventMs'])); s10=snap_at(ss,ts,t)
 if not s0 or not s10:return None
 _,a0=book_vals(s0,recovery); b10,a10=book_vals(s10,recovery)
 if any(v is None for v in [a0,b10,a10]):return None
 up=dn=0.0
 for x in fl:
  if x['side']=='UP':up+=float(x['shares'])
  else:dn+=float(x['shares'])
 gross=up+dn;cov=2*min(up,dn)/gross if gross>1e-9 else 0.0
 recent=sum(1 for x in fl if int(x['eventMs'])>=t-10000)
 return {'first_price':float(first['price']),'first_shares':float(first['shares']),'side_up':1.0 if first['side']=='UP' else 0.0,
  'seconds_left10':float(s10.get('secondsLeft') or 0.0),'maker_abs_net10':abs(up-dn),'maker_pair_coverage10':cov,'maker_fills10s':float(recent),
  'recovery_bid10':float(b10),'recovery_ask10':float(a10),'recovery_ask_drift10':float(a10)-float(a0)}

def main():
 df=event_rows().sort_values(['window_end_ms','market_id','t0']).reset_index(drop=True)
 mids=df[['window_end_ms','market_id']].drop_duplicates().sort_values(['window_end_ms','market_id']).market_id.tolist()
 cut=max(1,int(len(mids)*0.7)); train_m=set(mids[:cut]); test_m=mids[cut:]
 tr=df[df.market_id.isin(train_m)]; te=df[df.market_id.isin(test_m)]
 Xtr=tr[FEATURES].replace([np.inf,-np.inf],np.nan).fillna(0); Xte=te[FEATURES].replace([np.inf,-np.inf],np.nan).fillna(0)
 model=HistGradientBoostingClassifier(max_iter=200,max_leaf_nodes=12,l2_regularization=3,random_state=7).fit(Xtr,tr.label)
 ptr=model.predict_proba(Xtr)[:,1]; pte=model.predict_proba(Xte)[:,1]
 threshold=float(np.quantile(ptr,0.25))
 metrics={'markets':len(mids),'events':len(df),'trainMarkets':len(train_m),'testMarkets':len(test_m),'thresholdTrainQ25':threshold,
  'testAuc':float(roc_auc_score(te.label,pte)) if len(set(te.label))>1 else None,'testAp':float(average_precision_score(te.label,pte)),'testBrier':float(brier_score_loss(te.label,pte))}
 # Small intervention smoke: last 12 chronological test markets with usable fixed-tape replay.
 smoke=[]
 for mid in test_m[15:]:
  try:b=run_recovery(int(mid),enable_intervention=False,candidate_delay_ms=10000,passive_priority=False)
  except Exception as e:
   smoke.append({'marketId':int(mid),'error':'baseline '+repr(e)});continue
  f=candidate_features(int(mid),b)
  if f is None:
   smoke.append({'marketId':int(mid),'baselinePnl':b.get('realizedPnl'),'eligible':False});continue
  score=float(model.predict_proba(pd.DataFrame([f])[FEATURES].fillna(0))[:,1][0]); low=score<=threshold
  row={'marketId':int(mid),'baselinePnl':b.get('realizedPnl'),'eligible':True,'survivalScore':score,'lowSurvival':bool(low),'features':f,'baselineFinalAbsNet':b.get('combinedFinalAbsNet')}
  if low:
   try:
    ins=run_recovery(int(mid),enable_intervention=True,candidate_delay_ms=10000,passive_priority=False)
    row.update({'insurancePnl':ins.get('realizedPnl'),'deltaPnl':float(ins['realizedPnl'])-float(b['realizedPnl']),
      'insuranceFinalAbsNet':ins.get('combinedFinalAbsNet'),'deltaFinalAbsNet':float(ins['combinedFinalAbsNet'])-float(b['combinedFinalAbsNet']),
      'intervention':ins.get('intervention')})
   except Exception as e: row['insuranceError']=repr(e)
  smoke.append(row)
 acted=[r for r in smoke if r.get('lowSurvival') and r.get('deltaPnl') is not None]
 agg={'smokeMarkets':len(smoke),'eligible':sum(bool(r.get('eligible')) for r in smoke),'lowSurvivalActed':len(acted),
  'baselinePnlActed':sum(float(r['baselinePnl']) for r in acted),'insurancePnlActed':sum(float(r['insurancePnl']) for r in acted),
  'sumDeltaPnl':sum(float(r['deltaPnl']) for r in acted),'improvedMarkets':sum(float(r['deltaPnl'])>0 for r in acted),'worsenedMarkets':sum(float(r['deltaPnl'])<0 for r in acted),
  'sumDeltaFinalAbsNet':sum(float(r['deltaFinalAbsNet']) for r in acted)}
 out={'version':'R2_PAIR_SURVIVAL_INSURANCE_SMOKE_V0','researchOnly':True,'dreamFillAllowed':False,
  'dedup':'Not repairability veto, not generic refresh, not repeated-repair trigger. Reuses existing cancel->ACK->bounded Taker executor only when frozen cycle-level survival score is in training lowest quartile.',
  'model':metrics,'features':FEATURES,'intervention':'At first persistent asymmetry surviving 10s, if cycle survival score <= train Q25: cancel active recovery Maker child if any, await terminal state, then Taker at most one natural 18-share recovery chunk. No winner/PnL runtime input.',
  'aggregate':agg,'rows':smoke}
 path=OUT/'r2_pair_survival_insurance_smoke_v0_part2.json';path.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps({'report':str(path),'model':metrics,'aggregate':agg,'acted':[{k:r.get(k) for k in ['marketId','baselinePnl','insurancePnl','deltaPnl','survivalScore','deltaFinalAbsNet']} for r in acted]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
