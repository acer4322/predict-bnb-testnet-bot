from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v19_economic_ceiling_preposition as v19
except ImportError:v19=sib('v19detreserve','run_eth_repair_functional_exam_v19_economic_ceiling_preposition.py')
try:
 from tools import run_eth_repair_functional_exam_v18_reserve_builder_parent as v18
except ImportError:v18=sib('v18detreserve','run_eth_repair_functional_exam_v18_reserve_builder_parent.py')
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17detreserve','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16detreserve','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15detreserve','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13detreserve','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9detreserve','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
EPS=1e-9

class DeterministicReserveObligationSim(v19.EconomicCeilingReserveSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.deterministicReserveObligationActivations=0;self.deterministicReserveParentBirths=0;self.deterministicReserveAlreadyOwned=0
 def process(self,t):
  super().process(t);rb=self.reserveBuilder
  if rb is None or rb.get('firstFillAt') is None or rb.get('deterministicObligationActivated'):return
  ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None
  if weak is None:return
  if self.repairParent is None:
   b0=self.repairParentBirths;self._maybe_birth_parent(t,weak,{'repair_obligation_30s':1.0})
   if self.repairParentBirths>b0:self.deterministicReserveParentBirths+=1
  elif self.repairParent.get('side')==weak:self.deterministicReserveAlreadyOwned+=1
  if self.repairParent is not None and self.repairParent.get('side')==weak:
   rb['deterministicObligationActivated']=True;rb['deterministicObligationAt']=int(t);self.deterministicReserveObligationActivations+=1
 def _ensure_anchor(self,t,cs):
  rb=self.reserveBuilder
  if rb is not None and rb.get('firstFillAt') is not None and self.repairParent is not None:
   due=int(rb['firstFillAt'])+1
   self.anchorDueMs=due;self.anchorParentId=int(self.repairParent['id']);self.anchorEventN=int(self.capState.maker_n+self.capState.taker_n);self.anchorWasWait=(int(t)<due)
   return due
  return super()._ensure_anchor(t,cs)
 def run_exam_v20(self,models,winner):
  r=super().run_exam_v19(models,winner);r.update({'deterministicReserveObligationActivations':self.deterministicReserveObligationActivations,'deterministicReserveParentBirths':self.deterministicReserveParentBirths,'deterministicReserveAlreadyOwned':self.deterministicReserveAlreadyOwned});return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1829435');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v20_detreserve_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=DeterministicReserveObligationSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v20(models,cr['winner']);c=sim.causal()
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r,'causal':c});print(json.dumps({'marketId':mid,'firstFills':r['reserveFirstLegActualFill'],'detObligation':r['deterministicReserveObligationActivations'],'detBirths':r['deterministicReserveParentBirths'],'ceilingSubmit':r['ceilingRepairSubmits'],'firstLagMs':r['firstCeilingSubmitLagMs'],'cycles':r['reserveCycleCompletion'],'floorGain':r['reserveCycleFloorGainTotal'],'pairSumMax':r['reserveCyclePairSumMax'],'absNet':r['absNet'],'pnl':r['pnlDiagnosticOnly']}),flush=True)
  agg={k:sum(float(x['functional'].get(k) or 0) for x in rows) for k in ['reserveFirstLegActualFill','deterministicReserveObligationActivations','deterministicReserveParentBirths','ceilingRepairSubmits','reserveCycleCompletion','reserveCycleFloorGainTotal','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','unresolvedCarrierQty']};pairmax=max((float(x['functional']['reserveCyclePairSumMax']) for x in rows),default=0.);oppBefore=sum(int(x['causal']['oppositeBeforeFirstFill']) for x in rows);lags=[int(x['functional']['firstCeilingSubmitLagMs']) for x in rows if int(x['functional']['firstCeilingSubmitLagMs'])>=0];firstlag=min(lags,default=-1)
  gates={'reserveFirstLegActualFillExercised':agg['reserveFirstLegActualFill']>0,'deterministicObligationCoversAllFirstFills':agg['deterministicReserveObligationActivations']>=agg['reserveFirstLegActualFill'],'ceilingRepairSubmitExercised':agg['ceilingRepairSubmits']>0,'positiveCausalLagWithin2s':firstlag>0 and firstlag<=2000,'zeroOppositeBeforeFirstActualFill':oppBefore==0,'atLeastOneCheapCycleCompleted':agg['reserveCycleCompletion']>0 and pairmax>0 and pairmax<1.0,'floorIncreased':agg['reserveCycleFloorGainTotal']>0,'zeroRepairDrift':agg['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':agg['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':agg['overOwnedSubmitViolations']==0,'zeroUnresolved':abs(agg['unresolvedCarrierQty'])<=1e-9};out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V20_DETERMINISTIC_RESERVE_OBLIGATION','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':agg,'maxCompletedPairSum':pairmax,'firstCeilingSubmitLagMs':firstlag,'oppositeBeforeFirstFill':oppBefore,'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed HFT only','every controller-created Reserve first-leg fill creates deterministic second-leg responsibility','opposite submit only on a later receipt timestamp','economic ceiling preserved','no PnL tuning','no BTC numeric policy transfer']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
