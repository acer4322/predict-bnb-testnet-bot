from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v73f=sib('eth_v73f_for_v73h','run_eth_repair_v73f_source_active_child_family_hft_smoke.py');v70g=v73f.v70g;v38=v73f.v38;v1=v70g.v70f.v70d.v1
class V73HPassiveRearm(v73f.V73FFamilyRelay):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v73hAttempted=False;self.v73hSubmits=0;self.v73hKey=None;self.v73hEvents=[];self.v73hFillSeen=0.0;self.v73hFillQty=0.0
 def _maybe_rearm(self,t):
  if self.v73hAttempted:return False
  born=self.v70dGenerationBornAt;debt=max(0.0,float(self.v70dGenerationDebt or 0)-float(self.v70dGenerationPaid or 0))
  if born is None or int(t)<=int(born) or debt<=EPS:return False
  self._refresh_carrier_ledger_no_v70d();side=self.v70dGenerationSide;pay='DOWN' if side=='UP' else 'UP'
  rp=getattr(self,'repairParent',None)
  if rp is None or str(rp.get('side'))!=pay:return False
  for k,e in self.carrierLedger.items():
   if str(e.get('objectiveRole') or '')=='REPAIR' and str(e.get('side'))==pay and not bool(e.get('terminalConfirmed')):
    out=max(0.0,float(e.get('submittedQty') or 0)-float(e.get('actualFilled') or 0))
    if out>EPS:return False
  qv=v1.quotes(self.book)
  if not qv or qv.get(pay,{}).get('bid') is None:return False
  floor,u,d,cost=self._raw_floor();weak=d if pay=='DOWN' else u;strong=u if pay=='DOWN' else d;gap=max(0.0,strong-weak)
  if gap<=EPS:return False
  ceiling=(strong-cost)/gap;bid=float(qv[pay]['bid']);raw=min(bid,ceiling);p=math.floor((raw+1e-10)*100.0)/100.0
  if not(EPS<p<1-EPS):return False
  legal=1.0/p;room=gap
  q=max(debt,legal)
  self.v73hAttempted=True
  row={'t':int(t),'event':'GENERATION_DEBT_PASSIVE_REARM_EVAL','generationBornAt':int(born),'paySide':pay,'debt':debt,'floor':float(floor),'gap':gap,'economicRepairCeiling':ceiling,'bestBid':bid,'submitPrice':p,'venueMinQty':legal,'repairRoom':room,'qty':q,'parentId':rp.get('id')}
  if q>room+EPS:
   row['result']='NO_LEGAL_QTY';self.v73hEvents.append(row);return False
  oid=self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None
  if oid is None:
   row['result']='NO_EXISTING_REPAIR_OBJECTIVE';self.v73hEvents.append(row);return False
  self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=rp.get('id');self._pendingLane='V73H_GENERATION_DEBT_PASSIVE_REARM';n0=self.n
  ok=self.submit(t,pay,p,q)
  row.update({'result':'SUBMIT' if ok else 'SUBMIT_FAILED','objectiveId':oid})
  if ok:self.v73hSubmits+=1;self.v73hKey=f'{pay}_{n0}';row['key']=self.v73hKey
  self.v73hEvents.append(row);return bool(ok)
 def _scan_v73h(self,t):
  if not self.v73hKey:return
  self._refresh_carrier_ledger_no_v70d();cur=float(self.carrierLedger.get(self.v73hKey,{}).get('actualFilled') or 0)
  if cur>self.v73hFillSeen+EPS:
   inc=cur-self.v73hFillSeen;self.v73hFillSeen=cur;self.v73hFillQty+=inc;self.v73hEvents.append({'t':int(t),'event':'GENERATION_DEBT_PASSIVE_REARM_FILL','key':self.v73hKey,'incQty':inc,'cumQty':cur,'generationPaid':float(self.v70dGenerationPaid or 0)})
 def process(self,t):
  super().process(t);self._scan_v73h(t);self._maybe_rearm(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._scan_v73h(t);self._maybe_rearm(t)
 def run_exam_v73h(self,models,winner):
  r=super().run_exam_v73f(models,winner);self._scan_v73h(int(self.capEnd));r.update({'v73hAttempted':self.v73hAttempted,'v73hSubmits':self.v73hSubmits,'v73hKey':self.v73hKey,'v73hFillQty':self.v73hFillQty,'v73hEvents':self.v73hEvents});return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=1825353:raise ValueError('fixed market must be 1825353')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v73h_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V73H_PASSIVE_REARM','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V73H_PASSIVE_REARM_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};cr=by[a.market_id]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{a.market_id}.json.xz'
  b=v73f.V73FFamilyRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:br=b.run_exam_v73f(models,cr['winner'])
  finally:b.close()
  c=V73HPassiveRearm(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:rr=c.run_exam_v73h(models,cr['winner'])
  finally:c.close()
  safety={'truthMismatch':float(rr.get('authorizedSubmitWithTruthRoleMismatch') or 0),'overOwned':float(rr.get('overOwnedSubmitViolations') or 0),'responsibilityOverfill':float(rr.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(rr.get('repairToExpandAtFirstFill') or 0),'preBirthLeak':float(rr.get('v70dPreBirthPaymentLeak') or 0),'duplicateDebt':float(rr.get('v70dDuplicateGenerationDebt') or 0)}
  agg={'baselineDebt':float(br.get('v70dGenerationDebtQty') or 0),'baselinePaid':float(br.get('v70dGenerationPaidQty') or 0),'candidateDebt':float(rr.get('v70dGenerationDebtQty') or 0),'candidatePaid':float(rr.get('v70dGenerationPaidQty') or 0),'candidateRemaining':float(rr.get('v70dGenerationRemainingQty') or 0),'rearmSubmits':int(rr.get('v73hSubmits') or 0),'rearmFillQty':float(rr.get('v73hFillQty') or 0),'maxResponsibilitiesPerGeneration':int(rr.get('v70gMaxResponsibilitiesPerGeneration') or 0)}
  gates={'rearmSubmitExercised':agg['rearmSubmits']==1,'actualRepairFillExercised':agg['rearmFillQty']>EPS,'generationPaymentObserved':agg['candidatePaid']>agg['baselinePaid']+EPS,'generationRemainingReduced':agg['candidateRemaining']<max(0.0,agg['baselineDebt']-agg['baselinePaid'])-EPS,'maxOneExpandResponsibilityPerGeneration':agg['maxResponsibilitiesPerGeneration']<=1,'zeroSafetyAccountingViolation':all(abs(x)<=EPS for x in safety.values())}
  decision='KEEP_V73H_PASSIVE_REARM_REPLICATE_SMALL' if all(gates.values()) else ('REJECT_V73H_SUBMITTED_NO_FILL_AUDIT_QUEUE_REPRICE' if agg['rearmSubmits']==1 and agg['rearmFillQty']<=EPS else 'REJECT_V73H_FUNCTIONAL_GATE')
  out={'version':'ETH_REPAIR_V73H_GENERATION_DEBT_PASSIVE_REARM_SMOKE','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'fixedMarket':a.market_id,'aggregate':agg,'safety':safety,'gates':gates,'decision':decision,'events':rr.get('v73hEvents',[]),'baselineV73F':br,'candidateV73H':rr,'boundary':['same Repair parent/objective reused','one-shot generation-scoped passive rearm','economic ceiling frontier only','realistic-HFT/Predict Tape','no tuning','no winner/PnL gate','no H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'safety':safety,'gates':gates,'events':out['events']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
