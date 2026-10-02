from __future__ import annotations
import sqlite3,json,statistics,math
from pathlib import Path
from predict_bot.core import taker_fee
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data/research/r3_v0/target_r3_payoff_channel_attribution_v1.json'; FEE_BPS=200
uri=f"file:{DB.as_posix()}?mode=ro&immutable=1"; c=sqlite3.connect(uri,uri=True); c.row_factory=sqlite3.Row
res={int(r['market_id']):dict(r) for r in c.execute("select market_id,winner,net_pnl_usdt from target_market_results where asset='BTC'")}
rows=c.execute("select market_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id")
markets=[]; cur=None; evs=[]
def finish(mid,evs):
 if mid is None or mid not in res or not evs:return
 up=down=cost=fees=0.; prev=None; traj=[]
 for i,e in enumerate(evs):
  role=str(e['role']); side=str(e['side']); sh=float(e['shares'] or 0); px=float(e['average_price'] or 0)
  pre_up,pre_down,pre_cost,pre_fees=up,down,cost,fees
  pre_pu=up-cost-fees; pre_pd=down-cost-fees; pre_floor=min(pre_pu,pre_pd); pre_upside=max(pre_pu,pre_pd); pre_net=up-down
  if side=='UP': up+=sh
  else: down+=sh
  cost+=sh*px
  if role=='TAKER': fees+=float(taker_fee(sh,px,FEE_BPS))
  pu=up-cost-fees; pd=down-cost-fees; floor=min(pu,pd); upside=max(pu,pd); post_net=up-down
  if role=='TAKER': effect='ADD' if (pre_net>0 and side=='UP') or (pre_net<0 and side=='DOWN') else 'REPAIR' if abs(pre_net)>1e-9 else 'FLAT_START'
  else: effect='MAKER'
  traj.append({'i':i,'t':int(e['first_event_ms'] or 0),'role':role,'effect':effect,'side':side,'shares':sh,'price':px,'preFloor':pre_floor,'preUpside':pre_upside,'floor':floor,'upside':upside,'dFloor':floor-pre_floor,'dUpside':upside-pre_upside,'preNet':pre_net,'postNet':post_net})
 # identify safe-growth phases: once floor >= -5, subsequent events until end; and stronger floor>=0 subset
 safe_idxs=[i for i,x in enumerate(traj) if x['floor']>=-5]
 if not safe_idxs:return
 start=safe_idxs[0]; seg=traj[start:]
 def agg(xs):
  out={}
  for typ in ['MAKER','REPAIR','ADD','FLAT_START']:
   z=[x for x in xs if x['effect']==typ]; out[typ]={'events':len(z),'shares':sum(x['shares'] for x in z),'dFloor':sum(x['dFloor'] for x in z),'dUpside':sum(x['dUpside'] for x in z),'positiveUpsideEvents':sum(x['dUpside']>0 for x in z),'positiveFloorEvents':sum(x['dFloor']>0 for x in z)}
  return out
 # staircase events: upside +>=5 while floor not worsened by >2; safety-build: floor +>=2
 stair=[x for x in seg if x['dUpside']>=5 and x['dFloor']>=-2]
 safety=[x for x in seg if x['dFloor']>=2]
 # large asymmetric safe states, akin user examples: upside >= 2*abs(floor) and upside>=25 while floor>=-5
 asym=[x for x in seg if x['upside']>=25 and x['floor']>=-5 and x['upside']>=2*max(abs(x['floor']),1)]
 markets.append({'marketId':mid,'winner':res[mid].get('winner'),'officialNetPnl':res[mid].get('net_pnl_usdt'),'safeStartIndex':start,'safeStart':traj[start],'terminal':traj[-1],'safeSegmentAgg':agg(seg),'staircaseAgg':agg(stair),'safetyBuildAgg':agg(safety),'asymmetricSafeStates':len(asym),'firstAsymmetricSafe':asym[0] if asym else None,'lastAsymmetricSafe':asym[-1] if asym else None})
for r in rows:
 mid=int(r['market_id'])
 if cur is None:cur=mid
 if mid!=cur:finish(cur,evs);cur=mid;evs=[]
 evs.append(r)
finish(cur,evs); c.close()
# aggregate across markets, and stronger subset that actually forms asymmetric safe state
sub=[m for m in markets if m['asymmetricSafeStates']>0]
def combine(ms,key):
 out={}
 for typ in ['MAKER','REPAIR','ADD','FLAT_START']:
  out[typ]={k:sum(m[key][typ][k] for m in ms) for k in ['events','shares','dFloor','dUpside','positiveUpsideEvents','positiveFloorEvents']}
 return out
summary={'marketsWithSafeishState':len(markets),'marketsWithAsymmetricSafeState':len(sub),'rate':len(sub)/len(markets) if markets else None,'safeSegment':combine(sub,'safeSegmentAgg'),'staircase':combine(sub,'staircaseAgg'),'safetyBuild':combine(sub,'safetyBuildAgg')}
# shares of positive upside contribution by event category in staircase; positive-only so cancellations don't hide contribution
# derive top examples
examples=sorted(sub,key=lambda m:(m['lastAsymmetricSafe']['upside'] if m['lastAsymmetricSafe'] else -1),reverse=True)[:20]
OUT.write_text(json.dumps({'version':'TARGET_R3_PAYOFF_CHANNEL_ATTRIBUTION_V1','boundary':'Accounting attribution of realized Target parent-order path. REPAIR/ADD classified structurally from side vs pre-event inventory surplus. Does not prove subjective intent.','summary':summary,'topExamples':examples,'markets':markets},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'summary':summary,'examples':[{'m':m['marketId'],'first':m['firstAsymmetricSafe'],'last':m['lastAsymmetricSafe'],'stair':m['staircaseAgg']} for m in examples[:5]]},ensure_ascii=False,indent=2))
