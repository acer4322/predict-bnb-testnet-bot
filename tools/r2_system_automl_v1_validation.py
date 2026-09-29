from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_candidate_v2_frozen as cand
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
MARKETS=[1546838,1548720,1535341,1541633,1546083,1548036]
CONFIGS=[
 {'name':'BASE','INVENTORY_CURVE_MODE':'STAGED_54_108','EMERGENCY_MODE':'E72_EDGE3_SOFTSPREAD','ACTION_QUALITY_FLOOR':'ERR36_STREAK3','STANDARD_REPLACE_MULT':2,'EMERGENCY_SLICE_MAX':2},
 {'name':'E72_EDGE2_FROZEN','INVENTORY_CURVE_MODE':'STAGED_54_108','EMERGENCY_MODE':'E72_EDGE2','ACTION_QUALITY_FLOOR':'ERR36_STREAK3','STANDARD_REPLACE_MULT':2,'EMERGENCY_SLICE_MAX':2},
]
def apply(c):
 for k,v in c.items():
  if k!='name':setattr(cand,k,v)
def row(r):
 a=r['actualExecution'];l=r['lifecycle'];return {'marketId':r['marketId'],'pnl':a['realizedPnl'],'finalAbsNet':a['combinedFinalAbsNet'],'tracking':a['finalAbsTrackingError'],'takerFilled':a['takerFilledShares'],'replace':l['actionCounts']['REPLACE_ROUTE'],'unresolved':l['unresolvedTakerReturns']}
def summary(rows):
 p=[float(x['pnl']) for x in rows];return {'markets':len(rows),'pnl':sum(p),'wins':sum(x>0 for x in p),'losses':sum(x<0 for x in p),'meanAbsNet':sum(x['finalAbsNet'] for x in rows)/len(rows),'meanTracking':sum(x['tracking'] for x in rows)/len(rows),'takerFilled':sum(x['takerFilled'] for x in rows),'replace':sum(x['replace'] for x in rows),'unresolved':sum(x['unresolved'] for x in rows)}
def main():
 results=[]
 for c in CONFIGS:
  apply(c);rs=[]
  for m in MARKETS:
   rr=row(cand.run_market(m));rs.append(rr);print(json.dumps({'config':c['name'],**rr},ensure_ascii=False),flush=True)
  results.append({'config':c,'summary':summary(rs),'rows':rs})
 base=results[0]['summary'];win=results[1]['summary'];decision='KEEP' if win['pnl']>base['pnl'] and win['pnl']>0 and win['meanAbsNet']<=base['meanAbsNet']*1.10 else 'REJECT'
 rep={'version':'R2_SYSTEM_AUTOML_V1_VALIDATION','researchOnly':True,'dreamFillAllowed':False,'markets':MARKETS,'frozenCandidate':'E72_EDGE2','results':results,'decision':decision}
 p=BASE/'r2_system_automl_v1_validation_report.json';p.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'base':base,'frozen':win},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
