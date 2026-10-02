from __future__ import annotations
import json,sqlite3,bisect,math
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,average_precision_score

ROOT=Path(__file__).resolve().parents[1]
TDB=ROOT/'data'/'target_wallet_official_v1.db'; PDB=ROOT/'data'/'public_source_snapshot_archive_v2.db'
OUT=ROOT/'data'/'research'/'r4_v0'; OUT.mkdir(parents=True,exist_ok=True)
TZ=ZoneInfo('Asia/Taipei'); SEALED='2026-08-16'; VERSION='R4_DELIBERATE_EXPANSION_SIDE_PREACTION_V1'
MAX_MARKETS=600
PUBLIC=['predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps','spotMinusChainlinkBps','directionScore','spotReturn1sBps','spotReturn3sBps','spotReturn5sBps','futuresReturn1sBps','futuresReturn3sBps','futuresReturn5sBps','perpSpotBasisBps','spotQueueImbalance','futuresQueueImbalance','futuresTakerImbalance1s','secondsLeft']
STATE=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','signed_inventory','signed_payoff_gap','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','maker_events_15s','taker_events_15s','up_events_15s','down_events_15s','up_shares_15s','down_shares_15s','event_index_norm']
FEATURES=STATE+PUBLIC

def val(x):
 try:
  y=float(x); return y if math.isfinite(y) else 0.0
 except:return 0.0

def fee(sh,px,role): return sh*px*.02 if role=='TAKER' else 0.0

def load_meta():
 tc=sqlite3.connect(TDB); pc=sqlite3.connect(PDB)
 pub=set(r[0] for r in pc.execute('select distinct market_id from public_source_snapshots_v2')); pc.close()
 rows=[]
 for mid,wend in tc.execute("select distinct m.market_id,m.window_end_ms from target_markets m join target_parent_orders p on p.market_id=m.market_id where p.asset='BTC' and m.window_end_ms is not null order by m.window_end_ms"):
  if mid not in pub: continue
  dt=datetime.fromtimestamp(int(wend)/1000,TZ).date().isoformat()
  if dt==SEALED: continue
  rows.append((int(mid),int(wend)))
 tc.close(); return rows

def load_public(ids):
 c=sqlite3.connect(PDB); c.row_factory=sqlite3.Row; d={}; q=','.join('?'*len(ids))
 for r in c.execute(f'select market_id,sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id in ({q}) order by market_id,sampled_at_ms',ids):
  mid=int(r['market_id']); d.setdefault(mid,[[],[]]);d[mid][0].append(int(r['sampled_at_ms']));d[mid][1].append(json.loads(r['snapshot_json']))
 c.close();return d

def pub_at(pub,mid,t):
 z=pub.get(mid)
 if not z:return None
 i=bisect.bisect_right(z[0],t)-1
 if i<0 or t-z[0][i]>3000:return None
 return z[1][i]

def build_market(c,pub,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? and quote_type='BID' order by first_event_ms,parent_id",(mid,)).fetchall()
 if len(rr)<4:return []
 # Build pre-action checkpoints and future post-event states.
 up=down=cost=fees=0.;hist=deque();prev=None;pre=[];post=[]
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role).upper();side=str(side).upper();t=int(t);px=float(px);sh=float(sh)
  gross=up+down;base=min(up,down);pu=up-cost-fees;pd=down-cost-fees;fl=min(pu,pd);ups=max(pu,pd);ss=abs(up-down)
  r5=[x for x in hist if t-x[0]<=5000];r15=list(hist);p=pub_at(pub,mid,t)
  last=hist[-1] if hist else None
  feat={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'signed_inventory':up-down,'signed_payoff_gap':pu-pd,'last_price':float(last[4]) if last else 0.,'last_shares':float(last[3]) if last else 0.,'last_role_taker':1. if last and last[1]=='TAKER' else 0.,'age_since_last_ms':float(t-last[0]) if last else 0.,'events_5s':float(len(r5)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'up_events_15s':float(sum(x[2]=='UP' for x in r15)),'down_events_15s':float(sum(x[2]=='DOWN' for x in r15)),'up_shares_15s':float(sum(x[3] for x in r15 if x[2]=='UP')),'down_shares_15s':float(sum(x[3] for x in r15 if x[2]=='DOWN')),'event_index_norm':i/max(1,len(rr)-1)}
  for k in PUBLIC: feat[k]=val(p.get(k)) if p else 0.0
  pre.append({'t':t,'up':up,'down':down,'floor':fl,'abs':ss,'feat':feat,'haspub':p is not None})
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh;fees+=fee(sh,px,role);pu2=up-cost-fees;pd2=down-cost-fees
  post.append({'t':t,'up':up,'down':down,'floor':min(pu2,pd2),'abs':abs(up-down)})
  hist.append((t,role,side,sh,px));
  while hist and t-hist[0][0]>15000:hist.popleft()
  prev=t
 out=[]
 for i,z in enumerate(pre):
  if z['floor']<0 or z['abs']<5 or not z['haspub']:continue
  cand=[]
  for j in range(i,len(post)):
   if post[j]['t']-z['t']>5000:break
   if post[j]['floor']<0:continue
   inc=post[j]['abs']-z['abs']
   if inc>=max(5.,.1*z['abs']):cand.append((inc,post[j]))
  if not cand:continue
  _,best=max(cand,key=lambda q:q[0]);
  if abs(best['up']-best['down'])<1e-9:continue
  out.append((mid,[float(z['feat'][k]) for k in FEATURES],1 if best['up']>best['down'] else 0,z['t']))
 return out

def score(model,X,y):
 p=model.predict_proba(X)[:,1];return {'n':len(y),'positiveRateUP':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(y,p>=.5))}

def main():
 meta=load_meta()[-MAX_MARKETS:]; ids=[m for m,_ in meta];pub=load_public(ids);c=sqlite3.connect(TDB);data=[]
 for mid,_ in meta:data.extend(build_market(c,pub,mid))
 c.close(); markets=sorted(meta,key=lambda z:z[1]);a=int(.6*len(markets));b=int(.8*len(markets));sets=[set(m for m,_ in markets[:a]),set(m for m,_ in markets[a:b]),set(m for m,_ in markets[b:])];mats=[]
 for ms in sets:
  d=[x for x in data if x[0] in ms];mats.append((np.asarray([x[1] for x in d],float),np.asarray([x[2] for x in d],int)))
 model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=2.,random_state=20260826).fit(*mats[0])
 rep={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'teacher':'Pre-action checkpoint with floor>=0; label side of largest future <=5s surplus expansion that remains floor>=0. Winner excluded; public state strict-past <=3s.','markets':len(meta),'rows':len(data),'features':FEATURES,'publicArchiveRangeMarkets':[min(ids) if ids else None,max(ids) if ids else None],'train':score(model,*mats[0]),'validation':score(model,*mats[1]),'test':score(model,*mats[2]),'guards':{'sealed20260816':True,'winnerExcluded':True,'strictPastPublicMaxAgeMs':3000,'noEchtgeldTraining':True}}
 rep['gate']={'testAucRequired':.80,'pass':bool(rep['test']['auc']>=.80)}
 joblib.dump({'version':VERSION,'model':model,'features':FEATURES,'teacher':rep['teacher'],'researchOnly':True},OUT/'r4_deliberate_expansion_side_preaction_v1.joblib')
 out=OUT/'hourly'/f"r4_deliberate_expansion_side_preaction_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'markets':len(meta),'rows':len(data),'train':rep['train'],'validation':rep['validation'],'test':rep['test'],'gate':rep['gate']},ensure_ascii=False))
if __name__=='__main__':main()
