from __future__ import annotations
import json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'data/target_wallet_official_v1.db';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V85_COMPOSITE_RELAY_1912961_20260903.json';MID=1912961
con=sqlite3.connect(DB);con.row_factory=sqlite3.Row;rs=con.execute("select role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(MID,)).fetchall();con.close()
u=d=cost=0.0;rows=[]
for r in rs:
 q=float(r['shares'] or 0);p=float(r['average_price'] or 0);side=str(r['side']);gap=abs(u-d);weak='UP' if u<d-1e-9 else 'DOWN' if d<u-1e-9 else None;rq=min(q,gap) if side==weak else 0.0;eq=q-rq
 rows.append({'t':int(r['first_event_ms']),'role':str(r['role']),'side':side,'price':p,'qty':q,'repairAllocation':rq,'expandAllocation':eq,'composite':rq>1e-9 and eq>1e-9,'gapPre':gap})
 if side=='UP':u+=q
 else:d+=q
 cost+=q*p
rel=[]
for i,x in enumerate(rows):
 if not x['composite']:continue
 nxt=None
 for y in rows[i+1:]:
  if y['repairAllocation']>1e-9 and y['side']!=x['side']:
   nxt=y;break
 rel.append({'compositeT':x['t'],'side':x['side'],'repairAllocation':x['repairAllocation'],'overflowAllocation':x['expandAllocation'],'nextOppRepairT':None if nxt is None else nxt['t'],'lagSec':None if nxt is None else (nxt['t']-x['t'])/1000.0,'nextOppRepairSide':None if nxt is None else nxt['side'],'nextOppRepairQty':None if nxt is None else nxt['repairAllocation']})
withn=[x for x in rel if x['lagSec'] is not None]
res={'version':'TARGET_ETH_V85_COMPOSITE_RELAY_1912961','date':'2026-09-03','researchOnly':True,'marketId':MID,'summary':{'compositeParents':len(rel),'withNextOppositeRepair':len(withn),'within30s':sum(x['lagSec']<=30 for x in withn),'within60s':sum(x['lagSec']<=60 for x in withn),'medianLagSec':sorted(x['lagSec'] for x in withn)[len(withn)//2] if withn else None},'relayRows':rel,'boundary':['Offline Target post-market analysis only','No Target timing/side/qty as runtime authority']}
OUT.write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':res['summary'],'first8':rel[:8]}))
