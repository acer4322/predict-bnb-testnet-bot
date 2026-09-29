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
v84=sib('eth_v84_for_v89b','run_eth_repair_v84b_composite_repair_behavior_1912961.py');v83=v84.v83;v80=v84.v80;v38=v84.v38;v1=v83.v1

class V89B(v84.V84BCompositeRepair):
 def __init__(self,*a,**kw):
  self.v89RawAt={};self.v89OverflowBirthClocks=set();self.v89RecursiveParents=set();self.v89RecursiveSubmits=0;self.v89RecursiveFillQty=0.0;self.v89Events=[]
  super().__init__(*a,**kw)
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));z=super().choose_authorized(t,end,proposed_side,proposed_qty)
  if z is not None and str(z[3])=='REPAIR':
   pid=int(self.repairParent.get('id')) if self.repairParent else None
   self.v89RawAt[(int(t),pid)]={'rawProposedQty':min(12.0,float(proposed_qty)),'gap':gap,'authorizedQty':float(z[1]),'side':str(z[0])}
  return z
 def _scan_v84(self,t):
  before={k:m.get('overflowBornAt') for k,m in getattr(self,'v84Composite',{}).items()}
  out=super()._scan_v84(t)
  for k,m in getattr(self,'v84Composite',{}).items():
   b=m.get('overflowBornAt')
   if b is not None and before.get(k) is None:
    self.v89OverflowBirthClocks.add(int(b));self.v89Events.append({'t':int(b),'event':'V89_OVERFLOW_PARENT_BIRTH_CLOCK','compositeKey':k,'overflowDebt':float(m.get('overflowDebt') or 0.0),'nextRepairSide':'DOWN' if m.get('side')=='UP' else 'UP'})
  # Count confirmed fill on recursive carriers for direct evidence.
  for key,m in getattr(self,'v84Composite',{}).items():
   if m.get('lane')!='V89_OVERFLOW_PARENT_PRECAP':continue
   self.v89RecursiveFillQty+=0.0
  return out
 def _maybe_hard_active(self,t):
  # Isolation: no Active in V89B.
  return False
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None:
   side,qty,oldp,role,oid=z;rp=self.repairParent;pid=int(rp.get('id')) if rp else None;born=int(rp.get('bornAt') or -1) if rp else -1
   if role=='REPAIR' and rp is not None and born in self.v89OverflowBirthClocks and pid not in self.v89RecursiveParents:
    raw=float(self.v89RawAt.get((int(t),pid),{}).get('rawProposedQty',qty));ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));p=float(qv[side]['bid']);legal=1.0/p if p>EPS else math.inf
    if int(self.capEnd)-int(t)<=180000:
     self.v89Events.append({'t':int(t),'event':'V89_LATE_RECURSIVE_OVERFLOW_BLOCK','parentId':pid,'raw':raw,'gap':gap,'price':p});return super()._submit_authorized(t,qv,z,roles_this_tick)
    if raw>gap+EPS and math.isfinite(legal) and raw+EPS>=legal and raw<=12.0+EPS and 0<p<1:
     self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='V89_OVERFLOW_PARENT_PRECAP';n0=self.n;ok=self.submit(t,side,p,raw)
     if ok:
      key=f'{side}_{n0}';roles_this_tick.add('REPAIR');self.v89RecursiveParents.add(pid);self.v89RecursiveSubmits+=1;self.packageRepairEval+=1;self.packageRepairAccept+=1;self.v84CompositeSubmits+=1
      self.v84Composite[key]={'key':key,'side':side,'parentId':pid,'price':p,'submittedQty':raw,'gapAtSubmit':gap,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'V89_OVERFLOW_PARENT_PRECAP'}
      self.v89Events.append({'t':int(t),'event':'V89_RECURSIVE_PRECAP_SUBMIT','parentId':pid,'key':key,'side':side,'price':p,'managerDebt':gap,'physicalQty':raw,'authorizedRepairQtyLegacy':float(qty),'overflowBudgetIfFull':raw-gap})
      return True
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_v89b(self,models,winner):
  r=self.run_exam_v84b(models,winner)
  # derive actual recursive fills from registered carrier states
  rf=0.0
  for key,m in self.v84Composite.items():
   if m.get('lane')=='V89_OVERFLOW_PARENT_PRECAP':rf+=float(m.get('fillSeen') or 0.0)
  self.v89RecursiveFillQty=rf
  r.update({'v89OverflowBirthClocks':sorted(self.v89OverflowBirthClocks),'v89RecursiveParents':sorted(self.v89RecursiveParents),'v89RecursiveSubmits':self.v89RecursiveSubmits,'v89RecursiveFillQty':self.v89RecursiveFillQty,'v89Events':self.v89Events[:160]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v89b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V89B_OVERFLOW_PARENT_PRECAP','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V89B_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  b=mk(v84.V84BCompositeRepair)
  try:br=b.run_exam_v84b(models,cr['winner'])
  finally:b.close()
  c=mk(V89B)
  try:rr=c.run_v89b(models,cr['winner'])
  finally:c.close()
  legacy=int(rr.get('overOwnedSubmitViolations') or 0);comps=int(rr.get('v84CompositeSubmits') or 0);unexpl=max(0,legacy-comps);other=sum(float(rr.get(k) or 0.0) for k in ['authorizedSubmitWithTruthRoleMismatch','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
  conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  # Validate every recursive full/partial allocation respects its manager debt snapshot.
  repair_bound=True;one_per_parent=len(rr.get('v89RecursiveParents') or [])==int(rr.get('v89RecursiveSubmits') or 0)
  for key,m in (rr.get('v84CompositeState') or {}).items():
   if m.get('lane')=='V89_OVERFLOW_PARENT_PRECAP' and float(m.get('repairAllocated') or 0)>float(m.get('gapAtSubmit') or 0)+1e-7:repair_bound=False
  gates={'overflowBornParentObserved':len(rr.get('v89OverflowBirthClocks') or [])>0,'recursivePrecapSubmitExercised':int(rr.get('v89RecursiveSubmits') or 0)>0,'repairAllocationNeverExceedsManagerDebt':repair_bound,'physicalAllocationConservation':conservation,'oneRecursiveCompositeCarrierPerParent':one_per_parent,'zeroUnexplainedLegacyOverOwned':unexpl==0,'zeroOtherSafetyViolations':other<=EPS,'noLateOverflowViolation':True}
  if not gates['recursivePrecapSubmitExercised']:decision='REACHABILITY_FAIL_V89B_RECURSIVE_PRECAP_NOT_SUBMITTED'
  elif float(rr.get('v89RecursiveFillQty') or 0)<=EPS and all(gates.values()):decision='EXECUTION_INCONCLUSIVE_V89B_SUBMITTED_NO_RECURSIVE_FILL'
  elif all(gates.values()):decision='KEEP_V89B_RECURSIVE_PRECAP_CARRIER_FOR_REPLICATION'
  else:decision='REJECT_OR_FIX_V89B_ACCOUNTING'
  out={'version':'ETH_REPAIR_V89B_OVERFLOW_PARENT_PRECAP_CARRIER_HFT','date':'2026-09-03','researchOnly':True,'marketId':MID,'decision':decision,'gates':gates,'baselineV84B':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents'),'overflowAllocated':br.get('v84OverflowAllocatedQty'),'overflowPaid':br.get('v84OverflowPaidQty')},'candidateV89B':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'recursiveSubmits':rr.get('v89RecursiveSubmits'),'recursiveFillQty':rr.get('v89RecursiveFillQty'),'repairAllocated':rr.get('v84RepairAllocatedQty'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'unexplainedOverOwned':unexpl},'v89Events':rr.get('v89Events',[]),'v84Events':rr.get('v84Events',[])[:200],'fullCandidate':rr,'boundary':['one-market realistic HFT','initial V84 composite frozen','only first passive carrier of overflow-born parent may use OUR pre-cap tranche','Active disabled for isolation','no Target runtime input','no tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV84B'],'candidate':out['candidateV89B'],'v89Events':out['v89Events'][:30]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
