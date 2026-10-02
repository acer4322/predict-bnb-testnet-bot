from __future__ import annotations
import json, sqlite3, statistics, math
from collections import Counter,defaultdict
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.test_cap100_dynamic_pacer_fault_v1 import load, turn, qt, pace, DB, V, MIDS, BUDGET
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_v2_cycle_structure_preservation_v0.json'
CHECKS=[30000,60000,120000,180000,240000]

def load_full(c,m):
 rr=c.execute("select side,price,shares,placed_at_ms,placement_state_json from our_orders where strategy_version=? and market_id=? and channel='MAKER' order by placed_at_ms,order_id",(V,m)).fetchall(); z=[]
 for r in rr:
  try:d=json.loads(r['placement_state_json'] or '{}')
  except:d={}
  reason=str(d.get('reason') or '').upper(); kind='REPAIR' if ('REPAIR' in reason or 'UNRESOLVED' in reason) else 'NORMAL'
  z.append({'t':int(r['placed_at_ms']),'side':str(r['side']),'price':float(r['price']),'shares':float(r['shares']),'kind':kind,'reason':reason})
 return z

def side_alt(xs):
 if len(xs)<2:return None
 return sum(xs[i]['side']!=xs[i-1]['side'] for i in range(1,len(xs)))/(len(xs)-1)

def multi_ts(xs):
 c=Counter(x['t'] for x in xs); return sum(v>=2 for v in c.values())
def multi_emit(xs):
 c=Counter(x['tMs'] for x in xs); return sum(v>=2 for v in c.values())
def checkpoint_net(xs,tfield,shfield,scale=1.0):
 out={}
 for cp in CHECKS:
  up=sum(float(x[shfield])*scale for x in xs if int(x[tfield])<=cp and x['side']=='UP')
  dn=sum(float(x[shfield])*scale for x in xs if int(x[tfield])<=cp and x['side']=='DOWN')
  out[str(cp)]={'net':up-dn,'gross':up+dn,'ratio':abs(up-dn)/(up+dn) if up+dn else 0.0}
 return out

def main():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 prev=[]
 for (m,) in c.execute("select distinct market_id from our_orders where strategy_version=? and channel='MAKER' and market_id<? order by market_id",(V,MIDS[0])):
  r=load(c,m)
  if r:prev.append(turn(r))
 rows=[]
 for m in MIDS:
  p95=qt(prev,.95); scale=min(1,BUDGET/p95); src=load_full(c,m); emitted,_,spent=pace(src,scale)
  # attach source reason to emitted carrier
  for e in emitted:
   si=e['srcIntent']; e['reason']=src[si]['reason'] if si < len(src) else ''
  src0=src[0]['t']; sr=[{**x,'tMs':x['t']-src0} for x in src]
  src_rep=[x for x in sr if x['kind']=='REPAIR']; em_rep=[x for x in emitted if x['kind']=='REPAIR']
  src_burst=[x for x in sr if 'BURST' in x['reason']]; em_burst=[x for x in emitted if 'BURST' in x.get('reason','')]
  src_cp=checkpoint_net(sr,'tMs','shares',scale); em_cp=checkpoint_net(emitted,'tMs','shares')
  diffs=[]
  for cp in map(str,CHECKS): diffs.append(abs(src_cp[cp]['ratio']-em_cp[cp]['ratio']))
  rows.append({
   'marketId':m,'scale':scale,'spent':spent,
   'orderRetention':len(emitted)/len(src) if src else None,
   'sideAlternationSource':side_alt(sr),'sideAlternationV2':side_alt(emitted),
   'multiOrderTimestampSource':multi_ts(src),'multiOrderTimestampV2':multi_emit(emitted),
   'burstSource':len(src_burst),'burstEmitted':len(em_burst),'burstRetention':len(em_burst)/len(src_burst) if src_burst else None,
   'repairSource':len(src_rep),'repairEmitted':len(em_rep),'repairCarrierRetention':len(em_rep)/len(src_rep) if src_rep else None,
   'firstRepairSourceMs':min((x['tMs'] for x in src_rep),default=None),'firstRepairV2Ms':min((x['tMs'] for x in em_rep),default=None),
   'firstRepairDelayDeltaMs':(min(x['tMs'] for x in em_rep)-min(x['tMs'] for x in src_rep)) if src_rep and em_rep else None,
   'inventoryRatioCheckpointMAE':sum(diffs)/len(diffs),
   'sourceCheckpoint':src_cp,'v2Checkpoint':em_cp,
  })
  prev.append(turn(src))
 vals=lambda k:[r[k] for r in rows if r[k] is not None]
 summary={
  'markets':len(rows),'medianOrderRetention':statistics.median(vals('orderRetention')),
  'medianAlternationDelta':statistics.median([r['sideAlternationV2']-r['sideAlternationSource'] for r in rows if r['sideAlternationV2'] is not None]),
  'totalMultiTimestampSource':sum(r['multiOrderTimestampSource'] for r in rows),'totalMultiTimestampV2':sum(r['multiOrderTimestampV2'] for r in rows),
  'burstSourceTotal':sum(r['burstSource'] for r in rows),'burstEmittedTotal':sum(r['burstEmitted'] for r in rows),
  'repairSourceTotal':sum(r['repairSource'] for r in rows),'repairEmittedTotal':sum(r['repairEmitted'] for r in rows),
  'marketsWithRepair':sum(r['repairSource']>0 for r in rows),'marketsRepairPreserved':sum(r['repairSource']==0 or r['repairEmitted']>0 for r in rows),
  'medianFirstRepairDelayDeltaMs':statistics.median(vals('firstRepairDelayDeltaMs')) if vals('firstRepairDelayDeltaMs') else None,
  'medianInventoryRatioCheckpointMAE':statistics.median(vals('inventoryRatioCheckpointMAE')),
 }
 OUT.write_text(json.dumps({'version':'CAP100_V2_CYCLE_STRUCTURE_PRESERVATION_V0','summary':summary,'markets':rows},indent=2),encoding='utf-8')
 print(json.dumps({'out':str(OUT),'summary':summary,'markets':[{'id':r['marketId'],'ret':round(r['orderRetention'],3),'altDelta':round(r['sideAlternationV2']-r['sideAlternationSource'],3),'burst':f"{r['burstEmitted']}/{r['burstSource']}",'repair':f"{r['repairEmitted']}/{r['repairSource']}",'repairDt':r['firstRepairDelayDeltaMs'],'invMAE':round(r['inventoryRatioCheckpointMAE'],3)} for r in rows]},indent=2))
if __name__=='__main__': main()
