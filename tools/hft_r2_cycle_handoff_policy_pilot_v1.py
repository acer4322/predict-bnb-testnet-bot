from __future__ import annotations
import argparse,json,time
from pathlib import Path
from typing import Any
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter as base
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
class C:
 def __init__(self,p):self.p=float(p)
 def predict_proba(self,x):return np.tile(np.array([[1-self.p,self.p]],float),(len(x),1))
def art(mode):
 probs={'KEEP':(1,0,0),'WAIT':(0,1,0),'REPLACE':(1,0,1),'RETURN':(0,0,0)}[mode]
 return {'currentOnly':{'features':['workingRecoveryExists'],'models':{'act':C(probs[0]),'wait':C(probs[1]),'replace':C(probs[2])}},'thresholds':{'act':.5,'wait':.5,'replace':.5}}
def run(mid,mode):
 orig=base.joblib.load
 def load(path,*a,**kw):
  if Path(path).resolve()==base.MODEL_PATH.resolve():return art(mode)
  return orig(path,*a,**kw)
 base.joblib.load=load;t=time.perf_counter()
 try:r=base.run_market(mid)
 finally:base.joblib.load=orig
 a=r['actualExecution'];l=r['lifecycle'];return {'marketId':mid,'mode':mode,'runtimeSeconds':time.perf_counter()-t,'pnl':a['realizedPnl'],'finalAbsNet':a['combinedFinalAbsNet'],'tracking':a['finalAbsTrackingError'],'trackingArea':a['targetErrorAreaShareSeconds'],'exposureArea':a['combinedExposureAreaShareSeconds'],'floor':a['finalPortfolio']['worst_case_floor'],'pairedCoverage':a['finalPortfolio']['combined_paired_coverage'],'makerFilled':a['makerFilledShares'],'takerFilled':a['takerFilledShares'],'takerFees':a['takerFeesUsdt'],'actions':l['actionCounts'],'unresolved':l['unresolvedTakerReturns'],'cancelPending':l['cancelPendingAtDataEnd']}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--markets',default='1572594,1572805,1573000');ap.add_argument('--output',default='hft_r2_cycle_handoff_policy_pilot_v1.json');a=ap.parse_args();mids=[int(x) for x in a.markets.split(',')];rows=[]
 for mid in mids:
  for mode in ('KEEP','WAIT','REPLACE','RETURN'):
   z=run(mid,mode);rows.append(z);print(json.dumps(z,ensure_ascii=False),flush=True)
 by={}
 for mode in ('KEEP','WAIT','REPLACE','RETURN'):
  x=[r for r in rows if r['mode']==mode];by[mode]={'pnl':sum(float(r['pnl']) for r in x),'meanAbsNet':sum(r['finalAbsNet'] for r in x)/len(x),'meanTracking':sum(r['tracking'] for r in x)/len(x),'meanFloor':sum(r['floor'] for r in x)/len(x),'meanPairedCoverage':sum(r['pairedCoverage'] for r in x)/len(x),'takerFilled':sum(r['takerFilled'] for r in x),'unresolved':sum(r['unresolved'] for r in x)}
 oracle=[]
 for mid in mids:
  x=[r for r in rows if r['marketId']==mid];best=max(x,key=lambda r:(float(r['pnl']),-r['finalAbsNet']));oracle.append({'marketId':mid,'mode':best['mode'],'pnl':best['pnl'],'finalAbsNet':best['finalAbsNet']})
 rep={'version':'HFT_R2_CYCLE_HANDOFF_POLICY_PILOT_V1','researchOnly':True,'dreamFillAllowed':False,'frozenR2Authority':True,'markets':mids,'policies':['KEEP','WAIT','REPLACE','RETURN'],'rows':rows,'byPolicy':by,'perMarketOracle':oracle,'oraclePnl':sum(float(x['pnl']) for x in oracle),'interpretation':'System-level handoff ceiling only; oracle is offline hindsight and never runtime input.'};(OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf8');print(json.dumps({'ok':True,'byPolicy':by,'oracle':oracle,'oraclePnl':rep['oraclePnl']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
