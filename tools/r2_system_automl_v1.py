from __future__ import annotations
import json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_candidate_v2_frozen as cand
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
MARKETS=[1534534,1550728,1538475,1535523,1536024,1532025]
CONFIGS=[
 {'name':'BASE','INVENTORY_CURVE_MODE':'STAGED_54_108','EMERGENCY_MODE':'E72_EDGE3_SOFTSPREAD','ACTION_QUALITY_FLOOR':'ERR36_STREAK3','STANDARD_REPLACE_MULT':2,'EMERGENCY_SLICE_MAX':2},
 {'name':'CURVE_STOP72','INVENTORY_CURVE_MODE':'STOP_AT_72','EMERGENCY_MODE':'E72_EDGE3_SOFTSPREAD','ACTION_QUALITY_FLOOR':'ERR36_STREAK3','STANDARD_REPLACE_MULT':2,'EMERGENCY_SLICE_MAX':2},
 {'name':'NO_PAIR_REPLACE','INVENTORY_CURVE_MODE':'STAGED_54_108','EMERGENCY_MODE':'NO_PAIR_REPLACE','ACTION_QUALITY_FLOOR':'ERR36_STREAK3','STANDARD_REPLACE_MULT':2,'EMERGENCY_SLICE_MAX':2},
 {'name':'E72_EDGE2','INVENTORY_CURVE_MODE':'STAGED_54_108','EMERGENCY_MODE':'E72_EDGE2','ACTION_QUALITY_FLOOR':'ERR36_STREAK3','STANDARD_REPLACE_MULT':2,'EMERGENCY_SLICE_MAX':2},
]

def apply(cfg):
 for k,v in cfg.items():
  if k!='name': setattr(cand,k,v)

def compact(r):
 a=r['actualExecution'];l=r['lifecycle'];return {'marketId':r['marketId'],'pnl':a['realizedPnl'],'finalAbsNet':a['combinedFinalAbsNet'],'tracking':a['finalAbsTrackingError'],'makerRealization':a['makerRealizationRate'],'takerFilled':a['takerFilledShares'],'replace':l['actionCounts']['REPLACE_ROUTE'],'wait':l['actionCounts']['WAIT_FOR_CLARITY'],'unresolved':l['unresolvedTakerReturns']}
def summary(rows):
 pn=[float(x['pnl']) for x in rows];return {'markets':len(rows),'pnl':sum(pn),'wins':sum(x>0 for x in pn),'losses':sum(x<0 for x in pn),'meanAbsNet':sum(x['finalAbsNet'] for x in rows)/len(rows),'meanTracking':sum(x['tracking'] for x in rows)/len(rows),'takerFilled':sum(x['takerFilled'] for x in rows),'replace':sum(x['replace'] for x in rows),'unresolved':sum(x['unresolved'] for x in rows),'makerRealization':sum(x['makerRealization'] for x in rows)/len(rows)}
def score(s):
 # system-level: economics first, then balance and intervention burden
 return s['pnl'] - 0.02*s['meanAbsNet'] - 0.01*s['meanTracking'] - 0.01*s['takerFilled'] - 0.05*s['replace'] - 0.25*s['unresolved']
def main():
 out=[]
 for cfg in CONFIGS:
  apply(cfg); rows=[]
  for mid in MARKETS:
   r=cand.run_market(mid);rows.append(compact(r));print(json.dumps({'config':cfg['name'],'marketId':mid,'pnl':rows[-1]['pnl'],'absNet':rows[-1]['finalAbsNet'],'track':rows[-1]['tracking']},ensure_ascii=False),flush=True)
  s=summary(rows);s['fitness']=score(s);out.append({'config':cfg,'summary':s,'rows':rows})
 out.sort(key=lambda x:x['summary']['fitness'],reverse=True)
 rep={'version':'R2_SYSTEM_AUTOML_V1','researchOnly':True,'dreamFillAllowed':False,'unitOfSearch':'full coupled R2 loop; no isolated module scoring','markets':MARKETS,'fitness':'pnl - .02*meanAbsNet - .01*meanTracking - .01*takerFilled - .05*replace - .25*unresolved','candidates':out,'winner':out[0]['config']['name']}
 p=BASE/'r2_system_automl_v1_report.json';p.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'winner':rep['winner'],'ranking':[{'name':x['config']['name'],**x['summary']} for x in out]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
