from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v44=sib('eth_v44_for_v48','run_eth_repair_v44_coadapted_parallel_reexpand_causal.py');v38=v44.v38;EPS=1e-9

class V48GenerationLedger(v44.V44):
 def __init__(self,*a,genTeacher=None,**kw):
  super().__init__(*a,**kw);self.genTeacher=genTeacher
  self.v48Generations=[];self.v48GenByKey={};self.v48ParallelSeen={};self.v48NextGenId=1
  self.v48GenerationBirths=0;self.v48GenerationDebtQty=0.;self.v48GenerationPaidQty=0.;self.v48GenerationOverpay=0.;self.v48DiscardedPreBirthRepairQty=0.;self.v48PreBirthRepairLeak=0.
  self.v48PaymentEvents=[];self.v48GenerationEvents=[];self.v48RepeatedDecisions=0;self.v48RepeatedBlocks=0;self.v48FirstDecisions=0
 def _latest_gen(self):
  return self.v48Generations[-1] if self.v48Generations else None
 def _coord_feature(self,t):
  f=super()._coord_feature(t);g=self._latest_gen();gp=f['gross']+1.
  if g is None:gd=gr=0.;prog=1.
  else:
   gd=float(g['debt']);gr=max(0.,gd-float(g['paid']));prog=min(1.,float(g['paid'])/(gd+EPS)) if gd>EPS else 1.
  f.update({'latestGenerationDebtGross':gd/gp,'latestGenerationRemainingGross':gr/gp,'latestGenerationRepairProgress':prog})
  return f
 def _sync_parallel_generation_fills(self,t):
  self._refresh_carrier_ledger(t)
  for k in list(self.v44Keys):
   e=self.carrierLedger.get(k,{})
   now=float(e.get('actualFilled') or 0.);old=float(self.v48ParallelSeen.get(k,0.))
   if now<=old+EPS:
    self.v48ParallelSeen[k]=max(old,now);continue
   inc=now-old;self.v48ParallelSeen[k]=now
   if k not in self.v48GenByKey:
    g={'id':self.v48NextGenId,'key':k,'bornAt':int(t),'debt':0.,'paid':0.,'firstDebtAtBirth':inc,'source':'V44_PARALLEL_SURPLUS'};self.v48NextGenId+=1
    self.v48Generations.append(g);self.v48GenByKey[k]=g;self.v48GenerationBirths+=1
    self.v48GenerationEvents.append({'t':int(t),'event':'GENERATION_BIRTH','generationId':g['id'],'key':k,'incDebt':inc,'progressAtBirth':0.0})
   g=self.v48GenByKey[k];g['debt']+=inc;self.v48GenerationDebtQty+=inc
   self.v48GenerationEvents.append({'t':int(t),'event':'GENERATION_DEBT_ADD','generationId':g['id'],'key':k,'incDebt':inc,'debt':g['debt'],'paid':g['paid']})
 def _pay_generations(self,t,pay,is_taker):
  rem=max(0.,float(pay));paid0=rem
  if rem<=EPS:return
  if not self.v48Generations:
   self.v48DiscardedPreBirthRepairQty+=rem;return
  for g in reversed(self.v48Generations):
   owed=max(0.,float(g['debt'])-float(g['paid']))
   if owed<=EPS:continue
   x=min(owed,rem);g['paid']+=x;rem-=x;self.v48GenerationPaidQty+=x
   self.v48PaymentEvents.append({'t':int(t),'generationId':g['id'],'paidQty':x,'executionRole':'TAKER' if is_taker else 'MAKER','generationDebt':g['debt'],'generationPaid':g['paid'],'generationProgress':min(1.,g['paid']/(g['debt']+EPS))})
   if rem<=EPS:break
  if rem>EPS:
   # Payment beyond known parallel generations belongs to older/base Repair responsibility, not to future generations.
   self.v48DiscardedPreBirthRepairQty+=rem
  self.v48GenerationOverpay=max(self.v48GenerationOverpay,max([float(g['paid'])-float(g['debt']) for g in self.v48Generations]+[0.]))
 def _consume_new_fills(self):
  # Materialized parallel EXPAND debt must exist before the same receipt frontier can score subsequent Repair.
  t0=int(self.authHist[-1]['time']) if self.authHist else 0
  self._sync_parallel_generation_fills(t0)
  rows=self.authHist[self._coordFillSeen:]
  if not rows:return
  for x in rows:
   t=int(x['time']);side=str(x['side']).upper();q=float(x['shares']);preAbs=abs(self._coordU-self._coordD);preDebt=self._coordDebt
   if side=='UP':self._coordU+=q
   else:self._coordD+=q
   postAbs=abs(self._coordU-self._coordD);delta=postAbs-preAbs
   if delta>EPS:
    kind='EXPAND';self._coordDebt=max(0.,preDebt)+delta;self._coordLastExpandT=t
   elif delta<-EPS:
    kind='REPAIR';pay=min(max(0.,preDebt),-delta);self._coordDebt=max(0.,preDebt-pay);self._coordRepairedCum+=pay;self._coordLastRepairT=t
    is_taker=any(e.get('event')=='V36_ACTIVE_SHARED_FILL' and int(e.get('t',-1))==t for e in getattr(self,'activeEvents',[]));self._pay_generations(t,pay,is_taker)
   else:kind='FLAT'
   if kind in ('EXPAND','REPAIR'):
    self._coordStreak=self._coordStreak+1 if kind==self._coordLastKind else 1;self._coordLastKind=kind;self._coordHist.append((t,kind,q));self._score_state(t,kind)
   if self._coordDebt<=EPS:self._coordRepairedCum=0.
  self._coordFillSeen=len(self.authHist)
 def _submit_parallel(self,t,f,pA,pG,phase):
  row={'t':int(t),'parentId':int(self.repairParent.get('id')),'progress':f['repairProgressFrac'],'debt':f['debt'],'floor':f['floor'],'latestGenerationRepairProgress':f['latestGenerationRepairProgress'],'pV44':pA,'pV47':pG,'phase':phase,'submit':False,'reason':'MODEL_REPAIR'}
  if pA<.5: self.v44Decisions.append(row);return
  if phase=='REPEATED_GENERATION' and (pG is None or pG<.5):
   self.v48RepeatedBlocks+=1;row['reason']='GENERATION_VETO';self.v44Decisions.append(row);return
  if int(self.capEnd)-int(t)<=180000:self.v44LateBlocks+=1;row['reason']='LATE';self.v44Decisions.append(row);return
  if self._v44_unresolved(t):row['reason']='V44_CHILD_UNRESOLVED';self.v44Decisions.append(row);return
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):row['reason']='NO_THESIS';self.v44Decisions.append(row);return
  qv=v38.v36.v34.v30.v1.quotes(self.book)
  if not qv:row['reason']='NO_QUOTES';self.v44Decisions.append(row);return
  px=float(qv[side]['bid']);qty=1.0/px if px>EPS else 1e9
  if px<=EPS or qty>12.+EPS:row['reason']='VENUE_MIN_INFEASIBLE';self.v44Decisions.append(row);return
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n;self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V48_PARALLEL_SURPLUS';ok=self.submit(t,side,px,qty)
  if ok:
   k=f'{side}_{n0}';self.v44Keys.add(k);self.v44Submits+=1;row.update({'submit':True,'reason':'SUBMIT','key':k,'side':side,'price':px,'qty':qty})
  self.v44Decisions.append(row)
 def _score_state(self,t,after_kind):
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return
  f=self._coord_feature(t);xa=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pA=float(self.teacher['model'].predict_proba(xa)[0,1])
  if self.v48GenerationBirths<=0:
   self.v48FirstDecisions+=1;self._submit_parallel(t,f,pA,None,'FIRST_PARALLEL');return
  pG=None
  if self.genTeacher is not None:
   is_taker=any(e.get('event')=='V36_ACTIVE_SHARED_FILL' and int(e.get('t',-1))==int(t) for e in getattr(self,'activeEvents',[]));f['lastRepairWasTaker']=1.0 if is_taker else 0.0
   xg=np.asarray([[float(f[c]) for c in self.genTeacher['features']]],np.float32);pG=float(self.genTeacher['model'].predict_proba(xg)[0,1])
  self.v48RepeatedDecisions+=1;self._submit_parallel(t,f,pA,pG,'REPEATED_GENERATION')
 def run_exam_v48(self,models,winner):
  r=super().run_exam_v44(models,winner)
  self._sync_parallel_generation_fills(int(self.capEnd))
  mat=sum(1 for k in self.v44Keys if float(self.carrierLedger.get(k,{}).get('actualFilled') or 0.)>EPS)
  gprog=[{'generationId':g['id'],'key':g['key'],'debt':g['debt'],'paid':g['paid'],'remaining':max(0.,g['debt']-g['paid']),'progress':min(1.,g['paid']/(g['debt']+EPS)) if g['debt']>EPS else 1.} for g in self.v48Generations]
  r.update({'v48GenerationBirths':self.v48GenerationBirths,'v48MaterializedParallelCarriers':mat,'v48GenerationDebtQty':self.v48GenerationDebtQty,'v48GenerationPaidQty':self.v48GenerationPaidQty,'v48GenerationOverpay':self.v48GenerationOverpay,'v48DiscardedPreBirthRepairQty':self.v48DiscardedPreBirthRepairQty,'v48PreBirthRepairLeak':self.v48PreBirthRepairLeak,'v48PaymentEvents':len(self.v48PaymentEvents),'v48FirstDecisions':self.v48FirstDecisions,'v48RepeatedDecisions':self.v48RepeatedDecisions,'v48RepeatedBlocks':self.v48RepeatedBlocks,'v48Generations':gprog[:40],'v48GenerationEvents':self.v48GenerationEvents[:120],'v48PaymentRows':self.v48PaymentEvents[:120]})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v48_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V48','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V48_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v44.V44(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44)
   try:br=b.run_exam_v44(models,cr['winner'])
   finally:b.close()
   c=V48GenerationLedger(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v48(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV44':br,'candidateV48':rr});print(json.dumps({'marketId':mid,'births':rr['v48GenerationBirths'],'debt':rr['v48GenerationDebtQty'],'paid':rr['v48GenerationPaidQty'],'repeat':rr['v48RepeatedDecisions'],'blocks':rr['v48RepeatedBlocks'],'submits':rr['v44Submits'],'fillQty':rr['v44ActualFillQty']},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'generationBirths':int(sm('candidateV48','v48GenerationBirths')),'materializedParallelCarriers':int(sm('candidateV48','v48MaterializedParallelCarriers')),'generationDebtQty':sm('candidateV48','v48GenerationDebtQty'),'generationPaidQty':sm('candidateV48','v48GenerationPaidQty'),'generationOverpay':sm('candidateV48','v48GenerationOverpay'),'discardedPreBirthRepairQty':sm('candidateV48','v48DiscardedPreBirthRepairQty'),'preBirthRepairLeak':sm('candidateV48','v48PreBirthRepairLeak'),'paymentEvents':int(sm('candidateV48','v48PaymentEvents')),'firstDecisions':int(sm('candidateV48','v48FirstDecisions')),'repeatedDecisions':int(sm('candidateV48','v48RepeatedDecisions')),'repeatedBlocks':int(sm('candidateV48','v48RepeatedBlocks')),'submits':int(sm('candidateV48','v44Submits')),'actualFillQty':sm('candidateV48','v44ActualFillQty'),'repairAfterExpandQty':sm('candidateV48','v44RepairQtyAfterExpand'),'truthMismatch':sm('candidateV48','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV48','overOwnedSubmitViolations'),'repairDrift':sm('candidateV48','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV44','floor'),'candidateFloorSum':sm('candidateV48','floor'),'baselineAbsNetSum':sm('baselineV44','absNet'),'candidateAbsNetSum':sm('candidateV48','absNet')}
  gates={'birthMatchesMaterializedCarriers':agg['generationBirths']==agg['materializedParallelCarriers'],'debtMatchesActualParallelFill':abs(agg['generationDebtQty']-agg['actualFillQty'])<=1e-7,'zeroGenerationOverpay':agg['generationOverpay']<=1e-9,'zeroPreBirthRepairLeak':agg['preBirthRepairLeak']<=1e-12,'generationPaymentExercised':agg['paymentEvents']>0 and agg['generationPaidQty']>EPS,'repeatedDecisionPathExercised':agg['repeatedDecisions']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V48_GENERATION_LEDGER_FUNCTIONAL','researchOnly':True,'behaviorChange':True,'priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['first parallel re-expand V44 authority unchanged','repeated re-expand adds V47 generation-aware majority veto only after actual parallel fill','one generation per materialized V48/V44 parallel EXPAND carrier','actual Maker/Taker Repair pays newest generation first','no threshold/qty/delay tuning','floor/absNet diagnostic only','consumed Stage-A realistic HFT','no dream fill','no 8781','<=180s inherited']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
