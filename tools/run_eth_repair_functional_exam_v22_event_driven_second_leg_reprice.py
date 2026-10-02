from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v20_deterministic_reserve_obligation as v20
except ImportError:v20=sib('v20for22','run_eth_repair_functional_exam_v20_deterministic_reserve_obligation.py')
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17for22','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16for22','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15for22','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13for22','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9for22','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class EventDrivenSecondLegRepriceSim(v20.DeterministicReserveObligationSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.eventDrivenRepriceChecks=0;self.eventDrivenStaleSupported=0;self.eventDrivenRepriceRequests=0;self.eventDrivenRepriceEvents=[]
 def _maybe_event_reprice(self,t):
  rb=self.reserveBuilder
  if rb is None or rb.get('firstFillAt') is None or rb.get('pairDeadline') is None or int(t)>int(rb['pairDeadline']) or self.repairParent is None:return
  qv=v1.quotes(self.book)
  if not qv:return
  side=self.repairParent.get('side')
  if side not in ('UP','DOWN'):return
  desired=self._package_price(qv,side,rb);self.eventDrivenRepriceChecks+=1
  if desired is None:return
  rows=[(k,e,rem) for k,e,rem in self.unresolved(side=side) if e.get('objectiveRole')=='REPAIR' and not e.get('cancelRequested')]
  for key,e,rem in rows:
   o=self.orders.get(key)
   if o is None:continue
   old=float(o.get('price') or 0.)
   if desired>=old+0.01-EPS:
    self.eventDrivenStaleSupported+=1
    if self._cancel_key(t,key):
     self.eventDrivenRepriceRequests+=1;self.eventDrivenRepriceEvents.append({'t':int(t),'key':key,'side':side,'oldPrice':old,'desiredPrice':float(desired),'firstPrice':float(rb['firstPrice']),'ceiling':float(1.0-rb['firstPrice']-0.01),'remainingQty':float(rem)});return
 def process(self,t):
  super().process(t);self._maybe_event_reprice(t)
 def run_exam_v22(self,models,winner):
  r=super().run_exam_v20(models,winner);r.update({'eventDrivenRepriceChecks':self.eventDrivenRepriceChecks,'eventDrivenStaleSupported':self.eventDrivenStaleSupported,'eventDrivenRepriceRequests':self.eventDrivenRepriceRequests,'eventDrivenRepriceEvents':self.eventDrivenRepriceEvents});return r

def load_runtime(a):
 models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);return models,life,cap,tim,econ,price,sur

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1829435,1823769,1824301,1824758');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v22_event_reprice_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];pair={}
   for name,cls,method in [('V20',v20.DeterministicReserveObligationSim,'run_exam_v20'),('V22',EventDrivenSecondLegRepriceSim,'run_exam_v22')]:
    sim=cls(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
    try:r=getattr(sim,method)(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    pair[name]={'functional':r,'causal':c}
   rows.append({'marketId':mid,**pair});print(json.dumps({'marketId':mid,'v20Cycles':pair['V20']['functional']['reserveCycleCompletion'],'v22Cycles':pair['V22']['functional']['reserveCycleCompletion'],'staleSupport':pair['V22']['functional']['eventDrivenStaleSupported'],'repriceRequests':pair['V22']['functional']['eventDrivenRepriceRequests'],'v20PairMax':pair['V20']['functional']['reserveCyclePairSumMax'],'v22PairMax':pair['V22']['functional']['reserveCyclePairSumMax']},ensure_ascii=False),flush=True)
  def sm(ver,k):return sum(float(x[ver]['functional'].get(k) or 0) for x in rows)
  baseCycles=sm('V20','reserveCycleCompletion');newCycles=sm('V22','reserveCycleCompletion');support=sm('V22','eventDrivenStaleSupported');req=sm('V22','eventDrivenRepriceRequests');improved=sum(x['V22']['functional']['reserveCycleCompletion']>x['V20']['functional']['reserveCycleCompletion'] for x in rows);decreased=sum(x['V22']['functional']['reserveCycleCompletion']<x['V20']['functional']['reserveCycleCompletion'] for x in rows);pairmax=max((float(x['V22']['functional'].get('reserveCyclePairSumMax') or 0) for x in rows),default=0.);oppBefore=sum(int(x['V22']['causal']['oppositeBeforeFirstFill']) for x in rows)
  firstfills=sm('V22','reserveFirstLegActualFill');det=sm('V22','deterministicReserveObligationActivations');safety={'deterministicObligationCoversAllFirstFills':det>=firstfills,'zeroOppositeBeforeFirstActualFill':oppBefore==0,'zeroRepairDrift':sm('V22','repairToExpandAtFirstFill')==0,'zeroTruthMismatch':sm('V22','authorizedSubmitWithTruthRoleMismatch')==0,'zeroOverOwned':sm('V22','overOwnedSubmitViolations')==0,'zeroUnresolved':abs(sm('V22','unresolvedCarrierQty'))<=1e-9,'completedPairSumLt1':newCycles==0 or (pairmax>0 and pairmax<1.0)}
  behavior={'staleRepricePathExercisedOrNoSupport':req>0 or support==0,'cyclesNotDecreased':newCycles>=baseCycles,'noMarketCycleDecrease':decreased==0,'improvedAtLeastOneIfSupported':(support==0) or improved>0}
  gates={**safety,**behavior};out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V22_EVENT_DRIVEN_SECOND_LEG_REPRICE','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':{'v20Cycles':baseCycles,'v22Cycles':newCycles,'staleSupport':support,'repriceRequests':req,'improvedMarkets':improved,'decreasedMarkets':decreased,'v22MaxCompletedPairSum':pairmax,'v22FirstFills':firstfills,'v22DeterministicObligations':det},'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed realistic HFT only','V20 deterministic responsibility preserved','event-driven upward stale reprice only; no TTL sweep','economic ceiling unchanged','no learned fillability gate','no BTC numeric transfer','no PnL tuning']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':out['aggregate'],'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
