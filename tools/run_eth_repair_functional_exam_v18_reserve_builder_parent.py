from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17reserve','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v14_strict_lane_source as v14
except ImportError:v14=sib('v14reserve','run_eth_repair_functional_exam_v14_strict_lane_source.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15reserve','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16reserve','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13reserve','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9reserve','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class ReserveBuilderSim(v17.PostSafeSurplusSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.reserveBuilder=None;self.nextReserveBuilderId=1
  self.packageOpportunityTicks=0;self.reserveFirstLegSubmit=0;self.reserveFirstLegActualFill=0;self.packageRepairEval=0;self.packageRepairAccept=0;self.packageRepairBlock=0;self.packageRepairFallbackEval=0;self.reserveCycleCompletion=0;self.reserveCycleFloorGain=[];self.reserveCyclePairSums=[];self.reserveFirstKeys=[];self.reserveOppositeBeforeFirstFill=0
 def _thin_balanced_package(self,qv):
  floor,u,d,cost=self._raw_floor();g=u+d;absr=abs(u-d)/g if g>EPS else 0.;s=float(qv['UP']['bid'])+float(qv['DOWN']['bid'])
  return floor,u,d,cost,absr,s
 def _start_reserve_builder(self,t,qv):
  floor,u,d,cost,absr,s=self._thin_balanced_package(qv)
  if self.reserveBuilder is not None or self.repairParent is not None or self.outstanding_total()>EPS:return False
  if not (0<=floor<1.0 and absr<=.02 and s<1.-1e-9):return False
  self.packageOpportunityTicks+=1
  pu=float(qv['UP']['bid']);pd=float(qv['DOWN']['bid']);side='UP' if pu<pd-EPS else 'DOWN' if pd<pu-EPS else ('UP' if (self.n%2==0) else 'DOWN');p=float(qv[side]['bid']);q=1/p if p>EPS else 1e9
  if q<=EPS or q>12.+EPS:return False
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n;self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='RESERVE_BUILD_FIRST';ok=self.submit(t,side,p,q)
  if ok:
   key=f'{side}_{n0}';self.reserveFirstLegSubmit+=1;self.reserveFirstKeys.append(key);self.reserveBuilder={'id':self.nextReserveBuilderId,'firstKey':key,'firstSide':side,'firstPrice':p,'firstQty':q,'submittedAt':int(t),'firstFillAt':None,'floorBefore':floor,'pairDeadline':None,'completed':False};self.nextReserveBuilderId+=1
  return bool(ok)
 def process(self,t):
  super().process(t);rb=self.reserveBuilder
  if not rb:return
  key=rb['firstKey'];e=self.carrierLedger.get(key)
  if rb['firstFillAt'] is None and e and float(e.get('actualFilled',0.))>EPS:
   rb['firstFillAt']=int(t);rb['firstFilledQty']=float(e.get('actualFilled',0.));rb['pairDeadline']=int(t)+30000;self.reserveFirstLegActualFill+=1
   rem=self._ledger_remaining(e)
   if rem>EPS and not e.get('cancelRequested'):self._cancel_key(t,key)
  if rb.get('firstFillAt') is not None and not rb.get('completed'):
   floor,u,d,cost=self._raw_floor();g=u+d;absr=abs(u-d)/g if g>EPS else 0.
   if floor>rb['floorBefore']+1e-9 and absr<=.02 and self.repairParent is None and self.outstanding_total()<=EPS:
    rb['completed']=True;self.reserveCycleCompletion+=1;self.reserveCycleFloorGain.append(float(floor-rb['floorBefore']));
    if rb.get('repairFillPrice') is not None:self.reserveCyclePairSums.append(float(rb['firstPrice']+rb['repairFillPrice']))
    self.reserveBuilder=None
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is None:return False
  side,qty,oldp,role,oid=z;rb=self.reserveBuilder
  if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None:
   p=float(qv[side]['bid'])
   if int(t)<=int(rb['pairDeadline']):
    self.packageRepairEval+=1
    if rb['firstPrice']+p>=1.-1e-9:self.packageRepairBlock+=1;return False
    self.packageRepairAccept+=1;rb['repairFillPrice']=p
    return v14.StrictLaneSourceSim._submit_authorized(self,t,qv,z,roles_this_tick)
   else:self.packageRepairFallbackEval+=1
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_exam_v18(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1]);global_active=pa>=models['actionTh'];qty=max(.01,float(__import__('numpy').expm1(__import__('numpy').clip(models['qty'].predict(x)[0],0,5))));qty=min(qty,12.)
   self.repairSupervisorTicks+=1;self._refresh_carrier_ledger(t);self._reconcile_objective(t);self._continuous_capacity_reconcile(t)
   ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;cs=self.capState.snapshot(t,end)
   if weak is not None and self.repairParent is None:
    precap=self.capability.predict(cs);b0=self.repairParentBirths;self._maybe_birth_parent(t,weak,precap)
    if self.repairParentBirths>b0 and not global_active:self.repairParentBirthsBelowActionGate+=1
   if self.repairParent is None or weak is None:self._clear_anchor()
   roles=set();submitted=0
   if weak is not None and self.repairParent is not None:
    if self.lane_unresolved('REPAIR'):self._clear_anchor()
    else:
     due=self._ensure_anchor(t,self.capState.snapshot(t,end))
     if due is not None and t<due:self.timingWaitTicks+=1;self.timingParentPersistsDuringWaitTicks+=1
     else:
      if self.anchorWasWait:self.timingDeadlineFires+=1;self.timingWaitToNowTransitions+=1;self.anchorWasWait=False
      self.repairProposalTicks+=1;p=float(qv[weak]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,weak,min(q,12.));ok=self._submit_authorized(t,qv,z,roles);submitted+=int(ok)
      if ok:self.timingRepairSubmits+=1;self._clear_anchor()
   elif global_active and self.reserveBuilder is None and self.outstanding_total()<=EPS:
    self._start_reserve_builder(t,qv)
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self.process(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'unresolvedCarrierQty':self.outstanding_total(),'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'packageOpportunityTicks':self.packageOpportunityTicks,'reserveFirstLegSubmit':self.reserveFirstLegSubmit,'reserveFirstLegActualFill':self.reserveFirstLegActualFill,'packageRepairEval':self.packageRepairEval,'packageRepairAccept':self.packageRepairAccept,'packageRepairBlock':self.packageRepairBlock,'packageRepairFallbackEval':self.packageRepairFallbackEval,'reserveCycleCompletion':self.reserveCycleCompletion,'reserveCycleFloorGainTotal':sum(self.reserveCycleFloorGain),'reserveCycleFloorGainMin':min(self.reserveCycleFloorGain,default=0.),'reserveCyclePairSumMax':max(self.reserveCyclePairSums,default=0.),'reserveCyclePairSumMean':sum(self.reserveCyclePairSums)/len(self.reserveCyclePairSums) if self.reserveCyclePairSums else 0.,'crossLaneExpandFallbackBlocks':self.crossLaneExpandFallbackBlocks,'crossLaneRepairFallbackBlocks':self.crossLaneRepairFallbackBlocks}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1829435');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v18_reserve_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=ReserveBuilderSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v18(models,cr['winner']);c=sim.causal()
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r,'causal':c});print(json.dumps({'marketId':mid,'opportunity':r['packageOpportunityTicks'],'firstSubmit':r['reserveFirstLegSubmit'],'firstFill':r['reserveFirstLegActualFill'],'repairEval':r['packageRepairEval'],'repairAccept':r['packageRepairAccept'],'repairBlock':r['packageRepairBlock'],'cycles':r['reserveCycleCompletion'],'floorGain':r['reserveCycleFloorGainTotal'],'pairSumMax':r['reserveCyclePairSumMax'],'pnl':r['pnlDiagnosticOnly']}),flush=True)
  agg={k:sum(float(x['functional'].get(k) or 0) for x in rows) for k in ['packageOpportunityTicks','reserveFirstLegSubmit','reserveFirstLegActualFill','packageRepairEval','packageRepairAccept','packageRepairBlock','packageRepairFallbackEval','reserveCycleCompletion','reserveCycleFloorGainTotal','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','unresolvedCarrierQty']};pairmax=max((float(x['functional']['reserveCyclePairSumMax']) for x in rows),default=0.);oppBefore=sum(int(x['causal']['oppositeBeforeFirstFill']) for x in rows);gates={'opportunityExercised':agg['packageOpportunityTicks']>0,'firstLegSubmitExercised':agg['reserveFirstLegSubmit']>0,'firstLegActualFillExercised':agg['reserveFirstLegActualFill']>0,'zeroOppositeBeforeFirstActualFill':oppBefore==0,'packageRepairEvaluated':agg['packageRepairEval']>0,'packageRepairAccepted':agg['packageRepairAccept']>0,'cycleCompleted':agg['reserveCycleCompletion']>0,'completedPairSumLt1':pairmax>0 and pairmax<1.,'floorIncreased':agg['reserveCycleFloorGainTotal']>0,'zeroRepairDrift':agg['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':agg['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':agg['overOwnedSubmitViolations']==0,'zeroUnresolved':abs(agg['unresolvedCarrierQty'])<=1e-9};out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V18_RESERVE_BUILDER_PARENT','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':agg,'maxCompletedPairSum':pairmax,'oppositeBeforeFirstFill':oppBefore,'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed HFT only','one first leg only','actual fill required before Repair','package repair pairSum<1 for 30s','30s timeout falls back to V16 Repair','no PnL tuning']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
