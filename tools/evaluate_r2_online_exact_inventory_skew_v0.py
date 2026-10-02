from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_online_target_ledger_pair_completion_v8_exact_input as v8
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_online_exact_inventory_skew_v0_report.json'
MIDS=[1531343,1531361,1531383];EPS=1e-9

def dd(xs):
 peak=cur=m=0.0
 for x in xs:cur+=x;peak=max(peak,cur);m=max(m,peak-cur)
 return m

def agg(rows):
 p=[float(r['actualExecution']['realizedPnl']) for r in rows];md=sum(float(r['strategyRollout']['desiredMakerShares']) for r in rows);mf=sum(float(r['actualExecution']['makerFilledShares']) for r in rows)
 return {'markets':len(rows),'paperMakerOrders':sum(int(r['paperReference']['makerOrders']) for r in rows),'onlineMakerIntents':sum(int(r['strategyRollout']['makerIntents']) for r in rows),'intentTrajectoryCountExact':all(int(r['paperReference']['makerOrders'])==int(r['strategyRollout']['makerIntents']) for r in rows),'makerRealizationRate':mf/md if md>EPS else None,'totalRealizedPnl':sum(p),'wins':sum(x>EPS for x in p),'losses':sum(x<-EPS for x in p),'winRate':sum(x>EPS for x in p)/len(p),'maxCumulativeDrawdown':dd(p),'meanFinalAbsTrackingError':sum(float(r['actualExecution']['finalAbsTrackingError']) for r in rows)/len(rows),'meanCombinedFinalAbsNet':sum(float(r['actualExecution']['combinedFinalAbsNet']) for r in rows)/len(rows),'targetErrorAreaShareSeconds':sum(float(r['actualExecution']['targetErrorAreaShareSeconds']) for r in rows),'combinedExposureAreaShareSeconds':sum(float(r['actualExecution']['combinedExposureAreaShareSeconds']) for r in rows),'actionCounts':{k:sum(int(r['lifecycle']['actionCounts'][k]) for r in rows) for k in ['WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER']},'unresolvedTakerReturns':sum(int(r['lifecycle'].get('unresolvedTakerReturns',0)) for r in rows),'cancelPendingAtDataEnd':sum(int(r['lifecycle'].get('cancelPendingAtDataEnd',0)) for r in rows)}

def main():
 rep={'version':'R2_ONLINE_EXACT_INVENTORY_SKEW_V0','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'cohort':'OPENED_FRESH_EXACT3','configs':[],'candidate':'TRACK_SKEW_0_3_SIZE_HALF','guardrails':['Execution-only A/B; Frozen Strategy Brain unchanged.','Exact consumer snapshots required.','Intent count must remain paper==online per market.','Opened development only; no graduation claim.','Sequential arbitration model intentionally unchanged for this isolation test.']}
 old=v8.MAKER_SKEW_MODE
 try:
  for name,mode in [('BASELINE',None),('TRACK_SKEW_0_3_SIZE_HALF','TRACK_SKEW_0_3_SIZE_HALF')]:
   v8.MAKER_SKEW_MODE=mode;rows=[]
   for i,m in enumerate(MIDS,1):
    r=v8.run_market(m);rows.append(r);print(json.dumps({'config':name,'progress':i,'marketId':m,'paperMaker':r['paperReference']['makerOrders'],'onlineMaker':r['strategyRollout']['makerIntents'],'pnl':r['actualExecution']['realizedPnl'],'absNet':r['actualExecution']['combinedFinalAbsNet'],'trackArea':r['actualExecution']['targetErrorAreaShareSeconds'],'exposure':r['actualExecution']['combinedExposureAreaShareSeconds'],'actions':r['lifecycle']['actionCounts']},ensure_ascii=False),flush=True)
   rep['configs'].append({'name':name,'aggregate':agg(rows),'rows':rows})
 finally:v8.MAKER_SKEW_MODE=old
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps({'ok':True,'report':str(OUT),'configs':[{'name':x['name'],**x['aggregate']} for x in rep['configs']]},ensure_ascii=False,allow_nan=True))
if __name__=='__main__':main()
