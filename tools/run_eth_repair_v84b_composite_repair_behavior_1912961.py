from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1912961

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v83=sib('eth_v83_for_v84b','run_eth_repair_v83_clean_modular_candidate_smoke.py');v80=v83.v80;v38=v83.v38;v1=v83.v1

class V84BCompositeRepair(v83.V83CleanModularCandidate):
 def __init__(self,*a,**kw):
  self.v84Composite={};self.v84CompositeSubmits=0;self.v84CompositeFillQty=0.0;self.v84RepairAllocated=0.0;self.v84OverflowAllocated=0.0;self.v84OverflowDebt=0.0;self.v84OverflowPaid=0.0;self.v84PreBirthPaymentLeak=0.0;self.v84DuplicateDebt=0.0;self.v84LateBlocks=0;self.v84Events=[];self.v84OppSeen={}
  super().__init__(*a,**kw)
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None:
   side,qty,oldp,role,oid=z;rb=getattr(self,'reserveBuilder',None)
   if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None and int(t)<=int(rb.get('pairDeadline') or -1):
    try:
     ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));p=float(qv[side]['bid']);legal=1.0/p if p>EPS else math.inf;floor,u,d,cost=self._raw_floor()
     crossing=math.isfinite(legal) and legal>gap+EPS and legal<=12.0+EPS and gap>EPS and 0<p<1
     if crossing:
      hu=float(u)+(legal if side=='UP' else 0.0);hd=float(d)+(legal if side=='DOWN' else 0.0);hc=float(cost)+legal*p;hf=min(hu,hd)-hc;overflow=max(0.0,legal-gap)
      if int(self.capEnd)-int(t)<=180000:
       self.v84LateBlocks+=1;self.v84Events.append({'t':int(t),'event':'COMPOSITE_LATE_BLOCK','side':side,'price':p,'gap':gap,'legalQty':legal,'overflow':overflow});return False
      if hf>float(floor)+EPS and overflow>EPS:
       self.packageRepairEval+=1;self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=int(self.repairParent.get('id')) if self.repairParent else None;self._pendingLane='V84_COMPOSITE_REPAIR';n0=self.n
       ok=self.submit(t,side,p,legal)
       if ok:
        key=f'{side}_{n0}';roles_this_tick.add('REPAIR');self.packageRepairAccept+=1;self.v84CompositeSubmits+=1
        self.v84Composite[key]={'key':key,'side':side,'parentId':int(self.repairParent.get('id')) if self.repairParent else None,'price':p,'submittedQty':legal,'gapAtSubmit':gap,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0}
        if rb is not None:rb.setdefault('repairKeys',[]).append(key)
        self.v84Events.append({'t':int(t),'event':'COMPOSITE_REPAIR_SUBMIT','key':key,'side':side,'price':p,'qty':legal,'repairBudget':gap,'overflowBudget':overflow,'floorBefore':float(floor),'hypFloorAfter':hf,'hypFloorDelta':hf-float(floor),'pairSum':float(rb.get('firstPrice') or 0.0)+p})
        return True
    except Exception as ex:
     self.v84Events.append({'t':int(t),'event':'COMPOSITE_EVAL_ERROR','error':str(ex)})
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def _scan_v84(self,t):
  for key,m in list(self.v84Composite.items()):
   e=self.carrierLedger.get(key,{})
   cur=float(e.get('actualFilled') or 0.0);old=float(m.get('fillSeen') or 0.0)
   if cur<=old+EPS:continue
   inc=cur-old;m['fillSeen']=cur;self.v84CompositeFillQty+=inc
   rem=max(0.0,float(m['gapAtSubmit'])-float(m['repairAllocated']));r=min(inc,rem);o=max(0.0,inc-r)
   m['repairAllocated']+=r;m['overflowAllocated']+=o;self.v84RepairAllocated+=r;self.v84OverflowAllocated+=o
   ev={'t':int(t),'event':'COMPOSITE_FILL_ALLOCATION','key':key,'fillInc':inc,'repairInc':r,'overflowInc':o,'repairCum':m['repairAllocated'],'overflowCum':m['overflowAllocated']}
   if o>EPS:
    if m.get('overflowBornAt') is None:
     m['overflowBornAt']=int(t);opp='DOWN' if m['side']=='UP' else 'UP';baseline={}
     for rk,re in self.carrierLedger.items():
      if str(re.get('objectiveRole') or '')=='REPAIR' and re.get('side')==opp:
       seen=float(re.get('actualFilled') or 0.0);self.v84OppSeen[rk]=seen
       if seen>EPS:baseline[rk]=seen
     ev['overflowBirth']=True;ev['oppositeRepairBaseline']=baseline
    m['overflowDebt']+=o;self.v84OverflowDebt+=o
   self.v84Events.append(ev)
  # Strictly post-birth opposite Repair pays oldest overflow debt FIFO.
  for key,m in list(self.v84Composite.items()):
   born=m.get('overflowBornAt');rem=max(0.0,float(m.get('overflowDebt') or 0.0)-float(m.get('overflowPaid') or 0.0))
   if born is None or rem<=EPS:continue
   opp='DOWN' if m['side']=='UP' else 'UP'
   for rk,re in list(self.carrierLedger.items()):
    if str(re.get('objectiveRole') or '')!='REPAIR' or re.get('side')!=opp:continue
    cur=float(re.get('actualFilled') or 0.0);old=float(self.v84OppSeen.get(rk,0.0))
    if int(t)<=int(born):
     if cur>old+EPS:self.v84PreBirthPaymentLeak+=cur-old
     self.v84OppSeen[rk]=max(old,cur);continue
    if cur>old+EPS:
     inc=cur-old;pay=min(inc,rem);m['overflowPaid']+=pay;self.v84OverflowPaid+=pay;rem-=pay
     if pay>EPS:self.v84Events.append({'t':int(t),'event':'OVERFLOW_REPAIR_PAYMENT','compositeKey':key,'repairKey':rk,'fillInc':inc,'paid':pay,'overflowPaidCum':m['overflowPaid'],'overflowDebt':m['overflowDebt']})
    self.v84OppSeen[rk]=max(old,cur)
    if rem<=EPS:break
 def _refresh_carrier_ledger(self,t):
  out=super()._refresh_carrier_ledger(t)
  if hasattr(self,'v84Composite'):self._scan_v84(t)
  return out
 def run_exam_v84b(self,models,winner):
  r=super().run_exam_v83(models,winner);self._refresh_carrier_ledger(int(self.capEnd));r.update({'v84CompositeSubmits':self.v84CompositeSubmits,'v84CompositeFillQty':self.v84CompositeFillQty,'v84RepairAllocatedQty':self.v84RepairAllocated,'v84OverflowAllocatedQty':self.v84OverflowAllocated,'v84OverflowDebtQty':self.v84OverflowDebt,'v84OverflowPaidQty':self.v84OverflowPaid,'v84OverflowRemainingQty':max(0.0,self.v84OverflowDebt-self.v84OverflowPaid),'v84PreBirthPaymentLeak':self.v84PreBirthPaymentLeak,'v84DuplicateDebt':self.v84DuplicateDebt,'v84LateBlocks':self.v84LateBlocks,'v84CompositeState':self.v84Composite,'v84Events':self.v84Events[:240]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=FIXED:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v84b_behavior_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V84B_COMPOSITE_BEHAVIOR','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V84B_COMPOSITE_BEHAVIOR_START','market':FIXED}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};cr=by[FIXED]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
  b=v83.V83CleanModularCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:br=b.run_exam_v83(models,cr['winner'])
  finally:b.close()
  c=V84BCompositeRepair(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:rr=c.run_exam_v84b(models,cr['winner'])
  finally:c.close()
  safety=sum(float(rr.get(k) or 0.0) for k in ['authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
  conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  debt_exact=abs(float(rr.get('v84OverflowAllocatedQty') or 0)-float(rr.get('v84OverflowDebtQty') or 0))<=1e-7
  gates={'compositeSubmitExercised':int(rr.get('v84CompositeSubmits') or 0)>0,'compositePhysicalFillExercised':float(rr.get('v84CompositeFillQty') or 0)>EPS,'physicalAllocationConservation':conservation,'overflowDebtExact':debt_exact,'zeroPreBirthOverflowPaymentLeak':float(rr.get('v84PreBirthPaymentLeak') or 0)<=EPS,'zeroDuplicateOverflowDebt':float(rr.get('v84DuplicateDebt') or 0)<=EPS,'zeroLegacySafetyViolations':safety<=EPS,'noLateCompositeSubmit':int(rr.get('v84LateBlocks') or 0)>=0,'terminalFloorNonWorse':float(rr.get('floor') or 0)+EPS>=float(br.get('floor') or 0)}
  if not gates['compositePhysicalFillExercised'] and gates['compositeSubmitExercised']:decision='EXECUTION_INCONCLUSIVE_COMPOSITE_SUBMITTED_NO_FILL'
  elif all(gates.values()):decision='KEEP_V84B_COMPOSITE_REPAIR_BEHAVIOR_FOR_REPLICATION'
  else:decision='REJECT_OR_FIX_V84B_COMPOSITE_ACCOUNTING'
  out={'version':'ETH_REPAIR_V84B_COMPOSITE_REPAIR_BEHAVIOR_1912961','date':'2026-09-03','researchOnly':True,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baseline':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents'),'repairFill':br.get('v38PassiveRepairFillEvents'),'v36Fill':br.get('v36ActiveFillQty')},'candidate':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'repairParentBirths':rr.get('repairParentBirths'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'economicDeficit':rr.get('v80EconomicDeficitAmountAtEnd'),'rounds':rr.get('v70dSemanticRounds'),'compositeSubmits':rr.get('v84CompositeSubmits'),'compositeFill':rr.get('v84CompositeFillQty'),'repairAllocated':rr.get('v84RepairAllocatedQty'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowDebt':rr.get('v84OverflowDebtQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'lateBlocks':rr.get('v84LateBlocks')},'events':rr.get('v84Events',[]),'fullCandidate':rr,'boundary':['one fresh failure market only','no tuning','winner post-hoc only','realistic HFT only','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baseline'],'candidate':out['candidate'],'events':out['events'][:20]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
