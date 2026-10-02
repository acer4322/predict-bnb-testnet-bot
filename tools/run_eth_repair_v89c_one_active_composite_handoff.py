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
v89=sib('eth_v89b_for_v89c','run_eth_repair_v89b_overflow_parent_precap_carrier_hft.py');v84=v89.v84;v83=v89.v83;v80=v89.v80;v38=v89.v38;v1=v89.v1

class V89C(v89.V89B):
 def __init__(self,*a,**kw):
  self.v89cActiveCompositeSubmits=0;self.v89cActiveCompositeFills=0.0;self.v89cEvents=[];self.v89cActiveKeys=set()
  super().__init__(*a,**kw)
 def _maybe_hard_active(self,t):
  if self.v89cActiveCompositeSubmits>=1:return False
  rp=self.repairParent
  if rp is None:return False
  pid=int(rp.get('id'));side=rp.get('side');born=int(rp.get('bornAt') or -1)
  if born not in self.v89OverflowBirthClocks or pid not in self._armedParents or pid in self.activeByParent or pid in self.hardConfirmed or side not in ('UP','DOWN'):return False
  churn=[x for x in self.repairChurn if int(x.get('parentId') or -1)==pid]
  if not churn:return False
  base=float(self.armFillBase.get(pid,self._parent_actual_fill(pid)));now=float(self._parent_actual_fill(pid))
  if now>base+EPS:
   self.blockedPaymentProgress+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  if int(self.capEnd)-int(t)<=180000:
   self.v89cEvents.append({'t':int(t),'event':'V89C_LATE_ACTIVE_COMPOSITE_BLOCK','parentId':pid});return False
  # The churned passive carrier is the execution lineage; it need not still be live.
  last=churn[-1];pk=str(last['key']);e=self.carrierLedger.get(pk,{})
  qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf
  if not math.isfinite(legal) or legal<=EPS or legal>12.0+EPS:return False
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  self.hardConfirmed.add(pid);self.hardEventConfirmedCount+=1
  ev={'t':int(t),'event':'V89C_ACTIVE_COMPOSITE_HANDOFF_CONFIRMED','parentId':pid,'bornAt':born,'side':side,'passiveKey':pk,'churnCount':len(churn),'parentFillAtArm':base,'parentFillNow':now,'floor':pay['floor'],'managerDebt':pay['gap'],'liveAsk':ask,'legalPhysicalQty':legal,'overflowBudgetIfFull':max(0.0,legal-pay['gap'])}
  self.v89cEvents.append(dict(ev));self.activeEvents.append(dict(ev))
  ok=self._submit_active(t,pid,pk,side,ask,legal,oid)
  if ok:
   ak=self.activeByParent[pid]['key'];self.v89cActiveCompositeSubmits+=1;self.v89cActiveKeys.add(ak);self.v84CompositeSubmits+=1
   self.v84Composite[ak]={'key':ak,'side':side,'parentId':pid,'price':ask,'submittedQty':legal,'gapAtSubmit':float(pay['gap']),'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'V89C_ACTIVE_COMPOSITE'}
   self.v89cEvents.append({'t':int(t),'event':'V89C_ACTIVE_COMPOSITE_SUBMIT','parentId':pid,'key':ak,'side':side,'price':ask,'managerDebt':pay['gap'],'physicalQty':legal})
  return bool(ok)
 def run_v89c(self,models,winner):
  r=self.run_v89b(models,winner);af=0.0
  for key,m in self.v84Composite.items():
   if m.get('lane')=='V89C_ACTIVE_COMPOSITE':af+=float(m.get('fillSeen') or 0.0)
  self.v89cActiveCompositeFills=af
  r.update({'v89cActiveCompositeSubmits':self.v89cActiveCompositeSubmits,'v89cActiveCompositeFillQty':af,'v89cEvents':self.v89cEvents[:160]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v89c_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V89C_ONE_ACTIVE_COMPOSITE','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V89C_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  b=mk(v89.V89B)
  try:br=b.run_v89b(models,cr['winner'])
  finally:b.close()
  c=mk(V89C)
  try:rr=c.run_v89c(models,cr['winner'])
  finally:c.close()
  legacy=int(rr.get('overOwnedSubmitViolations') or 0);comps=int(rr.get('v84CompositeSubmits') or 0);unexpl=max(0,legacy-comps);other=sum(float(rr.get(k) or 0.0) for k in ['authorizedSubmitWithTruthRoleMismatch','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
  conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  repair_bound=True;active_state=[]
  for key,m in (rr.get('v84CompositeState') or {}).items():
   if m.get('lane')=='V89C_ACTIVE_COMPOSITE':
    active_state.append(m)
    if float(m.get('repairAllocated') or 0)>float(m.get('gapAtSubmit') or 0)+1e-7:repair_bound=False
  payment_improves=float(rr.get('v84OverflowPaidQty') or 0)>float(br.get('v84OverflowPaidQty') or 0)+EPS
  gates={'overflowBornParentObserved':len(rr.get('v89OverflowBirthClocks') or [])>0,'passivePrecapSubmittedFirst':int(rr.get('v89RecursiveSubmits') or 0)>0,'parentChurnObserved':any(int(x.get('parentId') or -1) in set(rr.get('v89RecursiveParents') or []) for x in rr.get('v34RepairChurnEvents',[])),'exactlyOneActiveCompositeSubmit':int(rr.get('v89cActiveCompositeSubmits') or 0)==1,'activePhysicalFillExercised':float(rr.get('v89cActiveCompositeFillQty') or 0)>EPS,'repairAllocationNeverExceedsManagerDebt':repair_bound,'physicalAllocationConservation':conservation,'overflowPaymentImproves':payment_improves,'zeroUnexplainedLegacyOverOwned':unexpl==0,'zeroOtherSafetyViolations':other<=EPS,'zeroSharedRealizedOverfill':float(rr.get('v36SharedRealizedOverfill') or 0)<=EPS,'zeroPassiveCancelBeforeActiveFill':int(rr.get('v36PassiveCancelBeforeActiveFill') or 0)==0}
  decision='KEEP_V89C_ONE_ACTIVE_COMPOSITE_HANDOFF_FOR_REPLICATION' if all(gates.values()) else ('EXECUTION_INCONCLUSIVE_V89C_ACTIVE_SUBMITTED_NO_FILL' if gates['exactlyOneActiveCompositeSubmit'] and not gates['activePhysicalFillExercised'] else 'REJECT_OR_DIAGNOSE_V89C')
  out={'version':'ETH_REPAIR_V89C_ONE_ACTIVE_COMPOSITE_HANDOFF','date':'2026-09-03','researchOnly':True,'marketId':MID,'decision':decision,'gates':gates,'baselineV89B':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'recursiveSubmits':br.get('v89RecursiveSubmits'),'recursiveFillQty':br.get('v89RecursiveFillQty'),'overflowAllocated':br.get('v84OverflowAllocatedQty'),'overflowPaid':br.get('v84OverflowPaidQty')},'candidateV89C':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'activeCompositeSubmits':rr.get('v89cActiveCompositeSubmits'),'activeCompositeFillQty':rr.get('v89cActiveCompositeFillQty'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'sharedOverfill':rr.get('v36SharedRealizedOverfill'),'unexplainedOverOwned':unexpl},'activeCompositeState':active_state,'v89cEvents':rr.get('v89cEvents',[]),'activeEvents':rr.get('v36ActiveEvents',[])[:160],'v84Events':rr.get('v84Events',[])[:240],'fullCandidate':rr,'boundary':['one-market realistic HFT','at most one allocation-aware active composite handoff','passive carrier must churn first','minimum legal active physical slice','Repair-first confirmed-fill allocation','no Target runtime input','no tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV89B'],'candidate':out['candidateV89C'],'events':out['v89cEvents'][:30]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
