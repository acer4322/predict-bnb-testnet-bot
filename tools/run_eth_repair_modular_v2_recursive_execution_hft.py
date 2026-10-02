from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;MID=1912961

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v89=sib('eth_v89b_for_modular_v2','run_eth_repair_v89b_overflow_parent_precap_carrier_hft.py')
v89d=sib('eth_v89d_for_modular_v2','run_eth_repair_v89d_two_active_composite_handoffs.py')
v84=v89.v84;v83=v84.v83;v80=v84.v80;v38=v89.v38;v1=v89.v1
router=sib('repair_execution_router_v2_runtime','repair_execution_router_v2.py')
RepairExecutionContext=router.RepairExecutionContextV2
REPAIR_EXECUTION_POLICY=router.RecursiveCompositeRepairExecutionRouterV2()

class ModularRecursiveRepairExecutionV2(v89.V89B):
 def _manager_debt_for_parent(self,pid,fallback):
  return max(0.0,float(fallback or 0.0))
 def __init__(self,*a,**kw):
  self.modularRepairExecutionEvents=[];self.modularActiveCompositeKeys=set();self.modularActiveCompositeSubmits=0
  self.repairExecutionRouter=kw.pop('repair_execution_router',None) or REPAIR_EXECUTION_POLICY
  super().__init__(*a,**kw)

 def _maybe_hard_active(self,t):
  rp=self.repairParent
  if rp is None:
   return v83.V83CleanModularCandidate._maybe_hard_active(self,t)
  pid=int(rp.get('id'));side=rp.get('side');born=int(rp.get('bornAt') or -1)
  # Ordinary parents remain exactly on the frozen V83/V36 execution route.
  if born not in self.v89OverflowBirthClocks:
   return v83.V83CleanModularCandidate._maybe_hard_active(self,t)
  churn=[x for x in self.repairChurn if int(x.get('parentId') or -1)==pid]
  base=float(self.armFillBase.get(pid,self._parent_actual_fill(pid)));now=float(self._parent_actual_fill(pid));progress=now>base+EPS
  pay=self._current_payoffs();qv=v1.quotes(self.book);ask=None;legal=None
  if qv and side in ('UP','DOWN') and side in qv and qv[side].get('ask') is not None:
   ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf
  manager_debt=self._manager_debt_for_parent(pid,float(pay['gap']))
  ctx=RepairExecutionContext(
   t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,parent_id=pid,parent_side=side,
   overflow_born_parent=True,armed=pid in self._armedParents,churn_count=len(churn),payment_progress_since_arm=progress,
   active_already_owned=pid in self.activeByParent,hard_confirmed=pid in self.hardConfirmed,
   floor=float(pay['floor']),manager_debt=float(manager_debt),live_ask=ask,legal_physical_qty=legal)
  pol=self.repairExecutionRouter;dec=pol.evaluate(ctx) if pol is not None else None
  ev={'t':int(t),'event':'MODULAR_REPAIR_EXECUTION_CHECK','parentId':pid,'bornAt':born,'side':side,'decision':(dec.reason if dec else 'NO_MODULE'),'allow':bool(dec.allow_active_handoff) if dec else False,'managerDebt':float(manager_debt),'floor':float(pay['floor']),'churnCount':len(churn),'paymentProgress':progress,'liveAsk':ask,'legalPhysicalQty':legal,'module':getattr(pol,'name',None)}
  # Avoid event spam: retain only material states / reason changes per parent.
  prev=next((x for x in reversed(self.modularRepairExecutionEvents) if x.get('parentId')==pid),None)
  if prev is None or prev.get('decision')!=ev['decision'] or ev['allow']:
   self.modularRepairExecutionEvents.append(dict(ev))
  if dec is None or not dec.allow_active_handoff:return False
  if not churn or ask is None:return False
  last=churn[-1];pk=str(last['key']);e=self.carrierLedger.get(pk,{})
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  self.hardConfirmed.add(pid);self.hardEventConfirmedCount+=1
  ok=self._submit_active(t,pid,pk,side,ask,float(dec.physical_qty),oid)
  if ok:
   ak=self.activeByParent[pid]['key'];self.modularActiveCompositeSubmits+=1;self.modularActiveCompositeKeys.add(ak);self.v84CompositeSubmits+=1
   self.v84Composite[ak]={'key':ak,'side':side,'parentId':pid,'price':ask,'submittedQty':float(dec.physical_qty),'gapAtSubmit':float(manager_debt),'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'MODULAR_V2_ACTIVE_COMPOSITE'}
   self.modularRepairExecutionEvents.append({'t':int(t),'event':'MODULAR_REPAIR_EXECUTION_ACTIVE_SUBMIT','parentId':pid,'key':ak,'side':side,'price':ask,'managerDebt':float(manager_debt),'physicalQty':float(dec.physical_qty),'module':getattr(pol,'name',None)})
  return bool(ok)

 def run_modular_v2(self,models,winner):
  r=self.run_v89b(models,winner);af=0.0
  for m in self.v84Composite.values():
   if m.get('lane')=='MODULAR_V2_ACTIVE_COMPOSITE':af+=float(m.get('fillSeen') or 0.0)
  prof={**self.policyProfile.describe(),'repairExecution':getattr(self.repairExecutionRouter,'name',type(self.repairExecutionRouter).__name__)}
  r.update({'modularRepairExecutionProfile':prof,'modularActiveCompositeSubmits':self.modularActiveCompositeSubmits,'modularActiveCompositeFillQty':af,'modularRepairExecutionEvents':self.modularRepairExecutionEvents[:240]});return r

def safety(r):
 legacy=int(r.get('overOwnedSubmitViolations') or 0);comps=int(r.get('v84CompositeSubmits') or 0)
 return {'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'unexplainedOverOwned':max(0,legacy-comps),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0),'preBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0)+float(r.get('v84PreBirthPaymentLeak') or 0),'duplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)+float(r.get('v84DuplicateDebt') or 0),'sharedOverfill':float(r.get('v36SharedRealizedOverfill') or 0)}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_modular_v2_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'MODULAR_V2_RECURSIVE_EXECUTION','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MODULAR_V2_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls,profile):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=profile)
  # Frozen bounded smoke is the control; candidate differs only by RepairExecutionRouter and removes the global two-handoff cap.
  b=mk(v89d.V89D,v80.economic_v1_profile())
  try:br=b.run_v89c(models,cr['winner'])
  finally:b.close()
  c=mk(ModularRecursiveRepairExecutionV2,v80.economic_v1_profile())
  try:rr=c.run_modular_v2(models,cr['winner'])
  finally:c.close()
  ss=safety(rr);conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  states=[m for m in (rr.get('v84CompositeState') or {}).values() if m.get('lane')=='MODULAR_V2_ACTIVE_COMPOSITE'];repair_bound=all(float(m.get('repairAllocated') or 0)<=float(m.get('gapAtSubmit') or 0)+1e-7 for m in states);active_parents=[int(m.get('parentId')) for m in states if float(m.get('submittedQty') or 0)>EPS]
  gates={'moduleProfileActive':(rr.get('modularRepairExecutionProfile') or {}).get('repairExecution')=='recursive_composite_single_disconnect_execution_v1','activeCompositeExercised':int(rr.get('modularActiveCompositeSubmits') or 0)>0,'oneActiveCompositePerResponsibility':len(active_parents)==len(set(active_parents)),'repairAllocationNeverExceedsManagerDebt':repair_bound,'physicalAllocationConservation':conservation,'zeroTruthMismatch':ss['truthMismatch']==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroRepairDrift':ss['repairDrift']==0,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS}
  decision='KEEP_MODULAR_V2_REPAIR_EXECUTION_FOR_FRESH_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_MODULAR_V2_REPAIR_EXECUTION'
  out={'version':'ETH_REPAIR_MODULAR_V2_RECURSIVE_EXECUTION_HFT','date':'2026-09-03','researchOnly':True,'marketId':MID,'decision':decision,'gates':gates,'safety':ss,'baselineV89D':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents'),'activeCompositeSubmits':br.get('v89cActiveCompositeSubmits'),'overflowPaid':br.get('v84OverflowPaidQty'),'overflowRemaining':br.get('v84OverflowRemainingQty'),'repairParentBirths':br.get('repairParentBirths')},'candidateModularV2':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'activeCompositeSubmits':rr.get('modularActiveCompositeSubmits'),'activeCompositeFillQty':rr.get('modularActiveCompositeFillQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements')},'moduleEvents':rr.get('modularRepairExecutionEvents',[]),'activeCompositeStates':states,'fullCandidate':rr,'boundary':['single module replacement: RepairExecutionRouter only','no fixed global handoff count','ordinary Repair execution inherits frozen V83/V36 route','no Target runtime input','no tuning','realistic HFT only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV89D'],'candidate':out['candidateModularV2']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
