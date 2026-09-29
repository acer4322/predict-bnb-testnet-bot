from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v20_deterministic_reserve_obligation as v20
except ImportError:v20=sib('v20for23','run_eth_repair_functional_exam_v20_deterministic_reserve_obligation.py')
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17for23','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16for23','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15for23','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13for23','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9for23','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9;INACTIVITY_MS=5000

class QueueProgressLeaseSim(v20.DeterministicReserveObligationSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.queueLeaseTracked=0;self.queueProgressEvents=0;self.queueLeaseExtensions=0;self.queueLeaseProtectedExpiryChecks=0;self.queueLeaseCancels=0;self.queueLease=[];self._qlease={}
 def _native_level_depth(self,o):
  side=o.get('side');p=float(o.get('price') or 0.)
  if side=='UP': return float(self.book.get('bids',{}).get(round(p,12),self.book.get('bids',{}).get(p,0.0)) or 0.0)
  if side=='DOWN':
   np=round(1.0-p,12);return float(self.book.get('asks',{}).get(np,self.book.get('asks',{}).get(1.0-p,0.0)) or 0.0)
  return 0.0
 def _is_reserve_second_leg(self,key,o):
  rb=self.reserveBuilder
  return bool(rb is not None and rb.get('firstFillAt') is not None and key in set(rb.get('repairKeys') or []) and o.get('side') in ('UP','DOWN'))
 def cancel_expired(self,t):
  # Same base 5s inactivity interval. Only deterministic Reserve second-leg carriers may refresh their lease on real public same-level depth depletion.
  for key,o in self.orders.items():
   s=self.snap(o)
   if not v1.live(s.get('status')):continue
   if self._is_reserve_second_leg(key,o):
    curd=self._native_level_depth(o);st=self._qlease.get(key)
    if st is None:
     st={'lastDepth':curd,'lastProgressAt':int(o.get('placed') or t),'placed':int(o.get('placed') or t),'progress':0,'extensions':0};self._qlease[key]=st;self.queueLeaseTracked+=1
    else:
     prev=float(st['lastDepth'])
     if curd<prev-EPS:
      st['lastProgressAt']=int(t);st['progress']+=1;self.queueProgressEvents+=1
      if int(t)-int(st['placed'])>=INACTIVITY_MS:st['extensions']+=1;self.queueLeaseExtensions+=1
     st['lastDepth']=curd
    age=int(t)-int(o.get('placed') or t);idle=int(t)-int(st['lastProgressAt'])
    if age>=INACTIVITY_MS and idle<INACTIVITY_MS:
     self.queueLeaseProtectedExpiryChecks+=1;continue
    if idle>=INACTIVITY_MS:
     if self._cancel_key(t,key):
      self.queueLeaseCancels+=1;self.queueLease.append({'key':key,'side':o.get('side'),'price':float(o.get('price') or 0.),'placed':int(o.get('placed') or 0),'cancelAt':int(t),'ageMs':age,'idleMs':idle,'progressEvents':int(st['progress']),'extensions':int(st['extensions'])})
    continue
   # Preserve original fixed TTL for every non-Reserve-second-leg order.
   if int(t)-int(o.get('placed') or t)>=INACTIVITY_MS:self._cancel_key(t,key)
 def run_exam_v23(self,models,winner):
  r=super().run_exam_v20(models,winner);r.update({'queueLeaseTracked':self.queueLeaseTracked,'queueProgressEvents':self.queueProgressEvents,'queueLeaseExtensions':self.queueLeaseExtensions,'queueLeaseProtectedExpiryChecks':self.queueLeaseProtectedExpiryChecks,'queueLeaseCancels':self.queueLeaseCancels,'queueLeaseEvents':self.queueLease[:50]});return r

def load_runtime(a):
 models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);return models,life,cap,tim,econ,price,sur

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1829435');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v23_queuelease_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];pair={}
   for name,cls,method in [('V20',v20.DeterministicReserveObligationSim,'run_exam_v20'),('V23',QueueProgressLeaseSim,'run_exam_v23')]:
    sim=cls(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
    try:r=getattr(sim,method)(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    pair[name]={'functional':r,'causal':c}
   rows.append({'marketId':mid,**pair});print(json.dumps({'marketId':mid,'v20First':pair['V20']['functional']['reserveFirstLegActualFill'],'v20Cycles':pair['V20']['functional']['reserveCycleCompletion'],'v23Cycles':pair['V23']['functional']['reserveCycleCompletion'],'tracked':pair['V23']['functional']['queueLeaseTracked'],'progress':pair['V23']['functional']['queueProgressEvents'],'extensions':pair['V23']['functional']['queueLeaseExtensions'],'protectedChecks':pair['V23']['functional']['queueLeaseProtectedExpiryChecks'],'leaseCancels':pair['V23']['functional']['queueLeaseCancels'],'v23PairMax':pair['V23']['functional']['reserveCyclePairSumMax']},ensure_ascii=False),flush=True)
  def sm(ver,k):return sum(float(x[ver]['functional'].get(k) or 0) for x in rows)
  base=sm('V20','reserveCycleCompletion');new=sm('V23','reserveCycleCompletion');first=sm('V23','reserveFirstLegActualFill');det=sm('V23','deterministicReserveObligationActivations');progress=sm('V23','queueProgressEvents');ext=sm('V23','queueLeaseExtensions');tracked=sm('V23','queueLeaseTracked');pairmax=max((float(x['V23']['functional'].get('reserveCyclePairSumMax') or 0) for x in rows),default=0.);opp=sum(int(x['V23']['causal']['oppositeBeforeFirstFill']) for x in rows);improved=sum(x['V23']['functional']['reserveCycleCompletion']>x['V20']['functional']['reserveCycleCompletion'] for x in rows);decreased=sum(x['V23']['functional']['reserveCycleCompletion']<x['V20']['functional']['reserveCycleCompletion'] for x in rows)
  baseline1829435=next((x for x in rows if x['marketId']==1829435),None);baselineFingerprint=(baseline1829435 is None or (baseline1829435['V20']['functional']['reserveFirstLegActualFill']==2 and baseline1829435['V20']['functional']['deterministicReserveObligationActivations']==2 and baseline1829435['V20']['functional']['reserveCycleCompletion']==1 and abs(float(baseline1829435['V20']['functional']['reserveCyclePairSumMax'])-.99)<1e-9))
  safety={'baselineFingerprintReproduced':baselineFingerprint,'deterministicObligationCoversAllFirstFills':det>=first,'zeroOppositeBeforeFirstActualFill':opp==0,'zeroRepairDrift':sm('V23','repairToExpandAtFirstFill')==0,'zeroTruthMismatch':sm('V23','authorizedSubmitWithTruthRoleMismatch')==0,'zeroOverOwned':sm('V23','overOwnedSubmitViolations')==0,'zeroUnresolved':abs(sm('V23','unresolvedCarrierQty'))<=1e-9,'completedPairSumLt1':new==0 or (pairmax>0 and pairmax<1.0)}
  behavior={'queueLeaseTracked':tracked>0,'progressPathExercisedOrNoProgress':progress>0 or tracked>0,'cyclesNotDecreased':new>=base,'noMarketCycleDecrease':decreased==0,'improvementRequiredForPromotion':improved>0}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V23_QUEUE_PROGRESS_LEASE','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':{'v20Cycles':base,'v23Cycles':new,'firstFills':first,'deterministicObligations':det,'queueLeaseTracked':tracked,'queueProgressEvents':progress,'queueLeaseExtensions':ext,'improvedMarkets':improved,'decreasedMarkets':decreased,'v23MaxCompletedPairSum':pairmax},'safetyGates':safety,'behaviorGates':behavior,'smokeVerified':all(safety.values()) and all(behavior.values()),'rows':rows,'boundary':['consumed realistic HFT only','V20 deterministic responsibility preserved','same 5s inactivity interval; public same-price depth-decrease progress may refresh Reserve second-leg lease','no best-bid chase','economic ceiling unchanged','no BTC numeric transfer','no PnL tuning']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':out['aggregate'],'safety':safety,'behavior':behavior},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
