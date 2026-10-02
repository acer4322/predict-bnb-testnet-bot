from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_target_ledger_full_strategy_v0 as fs
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'; OUT=D/'target_ledger_full_inventory_skew_v0_report.json'; EPS=1e-9
MIDS=[1520549,1521620,1521630,1521634,1521746,1521813,1521898,1522206,1522232,1522236,1522287,1522364,1522567,1522600,1523086,1523138,1523369,1524387,1524427,1524491,1524503,1524504,1524659]

def agg(rows):
 p=[float(r['realizedPnl']) for r in rows if r['realizedPnl'] is not None]; md=sum(float(r['makerDesiredShares']) for r in rows); mf=sum(float(r['makerFilledShares']) for r in rows); tr=sum(float(r['takerRequestedShares']) for r in rows); tf=sum(float(r['takerFilledShares']) for r in rows)
 return {'markets':len(rows),'totalRealizedPnl':sum(p),'wins':sum(x>EPS for x in p),'losses':sum(x<-EPS for x in p),'winRate':sum(x>EPS for x in p)/len(p),'maxCumulativeDrawdown':fs.max_drawdown(p),'makerRealizationRate':mf/md if md>EPS else None,'takerRealizationRate':tf/tr if tr>EPS else None,'meanMakerFinalAbsNet':sum(float(r['makerFinalAbsNet']) for r in rows)/len(rows),'meanCombinedFinalAbsNet':sum(float(r['combinedFinalAbsNet']) for r in rows)/len(rows),'maxCombinedFinalAbsNet':max(float(r['combinedFinalAbsNet']) for r in rows),'makerExposureAreaShareSeconds':sum(float(r['makerExposureAreaShareSeconds']) for r in rows),'combinedExposureAreaShareSeconds':sum(float(r['combinedExposureAreaShareSeconds']) for r in rows),'preliminaryShape':bool(p and sum(p)>0 and sum(x>EPS for x in p)/len(p)>=0.5)}

def main():
 rep={'version':'TARGET_LEDGER_FULL_INVENTORY_SKEW_V0','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'cohort':'FORWARD23_DEVELOPMENT','strategyIntentSource':'FROZEN_R2_PAPER_INTENT_TAPE','dreamFillUsedForPnl':False,'configs':[],'guardrails':['Only compare baseline vs one preselected 18/9 offset0/3 target-skew candidate.','No threshold sweep.','Development markets only; not formal graduation.','Original R2 Taker lifecycle unchanged.']}
 old=fs.MAKER_SKEW_MODE
 try:
  for name,mode in [('BASELINE',None),('TRACK_SKEW_0_3_SIZE_HALF','TRACK_SKEW_0_3_SIZE_HALF')]:
   fs.MAKER_SKEW_MODE=mode; rows=[]
   for i,m in enumerate(MIDS,1):
    r=fs.run_market(m); rows.append(r); print(json.dumps({'config':name,'progress':i,'marketId':m,'pnl':r['realizedPnl'],'makerAbsNet':r['makerFinalAbsNet'],'combinedAbsNet':r['combinedFinalAbsNet']},ensure_ascii=False),flush=True)
   rep['configs'].append({'name':name,'aggregate':agg(rows),'rows':rows})
 finally: fs.MAKER_SKEW_MODE=old
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps({'ok':True,'report':str(OUT),'configs':[{'name':x['name'],**x['aggregate']} for x in rep['configs']]},ensure_ascii=False,allow_nan=True))
if __name__=='__main__': main()
