from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;MID=1912961

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

mod=sib('modular_v2_for_alloc_v2','run_eth_repair_modular_v2_recursive_execution_hft.py')
allocmod=sib('allocation_ledger_v2_runtime','allocation_ledger_v2.py')
v89=mod.v89;v84=v89.v84;v80=v84.v80;v38=v89.v38;v1=v89.v1

class ModularAllocationLedgerV2(mod.ModularRecursiveRepairExecutionV2):
 def __init__(self,*a,**kw):
  self.allocationLedgerV2=allocmod.SharedParentDebtAllocationLedgerV2();self.allocationV2Events=[];self.allocationV2PureOverflowFirstFills=0
  super().__init__(*a,**kw)

 def _seed_parent_debt(self,pid,fallback):
  if self.allocationLedgerV2.describe_parent(pid) is not None:return self.allocationLedgerV2.remaining(pid,fallback)
  cand=[]
  for k,m in getattr(self,'v84Composite',{}).items():
   try:
    if int(m.get('parentId'))==int(pid):cand.append((str(k),float(m.get('gapAtSubmit') or 0.0)))
   except Exception:pass
  debt=float(cand[0][1]) if cand else max(0.0,float(fallback or 0.0))
  key=cand[0][0] if cand else f'PARENT_{pid}_SEED';self.allocationLedgerV2.register_carrier(key,int(pid),debt)
  return self.allocationLedgerV2.remaining(pid,debt)

 def _manager_debt_for_parent(self,pid,fallback):
  return self._seed_parent_debt(int(pid),float(fallback or 0.0))

 def _repair_payment_cum(self,key,e):
  m=getattr(self,'v84Composite',{}).get(key)
  if m is not None:return float(m.get('repairAllocated') or 0.0)
  return float(e.get('actualFilled') or 0.0)

 def _scan_v84(self,t):
  # Register sibling carriers against one parent debt before processing any new fill.
  for key,m in list(self.v84Composite.items()):
   pid=m.get('parentId')
   if pid is None:continue
   self.allocationLedgerV2.register_carrier(str(key),int(pid),float(m.get('gapAtSubmit') or 0.0))
  # Fill-time Repair-first allocation using shared parent debt.
  for key,m in list(self.v84Composite.items()):
   e=self.carrierLedger.get(key,{});cur=float(e.get('actualFilled') or 0.0);old=float(m.get('fillSeen') or 0.0)
   if cur<=old+EPS:continue
   pid=int(m.get('parentId'));first=(old<=EPS)
   ar=self.allocationLedgerV2.allocate_cumulative(str(key),pid,cur,float(m.get('gapAtSubmit') or 0.0))
   if ar is None:continue
   inc=ar.fill_increment;m['fillSeen']=cur;self.v84CompositeFillQty+=inc
   r=float(ar.repair_increment);o=float(ar.overflow_increment);m['repairAllocated']+=r;m['overflowAllocated']+=o;self.v84RepairAllocated+=r;self.v84OverflowAllocated+=o
   ev={'t':int(t),'event':'ALLOCATION_V2_FILL','key':key,'parentId':pid,'fillInc':inc,'parentDebtBefore':ar.debt_before,'repairInc':r,'overflowInc':o,'parentDebtAfter':ar.debt_after,'transitionOverflowTotal':ar.transition_overflow_total,'repairCum':m['repairAllocated'],'overflowCum':m['overflowAllocated']}
   if first and r<=EPS and o>EPS:self.allocationV2PureOverflowFirstFills+=1;ev['pureOverflowSiblingFirstFill']=True
   if o>EPS:
    if m.get('overflowBornAt') is None:
     m['overflowBornAt']=int(t);opp='DOWN' if m['side']=='UP' else 'UP';baseline={}
     for rk,re in self.carrierLedger.items():
      if str(re.get('objectiveRole') or '')=='REPAIR' and re.get('side')==opp:
       seen=self._repair_payment_cum(rk,re);self.v84OppSeen[rk]=seen
       if seen>EPS:baseline[rk]=seen
     ev['overflowBirth']=True;ev['oppositeRepairBaseline']=baseline
     # Preserve the frozen V89 event seam: new overflow makes the next Repair parent eligible for recursive execution routing.
     if hasattr(self,'v89OverflowBirthClocks'):
      self.v89OverflowBirthClocks.add(int(t))
      if hasattr(self,'v89Events'):
       self.v89Events.append({'t':int(t),'event':'V89_OVERFLOW_PARENT_BIRTH_CLOCK','compositeKey':key,'overflowDebt':float(o),'nextRepairSide':'DOWN' if m['side']=='UP' else 'UP','source':'ALLOCATION_LEDGER_V2'})
    m['overflowDebt']+=o;self.v84OverflowDebt+=o
   self.v84Events.append(dict(ev));self.allocationV2Events.append(dict(ev))
  # Strictly post-birth opposite *Repair allocation* pays old overflow debt FIFO.
  for key,m in list(self.v84Composite.items()):
   born=m.get('overflowBornAt');rem=max(0.0,float(m.get('overflowDebt') or 0.0)-float(m.get('overflowPaid') or 0.0))
   if born is None or rem<=EPS:continue
   opp='DOWN' if m['side']=='UP' else 'UP'
   for rk,re in list(self.carrierLedger.items()):
    if str(re.get('objectiveRole') or '')!='REPAIR' or re.get('side')!=opp:continue
    cur=self._repair_payment_cum(rk,re);old=float(self.v84OppSeen.get(rk,0.0))
    if int(t)<=int(born):
     if cur>old+EPS:self.v84PreBirthPaymentLeak+=cur-old
     self.v84OppSeen[rk]=max(old,cur);continue
    if cur>old+EPS:
     inc=cur-old;pay=min(inc,rem);m['overflowPaid']+=pay;self.v84OverflowPaid+=pay;rem-=pay
     if pay>EPS:self.v84Events.append({'t':int(t),'event':'ALLOCATION_V2_OVERFLOW_PAYMENT','compositeKey':key,'repairKey':rk,'repairAllocationInc':inc,'paid':pay,'overflowPaidCum':m['overflowPaid'],'overflowDebt':m['overflowDebt']})
    self.v84OppSeen[rk]=max(old,cur)
    if rem<=EPS:break

 def run_allocation_v2(self,models,winner):
  r=self.run_modular_v2(models,winner)
  r.update({'allocationLedgerV2':self.allocationLedgerV2.name,'allocationV2Parents':{str(pid):self.allocationLedgerV2.describe_parent(pid) for pid in self.allocationLedgerV2.parents},'allocationV2PureOverflowFirstFills':self.allocationV2PureOverflowFirstFills,'allocationV2Events':self.allocationV2Events[:240]});return r

