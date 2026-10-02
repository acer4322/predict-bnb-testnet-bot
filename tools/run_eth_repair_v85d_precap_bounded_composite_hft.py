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
v84=sib('eth_v84b_for_v85d','run_eth_repair_v84b_composite_repair_behavior_1912961.py');v83=v84.v83;v80=v84.v80;v38=v84.v38

class V85D(v84.V84BCompositeRepair):
 def __init__(self,*a,**kw):
  self.v85RawAt={};self.v85Events=[]
  super().__init__(*a,**kw)
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']))
  z=super().choose_authorized(t,end,proposed_side,proposed_qty)
  if z is not None and str(z[3])=='REPAIR':
   self.v85RawAt[int(t)]={'rawProposedQty':float(proposed_qty),'gapBeforeCap':gap,'authorizedQty':float(z[1]),'side':str(z[0])}
  return z
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None:
   side,qty,oldp,role,oid=z;rb=getattr(self,'reserveBuilder',None)
   if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None and int(t)<=int(rb.get('pairDeadline') or -1):
    try:
     ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));p=float(qv[side]['bid']);floor,u,d,cost=self._raw_floor();raw=float(self.v85RawAt.get(int(t),{}).get('rawProposedQty',qty));max_floor_qty=gap/p if p>EPS else 0.0;candidate=min(raw,max_floor_qty,12.0);legal=1.0/p if p>EPS else math.inf
     crossing=math.isfinite(legal) and candidate>gap+EPS and candidate+EPS>=legal and gap>EPS and 0<p<1
     if crossing:
      hu=float(u)+(candidate if side=='UP' else 0.0);hd=float(d)+(candidate if side=='DOWN' else 0.0);hc=float(cost)+candidate*p;hf=min(hu,hd)-hc;overflow=max(0.0,candidate-gap)
      if int(self.capEnd)-int(t)<=180000:
       self.v84LateBlocks+=1;self.v84Events.append({'t':int(t),'event':'COMPOSITE_LATE_BLOCK','side':side,'price':p,'gap':gap,'candidateQty':candidate,'overflow':overflow});return False
      if hf+1e-7>=float(floor) and overflow>EPS:
       self.packageRepairEval+=1;self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=int(self.repairParent.get('id')) if self.repairParent else None;self._pendingLane='V85D_PRECAP_COMPOSITE';n0=self.n
       ok=self.submit(t,side,p,candidate)
       if ok:
        key=f'{side}_{n0}';roles_this_tick.add('REPAIR');self.packageRepairAccept+=1;self.v84CompositeSubmits+=1
        self.v84Composite[key]={'key':key,'side':side,'parentId':int(self.repairParent.get('id')) if self.repairParent else None,'price':p,'submittedQty':candidate,'gapAtSubmit':gap,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0}
        ev={'t':int(t),'event':'V85D_PRECAP_COMPOSITE_SUBMIT','key':key,'side':side,'price':p,'rawProposedQty':raw,'oldCappedRepairQty':float(qty),'candidateQty':candidate,'repairBudget':gap,'overflowBudget':overflow,'floorBefore':float(floor),'hypFloorAfter':hf,'hypFloorDelta':hf-float(floor),'floorPreservingMaxQty':max_floor_qty}
        self.v84Events.append(ev);self.v85Events.append(ev)
        if rb is not None:rb.setdefault('repairKeys',[]).append(key)
        return True
    except Exception as ex:self.v85Events.append({'t':int(t),'event':'V85D_EVAL_ERROR','error':str(ex)})
  return v83.V83CleanModularCandidate._submit_authorized(self,t,qv,z,roles_this_tick)
 def run_v85d(self,models,winner):
  r=self.run_exam_v84b(models,winner);r['v85Events']=self.v85Events[:240];return r

def load(a,tmp):
 zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];cr={int(r['marketId']):r for r in co}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];return cr,models,life,cap,tim,econ,price,sur,t44,t47,tmp/'tapes'/f'{MID}.json.xz'

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v85d_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V85D_PRECAP_BOUNDED_COMPOSITE','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V85D_START','market':MID}),flush=True)
 try:
  cr,models,life,cap,tim,econ,price,sur,t44,t47,tape=load(a,tmp)
  b=v84.V84BCompositeRepair(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:br=b.run_exam_v84b(models,cr['winner'])
  finally:b.close()
  c=V85D(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:rr=c.run_v85d(models,cr['winner'])
  finally:c.close()
  legacy=int(rr.get('overOwnedSubmitViolations') or 0);comps=int(rr.get('v84CompositeSubmits') or 0);unexpl=max(0,legacy-comps)
  conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  debt=abs(float(rr.get('v84OverflowAllocatedQty') or 0)-float(rr.get('v84OverflowDebtQty') or 0))<=1e-7
  other=sum(float(rr.get(k) or 0) for k in ['authorizedSubmitWithTruthRoleMismatch','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
  bigger=float(rr.get('v84OverflowAllocatedQty') or 0)>1.25*max(float(br.get('v84OverflowAllocatedQty') or 0),EPS)
  gates={'physicalAllocationConservation':conservation,'overflowDebtExact':debt,'zeroPreBirthOverflowPaymentLeak':float(rr.get('v84PreBirthPaymentLeak') or 0)<=EPS,'zeroDuplicateOverflowDebt':float(rr.get('v84DuplicateDebt') or 0)<=EPS,'zeroUnexplainedLegacyOverOwned':unexpl==0,'zeroOtherSafetyViolations':other<=EPS,'terminalFloorNonWorseThanV84B':float(rr.get('floor') or 0)+EPS>=float(br.get('floor') or 0),'confirmedOverflowMateriallyLargerThanV84B':bigger}
  if all(gates.values()):decision='KEEP_V85D_PRECAP_BOUNDED_COMPOSITE_FOR_FRESH4_REPLICATION'
  elif all(v for k,v in gates.items() if k!='confirmedOverflowMateriallyLargerThanV84B'):decision='EXECUTION_INCONCLUSIVE_NO_LARGER_CONFIRMED_OVERFLOW'
  else:decision='REJECT_V85D_SAFETY_OR_FLOOR_REGRESSION'
  out={'version':'ETH_REPAIR_V85D_PRECAP_BOUNDED_COMPOSITE_HFT','date':'2026-09-03','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'v84b':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents'),'compositeSubmits':br.get('v84CompositeSubmits'),'compositeFill':br.get('v84CompositeFillQty'),'overflowAllocated':br.get('v84OverflowAllocatedQty'),'overflowPaid':br.get('v84OverflowPaidQty')},'v85d':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'compositeSubmits':rr.get('v84CompositeSubmits'),'compositeFill':rr.get('v84CompositeFillQty'),'repairAllocated':rr.get('v84RepairAllocatedQty'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowDebt':rr.get('v84OverflowDebtQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'semanticRounds':rr.get('v70dSemanticRounds'),'legacyOverOwned':legacy,'unexplainedOverOwned':unexpl},'events':rr.get('v84Events',[])[:100],'fullCandidate':rr,'boundary':['one market realistic-HFT','OUR raw pre-cap qty only','strict floor-preserving bound','no Target qty runtime input','no tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'v84b':out['v84b'],'v85d':out['v85d'],'events':out['events'][:20]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
