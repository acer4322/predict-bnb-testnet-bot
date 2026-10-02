from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v18_reserve_builder_parent as v18
except ImportError:v18=sib('v18ceiling','run_eth_repair_functional_exam_v18_reserve_builder_parent.py')
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17ceiling','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16ceiling','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15ceiling','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v14_strict_lane_source as v14
except ImportError:v14=sib('v14ceiling','run_eth_repair_functional_exam_v14_strict_lane_source.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13ceiling','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9ceiling','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class EconomicCeilingReserveSim(v18.ReserveBuilderSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.ceilingRepairSubmits=0;self.ceilingRepairReprices=0;self.ceilingPrices=[];self.ceilingSubmitLags=[];self._seenFillTrace=0
 def _ensure_anchor(self,t,cs):
  rb=self.reserveBuilder
  if rb is not None and rb.get('firstFillAt') is not None and self.repairParent is not None:
   # Reserve-package Repair has its own execution clock: responsibility is live immediately after actual first-leg fill.
   self.anchorDueMs=int(t);self.anchorParentId=int(self.repairParent['id']);self.anchorEventN=int(self.capState.maker_n+self.capState.taker_n);self.anchorWasWait=False
   return int(t)
  return super()._ensure_anchor(t,cs)
 def _package_price(self,qv,side,rb):
  ceiling=1.0-float(rb['firstPrice'])-0.01
  ask=float(qv[side]['ask'])
  raw=min(ceiling,ask-0.01)
  p=math.floor((raw+1e-10)*100.0)/100.0
  if p<=0.0 or p>=ask-EPS or float(rb['firstPrice'])+p>=1.0-EPS:return None
  return p
 def _submit_package_at_price(self,t,qv,z,roles_this_tick,p):
  if z is None:return False
  side,qty,_,role,oid=z
  if role!='REPAIR' or role in roles_this_tick:return False
  legal=1.0/p if p>EPS else 1e9
  if qty<legal-EPS:self.remainingCapBlocks+=1;return False
  qty=min(max(float(qty),legal),12.0)
  self._pendingAuthorizedRole=role;self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=self.repairParent['id'] if self.repairParent else None;self._pendingLane=role
  n0=self.n;ok=self.submit(t,side,p,qty)
  if ok:
   roles_this_tick.add(role);self.ceilingRepairSubmits+=1;self.ceilingPrices.append(float(p));rb=self.reserveBuilder
   if rb is not None:
    if rb.get('firstCeilingSubmitAt') is None:
     rb['firstCeilingSubmitAt']=int(t);self.ceilingSubmitLags.append(int(t)-int(rb['firstFillAt']))
    else:self.ceilingRepairReprices+=1
    rb.setdefault('repairKeys',[]).append(f'{side}_{n0}')
  return bool(ok)
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is None:return False
  side,qty,oldp,role,oid=z;rb=self.reserveBuilder
  if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None and int(t)<=int(rb['pairDeadline']):
   self.packageRepairEval+=1;p=self._package_price(qv,side,rb)
   if p is None:self.packageRepairBlock+=1;return False
   ok=self._submit_package_at_price(t,qv,z,roles_this_tick,p)
   if ok:self.packageRepairAccept+=1
   else:self.packageRepairBlock+=1
   return ok
  if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None:self.packageRepairFallbackEval+=1
  return v18.v17.PostSafeSurplusSim._submit_authorized(self,t,qv,z,roles_this_tick)
 def process(self,t):
  # Run parent simulator processing, then evaluate first-leg and opposite-leg actual fills before completion bookkeeping.
  v18.v17.PostSafeSurplusSim.process(self,t)
  rb=self.reserveBuilder
  if not rb:return
  newfills=self.fillTrace[self._seenFillTrace:];self._seenFillTrace=len(self.fillTrace)
  key=rb['firstKey'];e=self.carrierLedger.get(key)
  if rb.get('firstFillAt') is None and e and float(e.get('actualFilled',0.0))>EPS:
   rb['firstFillAt']=int(t);rb['firstFilledQty']=float(e.get('actualFilled',0.0));rb['pairDeadline']=int(t)+30000;self.reserveFirstLegActualFill+=1
   rem=self._ledger_remaining(e)
   if rem>EPS and not e.get('cancelRequested'):self._cancel_key(t,key)
  if rb.get('firstFillAt') is not None:
   opp='DOWN' if rb['firstSide']=='UP' else 'UP'
   for f in newfills:
    if int(f['t'])>=int(rb['firstFillAt']) and f['side']==opp:
     rb['repairFillPrice']=float(f['price']);rb['repairFillAt']=int(f['t']);break
  if rb.get('firstFillAt') is not None and not rb.get('completed'):
   floor,u,d,cost=self._raw_floor();g=u+d;absr=abs(u-d)/g if g>EPS else 0.0
   if floor>float(rb['floorBefore'])+1e-9 and absr<=.02 and self.repairParent is None and self.outstanding_total()<=EPS:
    rb['completed']=True;self.reserveCycleCompletion+=1;self.reserveCycleFloorGain.append(float(floor-rb['floorBefore']))
    if rb.get('repairFillPrice') is not None:self.reserveCyclePairSums.append(float(rb['firstPrice']+rb['repairFillPrice']))
    self.reserveBuilder=None
 def run_exam_v19(self,models,winner):
  r=super().run_exam_v18(models,winner)
  r.update({'ceilingRepairSubmits':self.ceilingRepairSubmits,'ceilingRepairReprices':self.ceilingRepairReprices,'ceilingPriceMean':sum(self.ceilingPrices)/len(self.ceilingPrices) if self.ceilingPrices else 0.0,'ceilingPriceMin':min(self.ceilingPrices,default=0.0),'ceilingPriceMax':max(self.ceilingPrices,default=0.0),'firstCeilingSubmitLagMs':min(self.ceilingSubmitLags,default=-1)})
  return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1829435');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v19_ceiling_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=EconomicCeilingReserveSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v19(models,cr['winner']);c=sim.causal()
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r,'causal':c});print(json.dumps({'marketId':mid,'firstFill':r['reserveFirstLegActualFill'],'ceilingSubmit':r['ceilingRepairSubmits'],'firstLagMs':r['firstCeilingSubmitLagMs'],'cycles':r['reserveCycleCompletion'],'floorGain':r['reserveCycleFloorGainTotal'],'pairSumMax':r['reserveCyclePairSumMax'],'pnl':r['pnlDiagnosticOnly']}),flush=True)
  agg={k:sum(float(x['functional'].get(k) or 0) for x in rows) for k in ['packageOpportunityTicks','reserveFirstLegSubmit','reserveFirstLegActualFill','packageRepairEval','packageRepairAccept','packageRepairBlock','reserveCycleCompletion','reserveCycleFloorGainTotal','ceilingRepairSubmits','ceilingRepairReprices','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','unresolvedCarrierQty']};pairmax=max((float(x['functional']['reserveCyclePairSumMax']) for x in rows),default=0.);oppBefore=sum(int(x['causal']['oppositeBeforeFirstFill']) for x in rows);firstlag=min((int(x['functional']['firstCeilingSubmitLagMs']) for x in rows if int(x['functional']['firstCeilingSubmitLagMs'])>=0),default=-1)
  gates={'firstLegActualFillExercised':agg['reserveFirstLegActualFill']>0,'ceilingRepairSubmitExercised':agg['ceilingRepairSubmits']>0,'ceilingSubmitWithin2sOfFirstFill':firstlag>=0 and firstlag<=2000,'zeroOppositeBeforeFirstActualFill':oppBefore==0,'cycleCompleted':agg['reserveCycleCompletion']>0,'completedPairSumLt1':pairmax>0 and pairmax<1.0,'floorIncreased':agg['reserveCycleFloorGainTotal']>0,'zeroRepairDrift':agg['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':agg['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':agg['overOwnedSubmitViolations']==0,'zeroUnresolved':abs(agg['unresolvedCarrierQty'])<=1e-9};out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V19_ECONOMIC_CEILING_PREPOSITION','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':agg,'maxCompletedPairSum':pairmax,'firstCeilingSubmitLagMs':firstlag,'oppositeBeforeFirstFill':oppBefore,'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed HFT only','first leg actual-fill before opposite submit','economic ceiling = 1-firstLegPrice-0.01','passive price <= ask-0.01','TTL retry preserves ceiling','no PnL tuning','no BTC numeric transfer']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
