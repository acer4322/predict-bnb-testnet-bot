from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v70d=sib('eth_v70d_for_v70f','run_eth_repair_v70d_parallel_relay_hft_smoke.py')
v65=v70d.v65;v38=v70d.v38;v1=v70d.v1;EPS=1e-9

class V70FEarlyClockParallelRelay(v70d.V70DParallelRelay):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v70fClockRows=[];self.v70fEligibleConsumed=set();self.v70fRepairPreservationViolations=0;self.v70fGlobalOccupancyBlocks=0
 def _score_state(self,t,after_kind):
  # V70F disables the rejected post-fill V70D bind point. Reservation authority exists only at Repair submit clock.
  return None
 def _reserve_expand_at_repair_submit(self,t,repair_key,repair_entry,pE):
  row={'t':int(t),'repairKey':repair_key,'pExpand':float(pE),'eligible':True,'submit':False,'reason':'EARLY_CLOCK'}
  if repair_key in self.v70fEligibleConsumed:
   row['reason']='CLOCK_ALREADY_CONSUMED';self.v70fClockRows.append(row);return
  self.v70fEligibleConsumed.add(repair_key)
  if int(self.capEnd)-int(t)<=180000:
   row['reason']='LATE';self.v70fClockRows.append(row);return
  # Preserve the just-created Repair responsibility exactly as submitted.
  before={k:repair_entry.get(k) for k in ('side','objectiveId','objectiveRole','submittedQty','actualFilled','submittedAt','parentId','lane')}
  if self._expand_occupied():
   self.v70fGlobalOccupancyBlocks+=1;row['reason']='GLOBAL_EXPAND_OCCUPIED';self.v70fClockRows.append(row);return
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):
   row['reason']='NO_THESIS';self.v70fClockRows.append(row);return
  qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('ask') is None:
   row['reason']='NO_QUOTES';self.v70fClockRows.append(row);return
  ask=float(qv[side]['ask']);quota=1.0/ask if ask>EPS else math.inf
  if not math.isfinite(quota) or quota<=EPS:
   row['reason']='VENUE_MIN_INFEASIBLE';self.v70fClockRows.append(row);return
  oid=self._new_objective('EXPAND',side)['id'];n=self.n;self.n+=1;native_side,native_price=v1.ex.native_order(side,ask)
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(quota),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(quota),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
  except Exception as ex:
   row.update({'reason':'SUBMIT_EXCEPTION','error':str(ex)});self.v70fClockRows.append(row);return
  key=f'{side}_{n}'
  self.orders[key]={'n':n,'side':side,'price':ask,'qty':quota,'cum':0.0,'placed':int(t),'status':'NEW','objective_role':'EXPAND','objective_id':oid,'execution_role':'TAKER_ACTIVE_EXPAND_SHARED'}
  self.placeHist.append((int(t),side,quota,ask));self.submits+=1;self.localPending[side][key]={'remaining':quota,'submitted':int(t)}
  self.submitRoleObserved[key]='EXPAND';self.submitRoleTruth[key]='EXPAND';self.submitRoleAuthorized[key]='EXPAND'
  self.carrierLedger[key]={'key':key,'side':side,'objectiveId':oid,'objectiveRole':'EXPAND','submittedQty':quota,'actualFilled':0.0,'submittedAt':int(t),'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t),'parentId':None,'lane':'ACTIVE_EXPAND_SHARED'}
  self.submitTrace.append({'t':int(t),'side':side,'qty':quota,'price':ask,'pendingRole':'EXPAND','parentId':None,'lane':'ACTIVE_EXPAND_SHARED'})
  self.v64Active[key]={'sourceKey':f'V70F_EARLY_{repair_key}','submitAt':int(t),'fillSeen':0.0,'qty':quota};self.v64Submits+=1
  self.v70dKeys.add(key);self.v70dFillSeen[key]=0.0;self.v70dReservations+=1
  after={k:self.carrierLedger.get(repair_key,{}).get(k) for k in before}
  if before!=after:self.v70fRepairPreservationViolations+=1
  row.update({'submit':True,'reason':'EARLY_PARALLEL_RESERVATION','key':key,'objectiveId':oid,'side':side,'ask':ask,'quota':quota,'submitRc':rc,'repairCarrierBefore':before,'repairCarrierAfter':after,'repairCarrierPreserved':before==after})
  self.v70fClockRows.append(row)
 def submit(self,t,side,p,q):
  role=str(getattr(self,'_pendingAuthorizedRole',None) or '');n0=self.n
  ok=super().submit(t,side,p,q)
  if ok and role=='REPAIR':
   repair_key=f'{side}_{n0}';f=self._coord_feature(t);pE=None
   if self.teacher is not None:
    x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
   e=self.carrierLedger.get(repair_key,{})
   row={'t':int(t),'repairKey':repair_key,'side':side,'qty':float(q),'price':float(p),'pExpand':pE,'remainingSec':(int(self.capEnd)-int(t))/1000.0,'carrierOutstandingAtSubmit':max(0.0,float(e.get('submittedQty') or q)-float(e.get('actualFilled') or 0.0)),'coordDebt':float(getattr(self,'_coordDebt',0.0)),'repairProgressFrac':float(f.get('repairProgressFrac') or 0.0)}
   eligible=bool(pE is not None and pE>=0.5 and int(self.capEnd)-int(t)>180000 and row['carrierOutstandingAtSubmit']>EPS)
   if eligible:self._reserve_expand_at_repair_submit(t,repair_key,e,pE)
   else:
    row.update({'eligible':False,'submit':False,'reason':'MODEL_OR_TIME_NOT_ELIGIBLE'});self.v70fClockRows.append(row)
  return ok
 def run_exam_v70f(self,models,winner):
  r=super().run_exam_v70d(models,winner)
  r.update({'v70fRepairSubmitClockRows':self.v70fClockRows,'v70fEligibleConsumedCount':len(self.v70fEligibleConsumed),'v70fRepairPreservationViolations':self.v70fRepairPreservationViolations,'v70fGlobalOccupancyBlocks':self.v70fGlobalOccupancyBlocks})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v70f_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V70F_EARLY_CLOCK_PARALLEL_RELAY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V70F_EARLY_CLOCK_PARALLEL_RELAY_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v65.V65GlobalExpandDedup(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v65(models,cr['winner'])
   finally:b.close()
   c=V70FEarlyClockParallelRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v70f(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':rr});print(json.dumps({'marketId':mid,'reservations':rr['v70dParallelReservations'],'fill':rr['v70dPhysicalExpandFillQty'],'debt':rr['v70dGenerationDebtQty'],'paid':rr['v70dGenerationPaidQty'],'rounds':[br['v64Rounds'],rr['v70dSemanticRounds']],'repairPreservationViolations':rr['v70fRepairPreservationViolations'],'globalOccupancyBlocks':rr['v70fGlobalOccupancyBlocks']},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.0) for x in rows)
  agg={'markets':len(rows),'parallelReservations':int(sm('candidate','v70dParallelReservations')),'eligibleClocksConsumed':int(sm('candidate','v70fEligibleConsumedCount')),'expandFillQty':sm('candidate','v70dPhysicalExpandFillQty'),'generationDebtQty':sm('candidate','v70dGenerationDebtQty'),'generationPaidQty':sm('candidate','v70dGenerationPaidQty'),'baselineRounds':int(sm('baseline','v64Rounds')),'candidateRounds':int(sm('candidate','v70dSemanticRounds')),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'preBirthPaymentLeak':sm('candidate','v70dPreBirthPaymentLeak'),'duplicateGenerationDebt':sm('candidate','v70dDuplicateGenerationDebt'),'repairPreservationViolations':int(sm('candidate','v70fRepairPreservationViolations')),'globalOccupancyBlocks':int(sm('candidate','v70fGlobalOccupancyBlocks'))}
  gates={'oneMarketCompletes':len(rows)==1,'eligibleClockConsumedAtMostOnce':agg['eligibleClocksConsumed']<=1,'parallelReservationObserved':agg['parallelReservations']>=1,'repairCarrierPreservedThroughReservation':agg['repairPreservationViolations']==0,'physicalConservation':abs(agg['expandFillQty']-agg['generationDebtQty'])<=1e-7,'zeroDoubleSpend':agg['duplicateGenerationDebt']<=EPS,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroOverOwned':agg['overOwned']==0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroUnauthorizedRoleDrift':agg['repairDrift']==0,'zeroPreBirthPaymentLeak':agg['preBirthPaymentLeak']<=EPS,'generationDebtExactAtBirth':abs(agg['expandFillQty']-agg['generationDebtQty'])<=1e-7,'semanticRoundsNonDecreasing':agg['candidateRounds']>=agg['baselineRounds']}
  safety=all(v for k,v in gates.items() if k not in ('parallelReservationObserved','semanticRoundsNonDecreasing'))
  if not gates['parallelReservationObserved']:
   decision='REJECT_V70F_EARLY_BIND_RELAY_SCHEDULING_OR_GLOBAL_OCCUPANCY_BLOCK'
  elif not safety:
   decision='REJECT_V70F_SAFETY_OR_ACCOUNTING'
  elif agg['expandFillQty']<=EPS:
   decision='NEED_MORE_EXECUTION_SUPPORT_EARLY_RESERVATION_REACHED_NO_PHYSICAL_FILL'
  elif not gates['semanticRoundsNonDecreasing']:
   decision='REJECT_V70F_PARALLEL_ACTIVE_BIND_MOVE_TO_RESPONSIBILITY_SCHEDULING_RELAY_OWNERSHIP'
  elif agg['generationDebtQty']>EPS and agg['generationPaidQty']<=EPS:
   decision='NEED_MORE_DATA_DEBT_BORN_NO_CONFIRMED_POST_BIRTH_PAYMENT'
  elif all(gates.values()):
   decision='KEEP_V70F_EARLY_CLOCK_PARALLEL_RELAY_FUNCTIONAL_SMOKE'
  else:decision='REJECT_V70F_FUNCTIONAL_GATE'
  out={'version':'ETH_REPAIR_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'actionAuthority':'ONE_MARKET_PREREGISTERED_FUNCTIONAL_SMOKE_ONLY','aggregate':agg,'gates':gates,'decision':decision,'rows':rows,'boundary':['matched V65 baseline','reservation only at Repair submit clock using frozen V44 fixed 0.5 authorization','Repair carrier preserved unchanged','at most one globally-deduped disjoint Expand reservation','active quantity equals venue-min explicit responsibility quota, not 10/18 sizing','confirmed Expand fill alone births equal generation debt','strictly post-birth opposite Repair increments alone pay generation debt','<=180s forbids new Expand','realistic HFT/Predict Tape only; no dream fill','no threshold/price/delay/qty sweep','no Stage-A/H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