def safety(r):
 legacy=int(r.get('overOwnedSubmitViolations') or 0);comps=int(r.get('v84CompositeSubmits') or 0);drift=int(r.get('repairToExpandAtFirstFill') or 0);covered=int(r.get('allocationV2PureOverflowFirstFills') or 0)
 return {'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'unexplainedOverOwned':max(0,legacy-comps),'legacyRepairDrift':drift,'coveredPureOverflowRoleTransitions':covered,'unexplainedRepairDrift':max(0,drift-covered),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'preBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0)+float(r.get('v84PreBirthPaymentLeak') or 0),'duplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)+float(r.get('v84DuplicateDebt') or 0),'sharedOverfill':float(r.get('v36SharedRealizedOverfill') or 0)}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_alloc_v2_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'MODULAR_ALLOCATION_V2','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MODULAR_ALLOCATION_V2_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  b=mk(mod.ModularRecursiveRepairExecutionV2)
  try:br=b.run_modular_v2(models,cr['winner'])
  finally:b.close()
  c=mk(ModularAllocationLedgerV2)
  try:rr=c.run_allocation_v2(models,cr['winner'])
  finally:c.close()
  ss=safety(rr);cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  parents=rr.get('allocationV2Parents') or {};parent_ok=all(float(x.get('repairPaid') or 0)<=float(x.get('initialDebt') or 0)+1e-7 and float(x.get('remainingDebt') or 0)>=-1e-9 for x in parents.values())
  gates={'allocationModuleActive':rr.get('allocationLedgerV2')=='shared_parent_debt_repair_first_allocation_v2','routerStillActive':(rr.get('modularRepairExecutionProfile') or {}).get('repairExecution')=='recursive_composite_single_disconnect_execution_v1','physicalAllocationConservation':cons,'parentRepairNeverExceedsInitialDebt':parent_ok,'zeroTruthMismatch':ss['truthMismatch']==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS}
  decision='KEEP_ALLOCATION_LEDGER_V2_FOR_FRESH_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_ALLOCATION_LEDGER_V2'
  out={'version':'ETH_REPAIR_MODULAR_ALLOCATION_LEDGER_V2_HFT','date':'2026-09-03','researchOnly':True,'marketId':MID,'decision':decision,'gates':gates,'safety':ss,'baselineRouterLegacyAllocation':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents'),'activeCompositeSubmits':br.get('modularActiveCompositeSubmits'),'legacyRepairDrift':br.get('repairToExpandAtFirstFill'),'overflowPaid':br.get('v84OverflowPaidQty'),'overflowRemaining':br.get('v84OverflowRemainingQty')},'candidateAllocationV2':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'activeCompositeSubmits':rr.get('modularActiveCompositeSubmits'),'legacyRepairDrift':rr.get('repairToExpandAtFirstFill'),'coveredPureOverflowFirstFills':rr.get('allocationV2PureOverflowFirstFills'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements')},'allocationParents':parents,'allocationEvents':rr.get('allocationV2Events',[]),'routerEvents':rr.get('modularRepairExecutionEvents',[]),'fullCandidate':rr,'boundary':['single module replacement: AllocationLedger only','RepairExecutionRouter frozen','Manager/Completion/Ownership/Generation frozen','no fixed global handoff count','no Target runtime input','no tuning','realistic HFT only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'safety':ss,'baseline':out['baselineRouterLegacyAllocation'],'candidate':out['candidateAllocationV2']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
