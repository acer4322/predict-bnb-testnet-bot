from __future__ import annotations
import json, os, time
from pathlib import Path
import numpy as np
import worker_r4_adaptive_cycle_microworld_update_budget_v6 as v6

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
SEEDS=[89001,89002,89003]
POLICIES=['FIXED_24','CAPPED_20','SCALED_16_20_24','SCALED_12_20_24']

def budget(policy,g):
 if policy=='FIXED_24':return 24
 if policy=='CAPPED_20':return 20
 if policy=='SCALED_16_20_24':return 16 if g<.004 else 20 if g<.008 else 24
 if policy=='SCALED_12_20_24':return 12 if g<.004 else 20 if g<.008 else 24
 raise KeyError(policy)

v6.budget=budget

def main():
 t=time.perf_counter();runs=[]
 for fam in v6.FAMILIES:
  for seed in SEEDS:
   for pol in POLICIES:runs.append(v6.run_one(seed,fam,pol))
 summary={}
 for pol in POLICIES:
  rr=[x for x in runs if x['policy']==pol]
  summary[pol]={k:float(np.mean([x[k] for x in rr])) for k in ['finalMeanBA','finalMinBA','worstRecall','baseRepairForgetting','baseAddForgetting','proposals','accepted','attemptedSteps','acceptedSteps']}
 ref=summary['FIXED_24'];gate={'attemptedStepReductionMin':.10,'finalMeanBALossMax':.00075,'worstRecallLossMax':.005}
 decisions={}
 for pol in POLICIES:
  if pol=='FIXED_24':continue
  z=summary[pol];saving=1-z['attemptedSteps']/ref['attemptedSteps'];baLoss=ref['finalMeanBA']-z['finalMeanBA'];recLoss=ref['worstRecall']-z['worstRecall'];summary[pol]['attemptedStepReductionVsFixed24']=float(saving);summary[pol]['finalMeanBALossVsFixed24']=float(baLoss);summary[pol]['worstRecallLossVsFixed24']=float(recLoss);decisions[pol]='KEEP_CANDIDATE' if saving>=gate['attemptedStepReductionMin'] and baLoss<=gate['finalMeanBALossMax'] and recLoss<=gate['worstRecallLossMax'] else 'REJECT'
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_UPDATE_BUDGET_V7_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'worldFamilies':v6.FAMILIES,'seeds':SEEDS,'summary':summary,'preRegisteredKeepGate':gate,'decisions':decisions,'elapsedSec':time.perf_counter()-t,'guard':'Synthetic update-budget mechanics only. Any KEEP_CANDIDATE must still return unchanged to realistic-HFT/fresh chronology.','contract':'r4_adaptive_cycle_microworld_update_budget_v7_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
