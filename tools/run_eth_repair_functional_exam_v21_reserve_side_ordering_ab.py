from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v20_deterministic_reserve_obligation as v20
except ImportError:v20=sib('v20sideab','run_eth_repair_functional_exam_v20_deterministic_reserve_obligation.py')
try:
 from tools import run_eth_repair_functional_exam_v18_reserve_builder_parent as v18
except ImportError:v18=sib('v18sideab','run_eth_repair_functional_exam_v18_reserve_builder_parent.py')
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17sideab','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16sideab','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15sideab','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13sideab','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9sideab','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
EPS=1e-9

class HigherPriceFirstReserveSim(v20.DeterministicReserveObligationSim):
 def _start_reserve_builder(self,t,qv):
  floor,u,d,cost,absr,s=self._thin_balanced_package(qv)
  if self.reserveBuilder is not None or self.repairParent is not None or self.outstanding_total()>EPS:return False
  if not (0<=floor<1.0 and absr<=.02 and s<1.-1e-9):return False
  self.packageOpportunityTicks+=1
  pu=float(qv['UP']['bid']);pd=float(qv['DOWN']['bid']);side='UP' if pu>pd+EPS else 'DOWN' if pd>pu+EPS else ('UP' if (self.n%2==0) else 'DOWN');p=float(qv[side]['bid']);q=1/p if p>EPS else 1e9
  if q<=EPS or q>12.+EPS:return False
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n;self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='RESERVE_BUILD_FIRST';ok=self.submit(t,side,p,q)
  if ok:
   key=f'{side}_{n0}';self.reserveFirstLegSubmit+=1;self.reserveFirstKeys.append(key);self.reserveBuilder={'id':self.nextReserveBuilderId,'firstKey':key,'firstSide':side,'firstPrice':p,'firstQty':q,'submittedAt':int(t),'firstFillAt':None,'floorBefore':floor,'pairDeadline':None,'completed':False};self.nextReserveBuilderId+=1
  return bool(ok)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1829435');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v21_sideab_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=HigherPriceFirstReserveSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v20(models,cr['winner']);c=sim.causal()
   finally:sim.close()
   first_res=[x for x in c.get('submitTrace',[]) if x.get('lane')=='RESERVE_BUILD_FIRST'];rows.append({'marketId':mid,'functional':r,'causal':c,'reserveFirstLegSubmits':first_res});print(json.dumps({'marketId':mid,'firstFills':r['reserveFirstLegActualFill'],'detObligation':r['deterministicReserveObligationActivations'],'ceilingSubmit':r['ceilingRepairSubmits'],'firstLagMs':r['firstCeilingSubmitLagMs'],'cycles':r['reserveCycleCompletion'],'floorGain':r['reserveCycleFloorGainTotal'],'pairSumMax':r['reserveCyclePairSumMax'],'absNet':r['absNet'],'pnl':r['pnlDiagnosticOnly']}),flush=True)
  agg={k:sum(float(x['functional'].get(k) or 0) for x in rows) for k in ['reserveFirstLegActualFill','deterministicReserveObligationActivations','ceilingRepairSubmits','reserveCycleCompletion','reserveCycleFloorGainTotal','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','unresolvedCarrierQty']};pairmax=max((float(x['functional']['reserveCyclePairSumMax']) for x in rows),default=0.);oppBefore=sum(int(x['causal']['oppositeBeforeFirstFill']) for x in rows);lags=[int(x['functional']['firstCeilingSubmitLagMs']) for x in rows if int(x['functional']['firstCeilingSubmitLagMs'])>=0];firstlag=min(lags,default=-1)
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V21_RESERVE_SIDE_ORDERING_AB','policy':'HIGHER_PRICE_FIRST','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':agg,'maxCompletedPairSum':pairmax,'firstCeilingSubmitLagMs':firstlag,'oppositeBeforeFirstFill':oppBefore,'rows':rows,'boundary':['consumed HFT only','only first-leg side ordering differs from V20','higher bid side chosen first','deterministic second-leg responsibility unchanged','economic ceiling unchanged','no PnL tuning','no BTC numeric policy transfer']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'pairmax':pairmax,'firstlag':firstlag,'oppBefore':oppBefore},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
