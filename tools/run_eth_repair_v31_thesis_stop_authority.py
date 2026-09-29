from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_repair_functional_exam_v30_directional_thesis_cycle as v30
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
EPS=1e-9
POLICIES=('STOP_AFTER_FIRST_RECOVERY','MAX_TWO_OPENS','SIGNAL_ONLY_UNLIMITED','SIGNAL_WAIT_10S')

class StopPolicySim(v30.DirectionalThesisCycleSim):
 def __init__(self,*a,stop_policy='SIGNAL_ONLY_UNLIMITED',**kw):
  super().__init__(*a,**kw);self.stopPolicy=stop_policy;self.stopBlocks=0;self.stopTimeoutBlocks=0;self.stopCycleBlocks=0
 def _maybe_reexpand_after_recovery(self,t):
  if self.lastPayoffRecoveryAt is None:return False
  if self.stopPolicy=='STOP_AFTER_FIRST_RECOVERY':
   self.stopBlocks+=1;self.stopCycleBlocks+=1;return False
  if self.stopPolicy=='MAX_TWO_OPENS':
   th=self.thesis
   if th is not None and int(th.get('opens') or 0)>=2:
    self.stopBlocks+=1;self.stopCycleBlocks+=1;return False
   return super()._maybe_reexpand_after_recovery(t)
  if self.stopPolicy=='SIGNAL_WAIT_10S':
   if int(t)-int(self.lastPayoffRecoveryAt)>10000:
    self.stopBlocks+=1;self.stopTimeoutBlocks+=1;return False
   return super()._maybe_reexpand_after_recovery(t)
  return super()._maybe_reexpand_after_recovery(t)
 def run_exam_v31(self,models,winner):
  r=super().run_exam_v30(models,winner);floor,u,d,cost=self._raw_floor();r.update({'stopPolicy':self.stopPolicy,'bestPayoff':max(u,d)-cost,'worstPayoff':floor,'finalUpPayoff':u-cost,'finalDownPayoff':d-cost,'stopBlocks':self.stopBlocks,'stopTimeoutBlocks':self.stopTimeoutBlocks,'stopCycleBlocks':self.stopCycleBlocks});return r

def load_runtime(a):
 models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);return models,life,cap,tim,econ,price,sur

def summarize(rows):
 n=len(rows);safe=sum(float(x['functional']['worstPayoff'])>=-EPS for x in rows);active=[x for x in rows if float(x['functional'].get('buyNotional') or 0)>EPS]
 def sm(k):return sum(float(x['functional'].get(k) or 0) for x in rows)
 return {'markets':n,'activeMarkets':len(active),'safeTerminalRate':safe/n if n else None,'terminalWorstPayoffSum':sm('worstPayoff'),'terminalBestPayoffSum':sm('bestPayoff'),'meanTerminalWorstPayoff':sm('worstPayoff')/n if n else None,'meanTerminalBestPayoff':sm('bestPayoff')/n if n else None,'payoffRepairCompletions':sm('payoffRepairCompletions'),'reexpandSubmits':sm('reexpandSubmits'),'reexpandActualFills':sm('reexpandActualFills'),'thesisCycleCompletions':sm('thesisCycleCompletions'),'signalHoldBlocks':sm('signalHoldBlocks'),'stopBlocks':sm('stopBlocks'),'stopTimeoutBlocks':sm('stopTimeoutBlocks'),'stopCycleBlocks':sm('stopCycleBlocks'),'repairToExpandAtFirstFill':sm('repairToExpandAtFirstFill'),'truthMismatch':sm('authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('overOwnedSubmitViolations'),'unresolvedCarrierQty':sm('unresolvedCarrierQty'),'pnlDiagnosticOnly':sm('pnlDiagnosticOnly')}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--policies',default=','.join(POLICIES));ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v31_stop_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);ids=[int(x) for x in a.market_ids.split(',') if x.strip()];policies=[x.strip() for x in a.policies.split(',') if x.strip()];rows=[]
  for pol in policies:
   if pol not in POLICIES:raise ValueError(pol)
   for mid in ids:
    cr=by[mid];sim=StopPolicySim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,stop_policy=pol)
    try:r=sim.run_exam_v31(models,cr['winner'])
    finally:sim.close()
    rows.append({'policy':pol,'marketId':mid,'functional':r});print(json.dumps({'policy':pol,'marketId':mid,'recoveries':r['payoffRepairCompletions'],'reexpandFills':r['reexpandActualFills'],'cycles':r['thesisCycleCompletions'],'floor':r['worstPayoff'],'best':r['bestPayoff'],'absNet':r['absNet'],'stopBlocks':r['stopBlocks']},ensure_ascii=False),flush=True)
  summaries={p:summarize([x for x in rows if x['policy']==p]) for p in policies}
  safety={p:{'zeroRepairDrift':summaries[p]['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':summaries[p]['truthMismatch']==0,'zeroOverOwned':summaries[p]['overOwned']==0,'zeroUnresolved':abs(summaries[p]['unresolvedCarrierQty'])<=EPS} for p in policies}
  out={'version':'ETH_REPAIR_V31_THESIS_STOP_AUTHORITY','researchOnly':True,'parent':'Frozen V30 core','selectedMarketIds':ids,'policies':policies,'summaries':summaries,'safety':safety,'rows':rows,'boundary':['consumed realistic HFT only','winner/PnL diagnostic only; not selection authority','only re-expand stopping authority differs','no Taker','no direction/Repair/price/queue retuning','10s fixed from Target ETH Maker TEST20 p75 before OUR comparison']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summaries':summaries,'safety':safety},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
