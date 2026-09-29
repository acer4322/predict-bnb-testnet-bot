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
v89c=sib('eth_v89c_for_v89d','run_eth_repair_v89c_one_active_composite_handoff.py');v89=v89c.v89;v84=v89c.v84;v80=v89c.v80;v38=v89c.v38;v1=v89c.v1

class V89D(v89c.V89C):
 def _maybe_hard_active(self,t):
  if self.v89cActiveCompositeSubmits>=2:return False
  rp=self.repairParent
  if rp is None:return False
  pid=int(rp.get('id'));side=rp.get('side');born=int(rp.get('bornAt') or -1)
  if born not in self.v89OverflowBirthClocks or pid not in self._armedParents or pid in self.activeByParent or pid in self.hardConfirmed or side not in ('UP','DOWN'):return False
  churn=[x for x in self.repairChurn if int(x.get('parentId') or -1)==pid]
  if not churn:return False
  base=float(self.armFillBase.get(pid,self._parent_actual_fill(pid)));now=float(self._parent_actual_fill(pid))
  if now>base+EPS:self.blockedPaymentProgress+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  if int(self.capEnd)-int(t)<=180000:return False
  last=churn[-1];pk=str(last['key']);e=self.carrierLedger.get(pk,{})
  qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf
  if not math.isfinite(legal) or legal<=EPS or legal>12.0+EPS:return False
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  self.hardConfirmed.add(pid);self.hardEventConfirmedCount+=1
  ev={'t':int(t),'event':'V89D_ACTIVE_COMPOSITE_HANDOFF_CONFIRMED','ordinal':self.v89cActiveCompositeSubmits+1,'parentId':pid,'bornAt':born,'side':side,'passiveKey':pk,'churnCount':len(churn),'parentFillAtArm':base,'parentFillNow':now,'floor':pay['floor'],'managerDebt':pay['gap'],'liveAsk':ask,'legalPhysicalQty':legal,'overflowBudgetIfFull':max(0.0,legal-pay['gap'])}
  self.v89cEvents.append(dict(ev));self.activeEvents.append(dict(ev))
  ok=self._submit_active(t,pid,pk,side,ask,legal,oid)
  if ok:
   ak=self.activeByParent[pid]['key'];self.v89cActiveCompositeSubmits+=1;self.v89cActiveKeys.add(ak);self.v84CompositeSubmits+=1
   self.v84Composite[ak]={'key':ak,'side':side,'parentId':pid,'price':ask,'submittedQty':legal,'gapAtSubmit':float(pay['gap']),'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'V89C_ACTIVE_COMPOSITE'}
   self.v89cEvents.append({'t':int(t),'event':'V89D_ACTIVE_COMPOSITE_SUBMIT','ordinal':self.v89cActiveCompositeSubmits,'parentId':pid,'key':ak,'side':side,'price':ask,'managerDebt':pay['gap'],'physicalQty':legal})
  return bool(ok)

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v89d_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V89D_TWO_ACTIVE_COMPOSITE','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V89D_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  b=mk(v89c.V89C)
  try:br=b.run_v89c(models,cr['winner'])
  finally:b.close()
  c=mk(V89D)
  try:rr=c.run_v89c(models,cr['winner'])
  finally:c.close()
  legacy=int(rr.get('overOwnedSubmitViolations') or 0);comps=int(rr.get('v84CompositeSubmits') or 0);unexpl=max(0,legacy-comps);other=sum(float(rr.get(k) or 0.0) for k in ['authorizedSubmitWithTruthRoleMismatch','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
  conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  states=[m for m in (rr.get('v84CompositeState') or {}).values() if m.get('lane')=='V89C_ACTIVE_COMPOSITE'];repair_bound=all(float(m.get('repairAllocated') or 0)<=float(m.get('gapAtSubmit') or 0)+1e-7 for m in states)
  payment_count=sum(1 for e in rr.get('v84Events',[]) if e.get('event')=='OVERFLOW_REPAIR_PAYMENT' and float(e.get('paid') or 0)>EPS)
  gates={'exactlyTwoActiveCompositeSubmits':int(rr.get('v89cActiveCompositeSubmits') or 0)==2,'bothActiveCompositeCarriersFill':len([m for m in states if float(m.get('fillSeen') or 0)>EPS])>=2,'atLeastTwoOverflowRepairPayments':payment_count>=2,'repairAllocationNeverExceedsManagerDebt':repair_bound,'physicalAllocationConservation':conservation,'zeroSharedOverfill':float(rr.get('v36SharedRealizedOverfill') or 0)<=EPS,'zeroUnexplainedLegacyOverOwned':unexpl==0,'zeroOtherSafetyViolations':other<=EPS,'secondRelayBirthEvidence':int(rr.get('repairParentBirths') or 0)>=4 or len(rr.get('v89OverflowBirthClocks') or [])>=3}
  decision='KEEP_V89D_TWO_HANDOFF_RELAY_FOR_FRESH_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_V89D_TWO_HANDOFF_RELAY'
  out={'version':'ETH_REPAIR_V89D_TWO_ACTIVE_COMPOSITE_HANDOFFS','date':'2026-09-03','researchOnly':True,'marketId':MID,'decision':decision,'gates':gates,'baselineV89C':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'activeCompositeSubmits':br.get('v89cActiveCompositeSubmits'),'activeCompositeFillQty':br.get('v89cActiveCompositeFillQty'),'overflowPaid':br.get('v84OverflowPaidQty'),'repairParentBirths':br.get('repairParentBirths')},'candidateV89D':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'activeCompositeSubmits':rr.get('v89cActiveCompositeSubmits'),'activeCompositeFillQty':rr.get('v89cActiveCompositeFillQty'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'unexplainedOverOwned':unexpl},'activeCompositeStates':states,'events':rr.get('v89cEvents',[]),'v84Events':rr.get('v84Events',[])[:300],'parentLedger':rr.get('v34ParentLedger',[]),'fullCandidate':rr,'boundary':['two-handoff smoke cap only','not a runtime strategy limit','same V89C mechanics otherwise frozen','no Target runtime input','no tuning','realistic HFT only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV89C'],'candidate':out['candidateV89D'],'events':out['events'][:40]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
