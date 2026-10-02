from __future__ import annotations
import json, math, sqlite3, sys
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score
from bisect import bisect_right

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
from tools.hftbacktest_r2_residual_repeat_repair_escalation_v0 import run_recovery
from tools.train_sequential_arbitration_option_v1 import CURRENT

D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
DB=ROOT/'data/hft_forward_paper_v1.db'
BUNDLE=joblib.load(D/'execution_robust_mpc_components_v1.joblib')
TARGETS=['deltaTargetErrorArea5s','deltaTargetErrorArea10s','deltaTargetErrorArea20s','deltaCompletionCost5s','deltaCompletionCost10s','deltaCompletionCost20s']
SF=['first_price','first_shares','side_up','seconds_left10','maker_abs_net10','maker_pair_coverage10','maker_fills10s','recovery_bid10','recovery_ask10','recovery_ask_drift10']
EPS=1e-9

def ro():
 c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row; return c

def snap_index(mid):
 ss=load_public_snapshots(mid); return ss,[int(x['sampledAtMs']) for x in ss]

def snap_at(ss,ts,t):
 i=bisect_right(ts,int(t))-1; return ss[i] if i>=0 else None

def bv(s,side):
 if not s:return None,None
 return (s.get('predictUpBid'),s.get('predictUpAsk')) if side=='UP' else (s.get('predictDownBid'),s.get('predictDownAsk'))

def survival_data():
 c=ro(); rows=[]
 runs=[dict(r) for r in c.execute("select market_id,window_end_ms from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by window_end_ms,market_id")]
 for rr in runs:
  mid=int(rr['market_id']); fs=[dict(x) for x in c.execute("select * from hft_forward_fills_v1 where strategy_key='R2' and market_id=? order by fill_ms,fill_seq",(mid,))]
  maker=[f for f in fs if str(f['channel']).upper()=='MAKER']
  if len(maker)<2: continue
  try:ss,ts=snap_index(mid)
  except: continue
  up=dn=0.0
  for i,f in enumerate(maker):
   pre=abs(up-dn); up += float(f['shares']) if f['side']=='UP' else 0; dn += float(f['shares']) if f['side']=='DOWN' else 0; post=abs(up-dn)
   if post<=pre+EPS: continue
   t0=int(f['fill_ms']); t10=t0+10000; t60=t0+60000; u,d=up,dn; resolved=False
   for g in maker[i+1:]:
    if int(g['fill_ms'])>t10: break
    if g['side']=='UP':u+=float(g['shares'])
    else:d+=float(g['shares'])
    if abs(u-d)<post-EPS: resolved=True;break
   if resolved: continue
   complete=0
   for g in maker[i+1:]:
    gt=int(g['fill_ms'])
    if gt<=t10: continue
    if gt>t60:break
    if g['side']=='UP':u+=float(g['shares'])
    else:d+=float(g['shares'])
    if abs(u-d)<post-EPS: complete=1;break
   recovery='DOWN' if up>dn else 'UP'; s0=snap_at(ss,ts,t0); s10=snap_at(ss,ts,t10)
   if not s0 or not s10: continue
   _,a0=bv(s0,recovery); b10,a10=bv(s10,recovery)
   if any(v is None for v in [a0,b10,a10]): continue
   uu=dd=0.0; recent=0
   for g in maker:
    if int(g['fill_ms'])<=t10:
     if g['side']=='UP':uu+=float(g['shares'])
     else:dd+=float(g['shares'])
    if t0<=int(g['fill_ms'])<=t10:recent+=1
   gross=uu+dd; cov=2*min(uu,dd)/gross if gross>EPS else 0.0
   rows.append({'market_id':mid,'window_end_ms':int(rr['window_end_ms']),'t0':t0,'label':complete,'first_price':float(f['price']),'first_shares':float(f['shares']),'side_up':float(f['side']=='UP'),'seconds_left10':float(s10.get('secondsLeft') or 0),'maker_abs_net10':abs(uu-dd),'maker_pair_coverage10':cov,'maker_fills10s':float(recent),'recovery_bid10':float(b10),'recovery_ask10':float(a10),'recovery_ask_drift10':float(a10)-float(a0)})
 c.close(); return pd.DataFrame(rows)

