from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v65=sib('eth_v65_for_v70d','run_eth_repair_v65_global_expand_ownership_dedup_smoke.py')
v38=v65.v38;v1=v65.v64.v1;EPS=1e-9

class V70DParallelRelay(v65.V65GlobalExpandDedup):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v70dReservations=0;self.v70dKeys=set();self.v70dEvents=[];self.v70dFillSeen={}
  self.v70dPhysicalFillQty=0.0;self.v70dGenerationDebt=0.0;self.v70dGenerationPaid=0.0
  self.v70dGenerationSide=None;self.v70dGenerationBornAt=None;self.v70dRepairPaySeen={}
  self.v70dPreBirthPaymentLeak=0.0;self.v70dDuplicateGenerationDebt=0.0;self.v70dNoReach=0
 def _live_repair_carriers(self):
  self._refresh_carrier_ledger_no_v70d()
  out=[]
  for k,e in self.carrierLedger.items():
   if str(e.get('objectiveRole') or '')!='REPAIR' or bool(e.get('terminalConfirmed')):continue
   rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
   if rem>EPS:out.append((k,e,rem))
  return out
 def _refresh_carrier_ledger_no_v70d(self):
  return super()._refresh_carrier_ledger(int(getattr(self,'capEnd',0) or 0))
 def _expand_occupied(self):
  self._refresh_carrier_ledger_no_v70d()
  for k,e in self.carrierLedger.items():
   if str(e.get('objectiveRole') or '')!='EXPAND':continue
   rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
   if rem>EPS and not bool(e.get('terminalConfirmed')):return True
  return False
 def _score_state(self,t,after_kind):
  # V70D replaces V44's passive Expand child only at the same pre-authorized model clock.
  # A valid Repair carrier must remain live; the new Active Expand gets a separate explicit objective quota.
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return
  f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
  row={'t':int(t),'parentId':int(self.repairParent.get('id')),'pExpand':pE,'submit':False,'reason':'MODEL_REPAIR'}
  if pE<.5:self.v70dEvents.append(row);return
  if int(self.capEnd)-int(t)<=180000:row['reason']='LATE';self.v70dEvents.append(row);return
  repairs=self._live_repair_carriers()
  if not repairs:self.v70dNoReach+=1;row['reason']='NO_PARALLEL_REPAIR_CARRIER';self.v70dEvents.append(row);return
  if self._expand_occupied():row['reason']='GLOBAL_EXPAND_OCCUPIED';self.v70dEvents.append(row);return
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):row['reason']='NO_THESIS';self.v70dEvents.append(row);return
  qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('ask') is None:row['reason']='NO_QUOTES';self.v70dEvents.append(row);return
  ask=float(qv[side]['ask']);quota=1.0/ask if ask>EPS else math.inf
  if not math.isfinite(quota) or quota<=EPS:row['reason']='VENUE_MIN_INFEASIBLE';self.v70dEvents.append(row);return
  oid=self._new_objective('EXPAND',side)['id'];n=self.n;self.n+=1;native_side,native_price=v1.ex.native_order(side,ask)
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(quota),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(quota),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
  except Exception as ex:
   row.update({'reason':'SUBMIT_EXCEPTION','error':str(ex)});self.v70dEvents.append(row);return
  key=f'{side}_{n}'
  self.orders[key]={'n':n,'side':side,'price':ask,'qty':quota,'cum':0.0,'placed':int(t),'status':'NEW','objective_role':'EXPAND','objective_id':oid,'execution_role':'TAKER_ACTIVE_EXPAND_SHARED'}
  self.placeHist.append((int(t),side,quota,ask));self.submits+=1;self.localPending[side][key]={'remaining':quota,'submitted':int(t)}
  self.submitRoleObserved[key]='EXPAND';self.submitRoleTruth[key]='EXPAND';self.submitRoleAuthorized[key]='EXPAND'
  self.carrierLedger[key]={'key':key,'side':side,'objectiveId':oid,'objectiveRole':'EXPAND','submittedQty':quota,'actualFilled':0.0,'submittedAt':int(t),'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t),'parentId':None,'lane':'ACTIVE_EXPAND_SHARED'}
  self.submitTrace.append({'t':int(t),'side':side,'qty':quota,'price':ask,'pendingRole':'EXPAND','parentId':None,'lane':'ACTIVE_EXPAND_SHARED'})
  self.v64Active[key]={'sourceKey':f'V70D_MODEL_{oid}','submitAt':int(t),'fillSeen':0.0,'qty':quota};self.v64Submits+=1
  self.v70dKeys.add(key);self.v70dFillSeen[key]=0.0;self.v70dReservations+=1
  row.update({'submit':True,'reason':'PARALLEL_RESERVATION','key':key,'objectiveId':oid,'side':side,'ask':ask,'quota':quota,'submitRc':rc,'repairCarrierKeys':[k for k,_,_ in repairs],'repairOutstandingQty':sum(r for _,_,r in repairs)})
  self.v70dEvents.append(row)
 def _refresh_carrier_ledger(self,t):
  out=super()._refresh_carrier_ledger(t)
  if not hasattr(self,'v70dKeys'):return out
  # Expand actual fills create equal generation debt. Snapshot opposite Repair cumulative fill at birth.
  for k in list(self.v70dKeys):
   e=self.carrierLedger.get(k,{});cur=float(e.get('actualFilled') or 0.0);old=float(self.v70dFillSeen.get(k,0.0))
   if cur>old+EPS:
    inc=cur-old;self.v70dFillSeen[k]=cur;self.v70dPhysicalFillQty+=inc;side=e.get('side')
    if self.v70dGenerationSide is None:self.v70dGenerationSide=side
    elif self.v70dGenerationSide!=side:self.v70dDuplicateGenerationDebt+=inc
    if self.v70dGenerationBornAt is None:
     self.v70dGenerationBornAt=int(t);pay_side='DOWN' if side=='UP' else 'UP';baseline={}
     for rk,re in self.carrierLedger.items():
      if rk in self.v70dKeys or str(re.get('objectiveRole') or '')!='REPAIR' or re.get('side')!=pay_side:continue
      seen=float(re.get('actualFilled') or 0.0);self.v70dRepairPaySeen[rk]=seen
      if seen>EPS:baseline[rk]=seen
     self.v70dEvents.append({'t':int(t),'event':'GENERATION_BIRTH','expandKey':k,'side':side,'paySide':pay_side,'debtInc':inc,'repairFillBaseline':baseline})
    self.v70dGenerationDebt+=inc;self.v70dEvents.append({'t':int(t),'event':'EXPAND_FILL','key':k,'incQty':inc,'generationDebt':self.v70dGenerationDebt})
  if self.v70dGenerationDebt-self.v70dGenerationPaid>EPS and self.v70dGenerationSide in ('UP','DOWN'):
   pay_side='DOWN' if self.v70dGenerationSide=='UP' else 'UP'
   for rk,re in list(self.carrierLedger.items()):
    if rk in self.v70dKeys or str(re.get('objectiveRole') or '')!='REPAIR' or re.get('side')!=pay_side:continue
    cur=float(re.get('actualFilled') or 0.0);old=float(self.v70dRepairPaySeen.get(rk,cur if self.v70dGenerationBornAt is None else 0.0))
    if self.v70dGenerationBornAt is not None and int(t)<=int(self.v70dGenerationBornAt):
     self.v70dRepairPaySeen[rk]=max(old,cur);continue
    if cur>old+EPS:
     inc=cur-old;pay=min(inc,max(0.0,self.v70dGenerationDebt-self.v70dGenerationPaid));self.v70dGenerationPaid+=pay
     if pay>EPS:self.v70dEvents.append({'t':int(t),'event':'GENERATION_REPAIR_PAYMENT','key':rk,'side':pay_side,'fillIncQty':inc,'paidQty':pay,'generationPaid':self.v70dGenerationPaid})
    self.v70dRepairPaySeen[rk]=max(old,cur)
  return out
 def run_exam_v70d(self,models,winner):
  r=super().run_exam_v65(models,winner);self._refresh_carrier_ledger(int(self.capEnd));cs=self._cycle_stats(sorted(self.v53Fills,key=lambda x:(x['t'],x['key'])))
  r.update({'v70dParallelReservations':self.v70dReservations,'v70dPhysicalExpandFillQty':self.v70dPhysicalFillQty,'v70dGenerationDebtQty':self.v70dGenerationDebt,'v70dGenerationPaidQty':self.v70dGenerationPaid,'v70dGenerationRemainingQty':max(0.0,self.v70dGenerationDebt-self.v70dGenerationPaid),'v70dGenerationBornAt':self.v70dGenerationBornAt,'v70dPreBirthPaymentLeak':self.v70dPreBirthPaymentLeak,'v70dDuplicateGenerationDebt':self.v70dDuplicateGenerationDebt,'v70dNoReach':self.v70dNoReach,'v70dSemanticRounds':cs['repairExpandRepairRounds'],'v70dCompressedSequence':cs['compressedSequence'][:100],'v70dEvents':self.v70dEvents[:220]})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v70d_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V70D_PARALLEL_RELAY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V70D_PARALLEL_RELAY_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v65.V65GlobalExpandDedup(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v65(models,cr['winner'])
   finally:b.close()
   c=V70DParallelRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v70d(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':rr});print(json.dumps({'marketId':mid,'reservations':rr['v70dParallelReservations'],'fill':rr['v70dPhysicalExpandFillQty'],'debt':rr['v70dGenerationDebtQty'],'paid':rr['v70dGenerationPaidQty'],'rounds':[br['v64Rounds'],rr['v70dSemanticRounds']],'safety':[rr['authorizedSubmitWithTruthRoleMismatch'],rr['overOwnedSubmitViolations'],rr.get('v51ResponsibilityOverfill',0),rr['v70dPreBirthPaymentLeak']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.0) for x in rows)
  agg={'markets':len(rows),'parallelReservations':int(sm('candidate','v70dParallelReservations')),'expandFillQty':sm('candidate','v70dPhysicalExpandFillQty'),'generationDebtQty':sm('candidate','v70dGenerationDebtQty'),'generationPaidQty':sm('candidate','v70dGenerationPaidQty'),'baselineRounds':int(sm('baseline','v64Rounds')),'candidateRounds':int(sm('candidate','v70dSemanticRounds')),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'preBirthPaymentLeak':sm('candidate','v70dPreBirthPaymentLeak'),'duplicateGenerationDebt':sm('candidate','v70dDuplicateGenerationDebt'),'noReach':int(sm('candidate','v70dNoReach'))}
  gates={'oneMarketCompletes':len(rows)==1,'parallelReservationObserved':agg['parallelReservations']>=1,'physicalConservation':abs(agg['expandFillQty']-agg['generationDebtQty'])<=1e-7,'zeroDoubleSpend':agg['duplicateGenerationDebt']<=EPS,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroOverOwned':agg['overOwned']==0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroUnauthorizedRoleDrift':agg['repairDrift']==0,'zeroPreBirthPaymentLeak':agg['preBirthPaymentLeak']<=EPS,'generationDebtExactAtBirth':abs(agg['expandFillQty']-agg['generationDebtQty'])<=1e-7,'semanticRoundsNonDecreasing':agg['candidateRounds']>=agg['baselineRounds']}
  safety=all(v for k,v in gates.items() if k!='parallelReservationObserved')
  if not gates['parallelReservationObserved']:decision='REJECT_BIND_POINT_AUDIT_EARLIER_REPAIR_PARENT_RESERVATION_CLOCK'
  elif not safety:decision='REJECT_V70D_SAFETY_OR_ACCOUNTING'
  elif agg['generationDebtQty']>EPS and agg['generationPaidQty']<=EPS:decision='NEED_MORE_DATA_DEBT_BORN_NO_CONFIRMED_POST_BIRTH_PAYMENT'
  elif all(gates.values()):decision='KEEP_V70D_PARALLEL_RELAY_FUNCTIONAL_SMOKE'
  else:decision='REJECT_V70D_FUNCTIONAL_GATE'
  out={'version':'ETH_REPAIR_V70D_PARALLEL_RELAY_HFT_SMOKE','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'actionAuthority':'ONE_MARKET_PREREGISTERED_FUNCTIONAL_SMOKE_ONLY','aggregate':agg,'gates':gates,'decision':decision,'rows':rows,'boundary':['matched V65 baseline','existing passive Repair carrier must remain live when parallel Active Expand reservation is created','new Expand objective is model-authorized at existing V44 Repair-state clock and globally occupancy-deduped','active quantity equals explicit venue-min Expand responsibility quota, not 10/18-share sizing','confirmed Expand fill alone births equal generation debt','only strictly post-birth opposite Repair fill increments may pay debt','<=180s forbids new Expand','realistic HFT/Predict Tape only; no dream fill','no tuning/winner/PnL action authority','no Stage-A/H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
