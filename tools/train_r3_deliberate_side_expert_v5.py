from __future__ import annotations
import json,sqlite3,bisect,math
from pathlib import Path
from collections import deque
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
TDB=ROOT/'data'/'target_wallet_official_v1.db'
PDB=ROOT/'data'/'public_source_snapshot_archive_v2.db'
OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
PUBLIC=['predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps','spotMinusChainlinkBps','directionScore','spotReturn1sBps','spotReturn3sBps','spotReturn5sBps','futuresReturn1sBps','futuresReturn3sBps','futuresReturn5sBps','perpSpotBasisBps','spotQueueImbalance','futuresQueueImbalance','futuresTakerImbalance1s','secondsLeft']
STATE=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','signed_inventory','signed_payoff_gap','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','maker_events_15s','taker_events_15s','up_events_15s','down_events_15s','up_shares_15s','down_shares_15s','event_index_norm']
FEATURES=STATE+PUBLIC

def fee(sh,px,role): return sh*px*.02 if role=='TAKER' else 0.0

def load_public(keep):
 c=sqlite3.connect(PDB); c.row_factory=sqlite3.Row; d={}
 q='select market_id,sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id>=? order by market_id,sampled_at_ms'
 for r in c.execute(q,(min(keep),)):
  mid=int(r['market_id']); js=json.loads(r['snapshot_json']); d.setdefault(mid,[[],[]]); d[mid][0].append(int(r['sampled_at_ms'])); d[mid][1].append(js)
 c.close(); return d

def pub_at(pub,mid,t):
 z=pub.get(mid)
 if not z:return None
 i=bisect.bisect_right(z[0],t)-1
 if i<0:return None
 if t-z[0][i]>3000:return None
 return z[1][i]

def val(x):
 try:
  y=float(x); return y if math.isfinite(y) else 0.0
 except:return 0.0

def build_market(c,pub,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
 if len(rr)<4:return []
 up=down=cost=fees=0.; hist=deque(); prev_t=None; snaps=[]
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh; fees+=fee(sh,px,role); pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down
  hist.append((t,role,side,sh));
  while hist and t-hist[0][0]>15000:hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist)
  p=pub_at(pub,mid,t)
  feat={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'signed_inventory':up-down,'signed_payoff_gap':pu-pd,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'up_events_15s':float(sum(x[2]=='UP' for x in r15)),'down_events_15s':float(sum(x[2]=='DOWN' for x in r15)),'up_shares_15s':float(sum(x[3] for x in r15 if x[2]=='UP')),'down_shares_15s':float(sum(x[3] for x in r15 if x[2]=='DOWN')),'event_index_norm':i/max(1,len(rr)-1)}
  if p:
   for k in PUBLIC:feat[k]=val(p.get(k))
  else:
   for k in PUBLIC:feat[k]=0.0
  snaps.append((t,up,down,fl,ss,feat,p is not None)); prev_t=t
 out=[]
 for i,(t,u,d,fl,ss,feat,haspub) in enumerate(snaps):
  if fl<0 or ss<5 or not haspub:continue
  j=i+1
  while j<len(snaps) and snaps[j][0]-t<=5000:j+=1
  if j<=i+1:continue
  # teacher: among future states that stay floor>=0, choose the state with largest increase in absolute surplus.
  cand=[]
  for z in snaps[i+1:j]:
   if z[3] < 0: continue
   inc=z[4]-ss
   if inc>=max(5.,.1*ss): cand.append((inc,z))
  if not cand:continue
  _,best=max(cand,key=lambda q:q[0]); bu,bd=best[1],best[2]
  if abs(bu-bd)<1e-9:continue
  label=1 if bu>bd else 0
  out.append((mid,[feat[k] for k in FEATURES],label))
 return out

def score(m,X,y):
 p=m.predict_proba(X)[:,1]; return {'n':len(y),'positiveRateUP':float(y.mean()),'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,(p>=.5).astype(int)))}

def main():
 c=sqlite3.connect(TDB); pc=sqlite3.connect(PDB); allpub=[r[0] for r in pc.execute('select distinct market_id from public_source_snapshots_v2 order by market_id')]; pc.close(); tm=set(r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC'")); keep=[m for m in allpub if m in tm][-300:]; pub=load_public(keep); mids=sorted(set(keep).intersection(pub)); data=[]
 for mid in mids:data.extend(build_market(c,pub,mid))
 c.close(); uniq=sorted(set(x[0] for x in data)); a=int(.6*len(uniq));b=int(.8*len(uniq)); sets=[set(uniq[:a]),set(uniq[a:b]),set(uniq[b:])]; mats=[]
 for ms in sets:
  dd=[x for x in data if x[0] in ms]; mats.append((np.asarray([x[1] for x in dd],float),np.asarray([x[2] for x in dd],int)))
 model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=2.,random_state=20260824).fit(*mats[0])
 rep={'version':'R3_DELIBERATE_SIDE_HGB_V5','teacher':'Only current floor>=0 checkpoints with a safe expansion within 5s; label is the UP/DOWN side of the largest safe future surplus expansion. Public context must be strict-past <=3s. Winner excluded.','markets':len(uniq),'rows':len(data),'features':FEATURES,'publicArchiveRangeMarkets':[min(pub) if pub else None,max(pub) if pub else None],'train':score(model,*mats[0]),'validation':score(model,*mats[1]),'test':score(model,*mats[2])}
 joblib.dump({'model':model,'features':FEATURES,'teacherVersion':'R3_DELIBERATE_SIDE_V5'},OUT/'r3_deliberate_side_hgb_v5.joblib'); (OUT/'r3_deliberate_side_hgb_v5_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