def train_survival():
 df=survival_data().sort_values(['window_end_ms','market_id','t0']).reset_index(drop=True)
 mids=df[['window_end_ms','market_id']].drop_duplicates().sort_values(['window_end_ms','market_id']).market_id.tolist(); cut=max(1,int(len(mids)*.7)); tm=set(mids[:cut])
 tr=df[df.market_id.isin(tm)]; te=df[~df.market_id.isin(tm)]
 m=HistGradientBoostingClassifier(max_iter=200,max_leaf_nodes=12,l2_regularization=3,random_state=7).fit(tr[SF].fillna(0),tr.label)
 p=m.predict_proba(te[SF].fillna(0))[:,1]
 return m,mids[cut:],{'events':len(df),'markets':len(mids),'testAuc':float(roc_auc_score(te.label,p)) if len(set(te.label))>1 else None,'testAp':float(average_precision_score(te.label,p))}

def candidate_survival_features(mid,b):
 t=int(b.get('candidateAtMs') or 0); since=int(b.get('candidateAsymmetrySinceMs') or 0); rec=str(b.get('candidateRecoverySide') or '')
 if not t or not since or rec not in ('UP','DOWN'): return None
 fl=[x for x in b.get('fillLog',[]) if x.get('role')=='MAKER' and int(x['eventMs'])<=t]
 if not fl:return None
 first=min(fl,key=lambda x:abs(int(x['eventMs'])-since)); ss,ts=snap_index(mid); s0=snap_at(ss,ts,int(first['eventMs'])); s10=snap_at(ss,ts,t)
 if not s0 or not s10:return None
 _,a0=bv(s0,rec); b10,a10=bv(s10,rec)
 if any(v is None for v in [a0,b10,a10]):return None
 up=sum(float(x['shares']) for x in fl if x['side']=='UP'); dn=sum(float(x['shares']) for x in fl if x['side']=='DOWN'); gross=up+dn
 return {'first_price':float(first['price']),'first_shares':float(first['shares']),'side_up':float(first['side']=='UP'),'seconds_left10':float(s10.get('secondsLeft') or 0),'maker_abs_net10':abs(up-dn),'maker_pair_coverage10':2*min(up,dn)/gross if gross>EPS else 0.0,'maker_fills10s':float(sum(int(x['eventMs'])>=t-10000 for x in fl)),'recovery_bid10':float(b10),'recovery_ask10':float(a10),'recovery_ask_drift10':float(a10)-float(a0)}

def mpc_action(feat, votes=5, margin=.5):
 row=[]
 for k in CURRENT:
  v=feat.get(k)
  try: row.append(float(v) if v is not None and math.isfinite(float(v)) else np.nan)
  except: row.append(np.nan)
 xx=np.asarray([row],float); rep=keep=0; pred={}
 for k in TARGETS:
  y=float(BUNDLE['models'][k].predict(xx)[0]); sig=float(BUNDLE['trainResidualStd'][k]); mm=margin*sig; pred[k]=y
  if y < -mm: rep+=1
  elif y > mm: keep+=1
 a='REPLACE' if rep>=votes and keep==0 else 'KEEP' if keep>=votes and rep==0 else 'WAIT'
 return a,rep,keep,pred

