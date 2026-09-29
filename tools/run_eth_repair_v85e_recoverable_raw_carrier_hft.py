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
v84=sib('eth_v84b_for_v85e','run_eth_repair_v84b_composite_repair_behavior_1912961.py');v83=v84.v83;v80=v84.v80;v38=v84.v38

class V85E(v84.V84BCompositeRepair):
 def __init__(self,*a,**kw):
  self.v85RawAt={};self.v85Recoverability=[];self.v85RawAccepted=0;self.v85Fallbacks=0
  super().__init__(*a,**kw)
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']))
  z=super().choose_authorized(t,end,proposed_side,proposed_qty)
  if z is not None and str(z[3])=='REPAIR':self.v85RawAt[int(t)]={'rawProposedQty':float(proposed_qty),'gapBeforeCap':gap,'authorizedQty':float(z[1]),'side':str(z[0])}
  return z
 def _recoverable_qty(self,t,side,qv,q):
  p=float(qv[side]['bid']);floor,u,d,cost=self._raw_floor();hu=float(u)+(q if side=='UP' else 0.0);hd=float(d)+(q if side=='DOWN' else 0.0);hc=float(cost)+q*p;hfloor=min(hu,hd)-hc
  repair='DOWN' if side=='UP' else 'UP';weak_qty=hd if repair=='DOWN' else hu;strong_qty=hu if repair=='DOWN' else hd;hgap=max(0.0,strong_qty-weak_qty)
  owned=0.0;gain=0.0;owned_rows=[]
  for key,e,rem in self.lane_unresolved('REPAIR'):
   if e.get('side')!=repair:continue
   rq=float(rem);op=float(self.orders.get(key,{}).get('price') or e.get('price') or 0.0);owned+=rq;g=rq*(1.0-op) if EPS<op<1-EPS else 0.0;gain+=g;owned_rows.append({'key':key,'remaining':rq,'price':op,'floorGainIfFilled':g})
  projected=hfloor+gain;room=max(0.0,hgap-owned);ceiling=(strong_qty-hc)/hgap if hgap>EPS else None;rbid=float(qv[repair]['bid']) if qv.get(repair,{}).get('bid') is not None else None;adm=min(rbid,float(ceiling)) if rbid is not None and ceiling is not None else None;need=legal=req=None;ok=False;reason=''
  if projected>=-EPS:ok=True;reason='EXISTING_REPAIR_RESERVATION_COVERS_PROJECTED_FLOOR'
  elif adm is None or not(EPS<adm<1-EPS):reason='NO_ADMISSIBLE_FUTURE_REPAIR_PRICE'
  else:
   need=max(0.0,-projected)/(1.0-adm);legal=1.0/adm;req=max(need,legal);ok=req<=room+EPS;reason='PASS' if ok else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM'
  return {'t':int(t),'side':side,'price':p,'qty':q,'floorBefore':float(floor),'hypFloorAfter':hfloor,'repairSide':repair,'ownedRepairQty':owned,'ownedRepairGain':gain,'ownedRepairRows':owned_rows,'projectedFloorAfterOwnedRepair':projected,'repairGapAfterExpand':hgap,'repairRoomAfterOwned':room,'economicRepairCeiling':ceiling,'repairBid':rbid,'admissibleFutureRepairPrice':adm,'futureNeedQty':need,'futureLegalQty':legal,'futureRequiredQty':req,'recoverable':bool(ok),'reason':reason}
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None:
   side,qty,oldp,role,oid=z;rb=getattr(self,'reserveBuilder',None)
   if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None and int(t)<=int(rb.get('pairDeadline') or -1):
    raw=min(12.0,float(self.v85RawAt.get(int(t),{}).get('rawProposedQty',qty)));ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));p=float(qv[side]['bid']);legal=1.0/p if p>EPS else math.inf
    if raw>gap+EPS and math.isfinite(legal) and raw+EPS>=legal and int(self.capEnd)-int(t)>180000:
     rec=self._recoverable_qty(t,side,qv,raw);rec.update({'rawProposedQty':raw,'oldCappedRepairQty':float(qty),'gap':gap});self.v85Recoverability.append(rec)
     if rec['recoverable']:
      self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=int(self.repairParent.get('id')) if self.repairParent else None;self._pendingLane='V85E_RECOVERABLE_RAW_COMPOSITE';n0=self.n;ok=self.submit(t,side,p,raw)
      if ok:
       key=f'{side}_{n0}';roles_this_tick.add('REPAIR');self.packageRepairEval+=1;self.packageRepairAccept+=1;self.v84CompositeSubmits+=1;self.v85RawAccepted+=1
       self.v84Composite[key]={'key':key,'side':side,'parentId':int(self.repairParent.get('id')) if self.repairParent else None,'price':p,'submittedQty':raw,'gapAtSubmit':gap,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0}
       ev={'t':int(t),'event':'V85E_RECOVERABLE_RAW_COMPOSITE_SUBMIT','key':key,'side':side,'price':p,'qty':raw,'repairBudget':gap,'overflowBudget':max(0.0,raw-gap),'recoverability':rec};self.v84Events.append(ev)
       if rb is not None:rb.setdefault('repairKeys',[]).append(key)
       return True
    self.v85Fallbacks+=1
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_v85e(self,models,winner):
  r=self.run_exam_v84b(models,winner);r.update({'v85RawAccepted':self.v85RawAccepted,'v85Fallbacks':self.v85Fallbacks,'v85Recoverability':self.v85Recoverability[:200]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v85e_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V85E_RECOVERABLE_RAW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V85E_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  b=mk(v84.V84BCompositeRepair)
  try:br=b.run_exam_v84b(models,cr['winner'])
  finally:b.close()
  c=mk(V85E)
  try:rr=c.run_v85e(models,cr['winner'])
  finally:c.close()
  legacy=int(rr.get('overOwnedSubmitViolations') or 0);comps=int(rr.get('v84CompositeSubmits') or 0);unexpl=max(0,legacy-comps);other=sum(float(rr.get(k) or 0) for k in ['authorizedSubmitWithTruthRoleMismatch','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
  gates={'rawRecoverabilityPathExercised':int(rr.get('v85RawAccepted') or 0)>0 or int(rr.get('v85Fallbacks') or 0)>0,'physicalAllocationConservation':abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7,'overflowDebtExact':abs(float(rr.get('v84OverflowAllocatedQty') or 0)-float(rr.get('v84OverflowDebtQty') or 0))<=1e-7,'zeroPreBirthPaymentLeak':float(rr.get('v84PreBirthPaymentLeak') or 0)<=EPS,'zeroDuplicateOverflowDebt':float(rr.get('v84DuplicateDebt') or 0)<=EPS,'zeroUnexplainedLegacyOverOwned':unexpl==0,'zeroOtherSafetyViolations':other<=EPS,'terminalFloorNonWorseThanV84B':float(rr.get('floor') or 0)+EPS>=float(br.get('floor') or 0)}
  secondary={'largerConfirmedOverflow':float(rr.get('v84OverflowAllocatedQty') or 0)>float(br.get('v84OverflowAllocatedQty') or 0)+EPS,'postBirthOverflowPayment':float(rr.get('v84OverflowPaidQty') or 0)>EPS,'moreShareSettlements':int(rr.get('v80ShareRepairSettlements') or 0)>int(br.get('v80ShareRepairSettlements') or 0)}
  if not int(rr.get('v85RawAccepted') or 0):decision='EXECUTION_INCONCLUSIVE_RAW_PATH_NOT_ACCEPTED'
  elif all(gates.values()) and any(secondary.values()):decision='KEEP_V85E_RECOVERABLE_RAW_FOR_FRESH4_REPLICATION'
  else:decision='REJECT_V85E_SAFETY_OR_TERMINAL_ECONOMICS'
  out={'version':'ETH_REPAIR_V85E_RECOVERABLE_RAW_CARRIER_HFT','date':'2026-09-03','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'secondary':secondary,'v84b':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents'),'overflowAllocated':br.get('v84OverflowAllocatedQty'),'overflowPaid':br.get('v84OverflowPaidQty'),'shareSettlements':br.get('v80ShareRepairSettlements')},'v85e':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'rawAccepted':rr.get('v85RawAccepted'),'fallbacks':rr.get('v85Fallbacks'),'compositeSubmits':rr.get('v84CompositeSubmits'),'compositeFill':rr.get('v84CompositeFillQty'),'repairAllocated':rr.get('v84RepairAllocatedQty'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'repairParentBirths':rr.get('repairParentBirths'),'legacyOverOwned':legacy,'unexplainedOverOwned':unexpl},'recoverability':rr.get('v85Recoverability',[]),'events':rr.get('v84Events',[])[:120],'fullCandidate':rr,'boundary':['OUR raw pre-cap qty only','existing whole-portfolio recoverability generalized to exact candidate qty','no Target qty runtime input','no tuning','realistic-HFT only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'secondary':secondary,'v84b':out['v84b'],'v85e':out['v85e'],'recoverability':out['recoverability'][:6]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
