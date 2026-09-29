from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_online_target_ledger_pair_completion_v8_exact_input as v8
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_online_exact_skew_robust_gate_v0_report.json'
MIDS=[1531343,1531361,1531383];EPS=1e-9
CONFIGS=[
 ('BASELINE',None,'SEQUENTIAL_V1'),
 ('SKEW_ONLY','TRACK_SKEW_0_3_SIZE_HALF','SEQUENTIAL_V1'),
 ('ROBUST_GATE_ONLY',None,'ROBUST_TRACK3_EDGE_SPREAD'),
 ('SKEW_PLUS_ROBUST','TRACK_SKEW_0_3_SIZE_HALF','ROBUST_TRACK3_EDGE_SPREAD'),
]

def dd(xs):
 peak=cur=m=0.0
 for x in xs:cur+=x;peak=max(peak,cur);m=max(m,peak-cur)
 return m

def agg(rows):
 p=[float(r['actualExecution']['realizedPnl']) for r in rows];md=sum(float(r['strategyRollout']['desiredMakerShares']) for r in rows);mf=sum(float(r['actualExecution']['makerFilledShares']) for r in rows)
 return {'markets':len(rows),'paperMakerOrders':sum(int(r['paperReference']['makerOrders']) for r in rows),'onlineMakerIntents':sum(int(r['strategyRollout']['makerIntents']) for r in rows),'intentTrajectoryCountExact':all(int(r['paperReference']['makerOrders'])==int(r['strategyRollout']['makerIntents']) for r in rows),'makerRealizationRate':mf/md if md>EPS else None,'totalRealizedPnl':sum(p),'wins':sum(x>EPS for x in p),'losses':sum(x<-EPS for x in p),'winRate':sum(x>EPS for x in p)/len(p),'maxCumulativeDrawdown':dd(p),'meanFinalAbsTrackingError':sum(float(r['actualExecution']['finalAbsTrackingError']) for r in rows)/len(rows),'meanCombinedFinalAbsNet':sum(float(r['actualExecution']['combinedFinalAbsNet']) for r in rows)/len(rows),'targetErrorAreaShareSeconds':sum(float(r['actualExecution']['targetErrorAreaShareSeconds']) for r in rows),'combinedExposureAreaShareSeconds':sum(float(r['actualExecution']['combinedExposureAreaShareSeconds']) for r in rows),'actionCounts':{k:sum(int(r['lifecycle']['actionCounts'][k]) for r in rows) for k in ['WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER']},'unresolvedTakerReturns':sum(int(r['lifecycle'].get('unresolvedTakerReturns',0)) for r in rows),'cancelPendingAtDataEnd':sum(int(r['lifecycle'].get('cancelPendingAtDataEnd',0)) for r in rows),'takerStateCounts':{k:sum(int(r['lifecycle'].get('takerChildStateCounts',{}).get(k,0)) for r in rows) for k in ['FILLED','PARTIAL','TERMINAL_ZERO_FILL']}}

def main():
 rep={'version':'R2_ONLINE_EXACT_SKEW_ROBUST_GATE_V0','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'cohort':'OPENED_FRESH_EXACT3','configs':[],'candidateComponents':{'makerSkew':'recovery full18 @ bid(offset0), worsening half9 @ offset3 when maker target error >=18','robustGate':'3/3 predicted tracking horizons beyond 0.5 train-residual sigma AND locked edge >= -0.02/share AND recovery spread <=1.5 ticks; otherwise WAIT; forced RETURN after sequence horizon'},'guardrails':['Four predeclared A/B configurations; no PnL threshold sweep.','Frozen Strategy Brain unchanged; paper vs online Maker intent count must remain exact.','Robust gate trained only old first100 curriculum markets; no exact3 fitting.','Opened development exact3 only; not graduation evidence.','Winner/PnL not runtime inputs.']}
 old_skew=v8.MAKER_SKEW_MODE;old_arb=v8.ARBITRATION_MODE
 try:
  for name,skew,arb in CONFIGS:
   v8.MAKER_SKEW_MODE=skew;v8.ARBITRATION_MODE=arb;rows=[]
   for i,m in enumerate(MIDS,1):
    r=v8.run_market(m);rows.append(r);print(json.dumps({'config':name,'progress':i,'marketId':m,'paperMaker':r['paperReference']['makerOrders'],'onlineMaker':r['strategyRollout']['makerIntents'],'pnl':r['actualExecution']['realizedPnl'],'absNet':r['actualExecution']['combinedFinalAbsNet'],'trackArea':r['actualExecution']['targetErrorAreaShareSeconds'],'exposure':r['actualExecution']['combinedExposureAreaShareSeconds'],'actions':r['lifecycle']['actionCounts'],'unresolved':r['lifecycle'].get('unresolvedTakerReturns',0)},ensure_ascii=False),flush=True)
   rep['configs'].append({'name':name,'makerSkewMode':skew,'arbitrationMode':arb,'aggregate':agg(rows),'rows':rows})
 finally:v8.MAKER_SKEW_MODE=old_skew;v8.ARBITRATION_MODE=old_arb
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps({'ok':True,'report':str(OUT),'configs':[{'name':x['name'],**x['aggregate']} for x in rep['configs']]},ensure_ascii=False,allow_nan=True))
if __name__=='__main__':main()