def main():
 smodel,test_m,sm= train_survival(); rows=[]
 # chronological unseen markets, split into two batches externally with env indexes if needed
 import os
 lo=int(os.environ.get('BATCH_LO','0')); hi=int(os.environ.get('BATCH_HI',str(len(test_m))))
 for mid in test_m[lo:hi]:
  try:
   b=run_recovery(int(mid),enable_intervention=False,candidate_delay_ms=10000,passive_priority=False)
   r=run_recovery(int(mid),enable_intervention=True,candidate_delay_ms=10000,passive_priority=False)
  except Exception as e:
   rows.append({'marketId':int(mid),'error':repr(e)}); continue
  iv=r.get('intervention'); sf=candidate_survival_features(int(mid),b)
  if not iv or sf is None:
   rows.append({'marketId':int(mid),'baselinePnl':b.get('realizedPnl'),'eligible':False});continue
  surv=float(smodel.predict_proba(pd.DataFrame([sf])[SF].fillna(0))[:,1][0]); feat=dict(iv.get('features') or {})
  feat['asymmetryAgeMs']=float((b.get('candidateAtMs') or 0)-(b.get('candidateAsymmetrySinceMs') or 0)); feat['observationDelayMs']=10000.0; feat['hasPriorObservation']=0.0; feat['elapsedSincePriorMs']=np.nan
  st=str(feat.get('workingRecoveryStatus') or ''); feat['recoveryStatusNew']=float(st=='NEW'); feat['recoveryStatusPartial']=float(st=='PARTIALLY_FILLED')
  act,rv,kv,preds=mpc_action(feat,5,.5); edge=feat.get('lockedPairEdgePerShare'); edge=float(edge) if edge is not None and math.isfinite(float(edge)) else None
  # Fixed prior-research authority: CONSENSUS_5_M05 + observable edge >= -0.02. Survival only modifies uncertainty: very low survival permits replace; high survival requires stronger 6-vote consensus.
  act6,rv6,kv6,_=mpc_action(feat,6,.5)
  authority = (act=='REPLACE' and edge is not None and edge>=-0.02 and (surv<=0.5 or act6=='REPLACE'))
  bp=float(b['realizedPnl']); rp=float(r['realizedPnl']); chosen=rp if authority else bp
  rows.append({'marketId':int(mid),'eligible':True,'baselinePnl':bp,'replacePnl':rp,'deltaIfReplace':rp-bp,'survivalScore':surv,'mpcAction5':act,'replaceVotes5':rv,'keepVotes5':kv,'mpcAction6':act6,'lockedPairEdgePerShare':edge,'authorityReplace':bool(authority),'chosenPnl':chosen,'baselineAbsNet':b.get('combinedFinalAbsNet'),'replaceAbsNet':r.get('combinedFinalAbsNet')})
 elig=[x for x in rows if x.get('eligible')]; chosen=[x for x in elig if x.get('authorityReplace')]
 agg={'markets':len(rows),'eligible':len(elig),'replaceChosen':len(chosen),'baselinePnlAll':sum(float(x['baselinePnl']) for x in elig),'policyPnlAll':sum(float(x['chosenPnl']) for x in elig),'deltaPnlAll':sum(float(x['chosenPnl'])-float(x['baselinePnl']) for x in elig),'chosenReplaceWins':sum(float(x['deltaIfReplace'])>0 for x in chosen),'chosenReplaceLosses':sum(float(x['deltaIfReplace'])<0 for x in chosen)}
 out={'version':'R2_CLOSED_LOOP_MPC_AUTHORITY_V0','researchOnly':True,'dreamFillAllowed':False,'policy':'Prior research fixed CONSENSUS_5_M05 + current lockedPairEdgePerShare >= -0.02. Cycle survival is uncertainty modifier only: if survival<=0.5 use 5-vote replace; otherwise require 6-vote replace. No PnL/winner runtime input; actual HftBacktest fills and full terminal PnL.','survivalModel':sm,'batch':[lo,hi],'aggregate':agg,'rows':rows}
 path=D/f'r2_closed_loop_mpc_authority_v0_{lo}_{hi}.json'; path.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'report':str(path),'aggregate':agg,'chosen':chosen},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
