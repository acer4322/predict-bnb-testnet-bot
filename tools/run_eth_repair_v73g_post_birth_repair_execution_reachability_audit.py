from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v73f=sib('eth_v73f_for_v73g','run_eth_repair_v73f_source_active_child_family_hft_smoke.py');v70g=v73f.v70g;v38=v73f.v38;v1=v70g.v70f.v70d.v1
class V73GReachability(v73f.V73FFamilyRelay):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v73gRows=[];self._v73gSeenBirth=False
 def _audit(self,t):
  born=self.v70dGenerationBornAt
  rem=max(0.0,float(self.v70dGenerationDebt or 0)-float(self.v70dGenerationPaid or 0))
  if born is None or int(t)<=int(born) or rem<=EPS:return
  self._refresh_carrier_ledger_no_v70d();side=self.v70dGenerationSide;pay='DOWN' if side=='UP' else 'UP';qv=v1.quotes(self.book)
  floor,u,d,cost=self._raw_floor();weak=d if pay=='DOWN' else u;strong=u if pay=='DOWN' else d;gap=max(0.0,strong-weak)
  live=[];allrep=[]
  for k,e in self.carrierLedger.items():
   if str(e.get('objectiveRole') or '')!='REPAIR' or str(e.get('side'))!=pay:continue
   submitted=float(e.get('submittedQty') or 0);filled=float(e.get('actualFilled') or 0);out=max(0.0,submitted-filled)
   z={'key':k,'submitted':submitted,'filled':filled,'outstanding':out,'terminal':bool(e.get('terminalConfirmed')),'lane':e.get('lane'),'submittedAt':e.get('submittedAt')};allrep.append(z)
   if out>EPS and not bool(e.get('terminalConfirmed')):live.append(z)
  owned=sum(x['outstanding'] for x in live);room=max(0.0,gap-owned);ceiling=(strong-cost)/gap if gap>EPS else None
  bid=ask=None;legal=None
  if qv and qv.get(pay):
   bid=qv[pay].get('bid');ask=qv[pay].get('ask');bid=None if bid is None else float(bid);ask=None if ask is None else float(ask);legal=(1.0/bid if bid and bid>EPS else None)
  rp=getattr(self,'repairParent',None);authority=bool(rp is not None and str(rp.get('side'))==pay)
  if not authority:reason='NO_REPAIR_AUTHORITY'
  elif bid is None or ceiling is None or not(EPS<min(bid,ceiling)<1-EPS):reason='NO_LEGAL_PRICE'
  elif legal is None or legal>room+EPS:reason='NO_LEGAL_QTY'
  elif live:reason='CARRIER_OCCUPANCY_HANDOFF'
  else:reason='NO_REPAIR_SUBMIT_DESPITE_REACHABLE_STATE'
  self.v73gRows.append({'t':int(t),'bornAt':int(born),'generationSide':side,'paySide':pay,'remainingDebt':rem,'floor':float(floor),'gap':gap,'repairParentId':rp.get('id') if rp else None,'repairParentSide':rp.get('side') if rp else None,'authority':authority,'bid':bid,'ask':ask,'economicRepairCeiling':ceiling,'venueMinPassiveQty':legal,'liveRepairOutstandingQty':owned,'repairRoom':room,'liveRepairCarriers':live,'allRepairCarriers':allrep[-8:],'reason':reason})
 def process(self,t):
  super().process(t);self._audit(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._audit(t)
 def run_exam_v73g(self,models,winner):
  r=super().run_exam_v73f(models,winner);self._audit(int(self.capEnd));r['v73gReachabilityRows']=self.v73gRows[:1200];return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=1825353:raise ValueError('fixed market must be 1825353')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v73g_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V73G_REPAIR_REACHABILITY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V73G_REPAIR_REACHABILITY_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};cr=by[a.market_id]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];c=V73GReachability(tmp/'tapes'/f'{a.market_id}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:r=c.run_exam_v73g(models,cr['winner'])
  finally:c.close()
  rows=r['v73gReachabilityRows'];counts={}
  for z in rows:counts[z['reason']]=counts.get(z['reason'],0)+1
  postRep=[x for x in r.get('v53FillEvents',[]) if int(x.get('t') or 0)>int(r.get('v70dGenerationBornAt') or 10**18) and x.get('role') in ('PASSIVE_REPAIR','ACTIVE_REPAIR') and x.get('side')==('DOWN' if r.get('v70dGenerationSide')=='UP' else 'UP')]
  submitted=sum(1 for z in rows if z['liveRepairCarriers']);dominant=max(counts,key=counts.get) if counts else 'NO_POST_BIRTH_AUDIT_STATE'
  if postRep:classification='REPAIR_SUBMITTED_NO_FILL' if float(r.get('v70dGenerationPaidQty') or 0)<=EPS else 'PAYMENT_OBSERVED'
  elif submitted:classification='QUEUE_UNREACHABLE_OR_SUBMITTED_NO_FILL'
  elif dominant=='CARRIER_OCCUPANCY_HANDOFF':classification='CARRIER_OCCUPANCY_HANDOFF'
  else:classification=dominant
  safety={'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'overOwned':float(r.get('overOwnedSubmitViolations') or 0),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0),'preBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0),'duplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)}
  gates={'generationDebtObserved':float(r.get('v70dGenerationDebtQty') or 0)>EPS,'debtStillUnpaid':float(r.get('v70dGenerationRemainingQty') or 0)>EPS,'auditStatesObserved':len(rows)>0,'zeroSafetyAccountingChange':all(abs(x)<=EPS for x in safety.values()),'shadowOnly':True}
  decision='KEEP_V73G_DIAGNOSIS_'+classification if all(gates.values()) else 'REJECT_V73G_AUDIT_INVALID'
  out={'version':'ETH_REPAIR_V73G_POST_BIRTH_REPAIR_EXECUTION_REACHABILITY_AUDIT','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'fixedMarket':a.market_id,'classification':classification,'reasonCounts':counts,'aggregate':{'debt':r.get('v70dGenerationDebtQty'),'paid':r.get('v70dGenerationPaidQty'),'remaining':r.get('v70dGenerationRemainingQty'),'generationBornAt':r.get('v70dGenerationBornAt'),'auditStates':len(rows),'postBirthOppositeRepairFillEvents':len(postRep)},'safety':safety,'gates':gates,'decision':decision,'rows':rows,'functional':r,'boundary':['shadow only','V73F behavior unchanged','realistic-HFT/Predict Tape','no winner/PnL gate','no tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'classification':classification,'counts':counts,'aggregate':out['aggregate'],'safety':safety,'sampleRows':rows[:3]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
