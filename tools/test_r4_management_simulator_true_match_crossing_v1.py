from __future__ import annotations
import json,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1';TAPE=ROOT/'data/execution_tape_v1/markets';ENTRY=1092
from src.predict_bot.execution_tape_archive_v1 import load_archive
from tools import hftbacktest_true_match_calibration_v0 as tm

def first_rows():
 out=[]
 for i in range(4):
  d=json.loads((P/f'r4_management_simulator_economic_late20d_chunk{i}_v1.json').read_text())
  first={}
  for r in d['rows']:
   k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
   if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
  out+=list(first.values())
 return out

def market_data(mid):
 d=load_archive(TAPE/f'{mid}.json.xz');ups=sorted(d.get('updates') or [],key=lambda r:(int(r[1]),int(r[0])));book={'bids':{},'asks':{}};snaps=[]
 for u in ups:
  t=int(u[1]);cp=int(u[3]);chg=u[6] or {}
  if cp and u[4] is not None and u[5] is not None:book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}}
  else:
   for side in ('bids','asks'):
    for x in chg.get(side,[]) or []:
     p=float(x[0]);after=float(x[2]);
     if after<=1e-9:book[side].pop(p,None)
     else:book[side][p]=after
  snaps.append((t,dict(book['bids']),dict(book['asks'])))
 trades=[]
 for raw in d.get('matches') or []:
  n=tm.normalize_match(raw)
  if n is not None:trades.append({'t':int(n['tsMs'])+500,'px':float(n['nativeYesPrice']),'qty':float(n['qty']),'agg':str(n['nativeAggressor'])})
 trades.sort(key=lambda x:x['t']);return snaps,trades

def depth_at(snaps,t,side,px):
 state=None
 for z in snaps:
  if z[0]>t:break
  state=z
 if state is None:return 0.0
 return float((state[1] if side=='bids' else state[2]).get(px,0.0))
def rel_qty(trades,a,b,side,native_px):
 if side=='UP':return sum(x['qty'] for x in trades if a<x['t']<=b and x['agg']=='SELL' and x['px']<=native_px+1e-9)
 return sum(x['qty'] for x in trades if a<x['t']<=b and x['agg']=='BUY' and x['px']>=native_px-1e-9)
def main():
 rows=first_rows();by=defaultdict(list)
 for r in rows:by[int(r['marketId'])].append(r)
 out=[]
 for mid,rr in by.items():
  snaps,trades=market_data(mid)
  for r in rr:
   t0=int(r['t']);age=float(r.get('checkpointOldestOwnerAgeS') or 0);rest=t0-int(round(age*1000))+ENTRY;side=str(r['checkpointSide']);px=float(r['requested_px']);native_side='bids' if side=='UP' else 'asks';native_px=px if side=='UP' else round(1.0-px,10);initial=depth_at(snaps,rest,native_side,native_px);past=rel_qty(trades,rest,t0,side,native_px);spill=max(0.,past-initial);qa=max(0.,initial-past);future=rel_qty(trades,t0,t0+5000,side,native_px);req=float(r['checkpointUnresolvedQty']);fill=max(0.,min(req,future-qa));out.append({'marketId':mid,'actualFill':float(r['rootFillShares5s']),'actualAny':int(float(r['rootFillShares5s'])>1e-9),'actualCompleted':int(r['rootCompleted5s']),'initialQueueAhead':initial,'pastRelevantTradeQty':past,'preCheckpointSpill':spill,'queueAheadAtCheckpoint':qa,'futureRelevantTradeQty':future,'predFill':fill,'predAny':int(fill>1e-9),'predCompleted':int(fill>=req-1e-9)})
 n=len(out);rate=lambda k:sum(float(x[k]) for x in out)/n;aa=rate('actualAny');pa=rate('predAny');ac=rate('actualCompleted');pc=rate('predCompleted');scale=max(1.,sum(abs(x['actualFill']) for x in out)/n);qmae=sum(abs(x['predFill']-x['actualFill']) for x in out)/n/scale;base=json.loads((P/'r4_management_simulator_true_match_queue_v1.json').read_text())
 rep={'version':'R4_MANAGEMENT_SIMULATOR_TRUE_MATCH_CROSSING_V1','researchOnly':True,'roots':n,'actualAnyFillRate':aa,'predAnyFillRate':pa,'anyFillRateAbsError':abs(pa-aa),'actualCompletedRate':ac,'predCompletedRate':pc,'completedRateAbsError':abs(pc-ac),'fillQtyNormalizedMAE':qmae,'rootsWithPreCheckpointSpill':sum(x['preCheckpointSpill']>1e-9 for x in out),'comparisonVsExactPriceTrueMatch':{'anyFillErrorImprovement':base['anyFillRateAbsError']-abs(pa-aa),'completedErrorImprovement':base['completedRateAbsError']-abs(pc-ac),'fillQtyNMAEImprovement':base['fillQtyNormalizedMAE']-qmae,'preCheckpointSpillDelta':sum(x['preCheckpointSpill']>1e-9 for x in out)-base['rootsWithPreCheckpointSpill']},'keepCrossingMechanics':(abs(pa-aa)<base['anyFillRateAbsError'] or abs(pc-ac)<base['completedRateAbsError'] or qmae<base['fillQtyNormalizedMAE']) and sum(x['preCheckpointSpill']>1e-9 for x in out)<=base['rootsWithPreCheckpointSpill']+2,'interpretation':'Strict-past true-match queue with opposing crossing-trade semantics.'};(P/'r4_management_simulator_true_match_crossing_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
