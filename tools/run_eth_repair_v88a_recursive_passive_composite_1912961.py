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
v84=sib('eth_v84_for_v88a','run_eth_repair_v84b_composite_repair_behavior_1912961.py');v80=v84.v80;v38=v84.v38

class PassiveOnlyV84(v84.V84BCompositeRepair):
 def _maybe_hard_active(self,t):
  return False

class V88A(PassiveOnlyV84):
 def __init__(self,*a,**kw):
  self.v88OverflowBirthClocks=set();self.v88RecursiveSubmits=0;self.v88RecursiveKeys=set();self.v88Events=[]
  super().__init__(*a,**kw)
 def _scan_v84(self,t):
  before={k:m.get('overflowBornAt') for k,m in getattr(self,'v84Composite',{}).items()}
  out=super()._scan_v84(t)
  for k,m in getattr(self,'v84Composite',{}).items():
   b=m.get('overflowBornAt')
   if b is not None and before.get(k) is None:
    self.v88OverflowBirthClocks.add(int(b));self.v88Events.append({'t':int(b),'event':'V88_OVERFLOW_BIRTH','compositeKey':k,'overflowDebt':float(m.get('overflowDebt') or 0.0),'nextRepairSide':'DOWN' if m.get('side')=='UP' else 'UP'})
  return out
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None:
   side,qty,oldp,role,oid=z;rp=getattr(self,'repairParent',None);born=int(rp.get('bornAt') or -1) if isinstance(rp,dict) else -1
   if role=='REPAIR' and born in self.v88OverflowBirthClocks:
    try:
     ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));p=float(qv[side]['bid']);legal=1.0/p if p>EPS else math.inf;floor,u,d,cost=self._raw_floor()
     crossing=math.isfinite(legal) and legal>gap+EPS and legal<=12.0+EPS and gap>EPS and 0<p<1
     if crossing:
      hu=float(u)+(legal if side=='UP' else 0.0);hd=float(d)+(legal if side=='DOWN' else 0.0);hc=float(cost)+legal*p;hf=min(hu,hd)-hc;overflow=max(0.0,legal-gap)
      if int(self.capEnd)-int(t)<=180000:
       self.v84LateBlocks+=1;self.v88Events.append({'t':int(t),'event':'V88_RECURSIVE_LATE_BLOCK','parentId':int(rp.get('id')),'side':side,'gap':gap,'legalQty':legal});return False
      if hf>float(floor)+EPS and overflow>EPS:
       self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=int(rp.get('id'));self._pendingLane='V88_RECURSIVE_COMPOSITE_REPAIR';n0=self.n
       ok=self.submit(t,side,p,legal)
       if ok:
        key=f'{side}_{n0}';roles_this_tick.add('REPAIR');self.v84CompositeSubmits+=1;self.v88RecursiveSubmits+=1;self.v88RecursiveKeys.add(key)
        self.v84Composite[key]={'key':key,'side':side,'parentId':int(rp.get('id')),'price':p,'submittedQty':legal,'gapAtSubmit':gap,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0}
        rb=getattr(self,'reserveBuilder',None)
        if rb is not None:rb.setdefault('repairKeys',[]).append(key)
        ev={'t':int(t),'event':'V88_RECURSIVE_COMPOSITE_SUBMIT','key':key,'parentId':int(rp.get('id')),'parentBornAt':born,'side':side,'price':p,'qty':legal,'repairBudget':gap,'overflowBudget':overflow,'floorBefore':float(floor),'hypFloorAfter':hf,'hypFloorDelta':hf-float(floor)}
        self.v88Events.append(ev);self.v84Events.append(dict(ev));return True
    except Exception as ex:self.v88Events.append({'t':int(t),'event':'V88_RECURSIVE_EVAL_ERROR','error':str(ex)})
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_v88a(self,models,winner):
  r=self.run_exam_v84b(models,winner)
  recursive_fill=0.0;recursive_overflow=0.0
  for k in self.v88RecursiveKeys:
   m=self.v84Composite.get(k,{})
   recursive_fill+=float(m.get('fillSeen') or 0.0);recursive_overflow+=float(m.get('overflowAllocated') or 0.0)
  r.update({'v88OverflowBirthClocks':sorted(self.v88OverflowBirthClocks),'v88RecursiveCompositeSubmits':self.v88RecursiveSubmits,'v88RecursiveCompositeFillQty':recursive_fill,'v88RecursiveOverflowAllocatedQty':recursive_overflow,'v88Events':self.v88Events[:180]})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v88a_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V88A_RECURSIVE_PASSIVE','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V88A_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  b=mk(PassiveOnlyV84)
  try:br=b.run_exam_v84b(models,cr['winner'])
  finally:b.close()
  c=mk(V88A)
  try:rr=c.run_v88a(models,cr['winner'])
  finally:c.close()
  legacy=int(rr.get('overOwnedSubmitViolations') or 0);comps=int(rr.get('v84CompositeSubmits') or 0);unexpl=max(0,legacy-comps)
  other=sum(float(rr.get(k) or 0) for k in ['authorizedSubmitWithTruthRoleMismatch','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
  conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  debt=abs(float(rr.get('v84OverflowAllocatedQty') or 0)-float(rr.get('v84OverflowDebtQty') or 0))<=1e-7
  recsubmit=int(rr.get('v88RecursiveCompositeSubmits') or 0);recfill=float(rr.get('v88RecursiveCompositeFillQty') or 0)
  gates={'overflowBornRepairParentObserved':len(rr.get('v88OverflowBirthClocks') or [])>0,'recursiveCompositeSubmitExercised':recsubmit>0,'recursiveCompositePhysicalFillExercised':recfill>EPS,'physicalAllocationConservation':conservation,'overflowDebtExact':debt,'zeroPreBirthPaymentLeak':float(rr.get('v84PreBirthPaymentLeak') or 0)<=EPS,'zeroDuplicateDebt':float(rr.get('v84DuplicateDebt') or 0)<=EPS,'zeroUnexplainedLegacyOverOwned':unexpl==0,'zeroOtherSafetyViolations':other<=EPS,'terminalFloorNonWorseThanV84B':float(rr.get('floor') or 0)+EPS>=float(br.get('floor') or 0)}
  if recsubmit>0 and recfill<=EPS:decision='EXECUTION_INCONCLUSIVE_RECURSIVE_COMPOSITE_SUBMITTED_NO_FILL'
  elif all(gates.values()):decision='KEEP_V88A_RECURSIVE_PASSIVE_COMPOSITE_FOR_REPLICATION'
  else:decision='REJECT_OR_DIAGNOSE_V88A_RECURSIVE_PASSIVE_COMPOSITE'
  out={'version':'ETH_REPAIR_V88A_RECURSIVE_PASSIVE_COMPOSITE','date':'2026-09-03','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baselineV84B':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents'),'compositeSubmits':br.get('v84CompositeSubmits'),'compositeFill':br.get('v84CompositeFillQty'),'overflowAllocated':br.get('v84OverflowAllocatedQty'),'overflowPaid':br.get('v84OverflowPaidQty')},'v88a':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'compositeSubmits':rr.get('v84CompositeSubmits'),'compositeFill':rr.get('v84CompositeFillQty'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'recursiveSubmits':recsubmit,'recursiveFillQty':recfill,'recursiveOverflowAllocated':rr.get('v88RecursiveOverflowAllocatedQty'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'unexplainedOverOwned':unexpl},'v88Events':rr.get('v88Events',[]),'v84Events':rr.get('v84Events',[])[:240],'fullCandidate':rr,'boundary':['one fresh market only','recursive minimum-legal passive composite only','Active Repair disabled in both arms','ordinary Repair parents unchanged','no raw carrier sizing','no Target runtime input','no tuning','realistic HFT only','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV84B'],'v88a':out['v88a'],'v88Events':out['v88Events'][:30]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
