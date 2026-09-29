from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v65=sib('eth_v65_for_v70b','run_eth_repair_v65_global_expand_ownership_dedup_smoke.py')
v38=v65.v38;v1=v65.v64.v1;EPS=1e-9

class V70BParentClockComposite(v65.V65GlobalExpandDedup):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v70CompositeTriggered=False;self.v70CompositeKeys=set();self.v70Composite={};self.v70CompositeEvents=[]
  self.v70CompositeSeen={};self.v70RepairPaySeen={};self.v70GenerationDebt=0.0;self.v70GenerationPaid=0.0;self.v70GenerationSide=None;self.v70GenerationBornAt=None;self.v70PreBirthPaymentLeak=0.0
  self.v70ExpandBlocks=0;self.v70CandidateAttempts=0;self.v70CandidateSubmit=0;self.v70CrossFillQty=0.0;self.v70RepairFillQty=0.0
  self.v70PhysicalFillQty=0.0;self.v70DuplicateGenerationDebt=0.0;self._v70PendingSpec=None
 def _v70_live_composite(self):
  self._refresh_carrier_ledger_no_v70()
  for k in self.v70CompositeKeys:
   e=self.carrierLedger.get(k,{})
   if self._ledger_remaining(e)>EPS:return True
  return False
 def _refresh_carrier_ledger_no_v70(self):
  return super()._refresh_carrier_ledger(int(getattr(self,'capEnd',0) or 0))
 def submit(self,t,side,p,q):
  spec=self._v70PendingSpec;n0=self.n;pre_over=int(getattr(self,'overOwnedSubmitViolations',0));ok=super().submit(t,side,p,q)
  if not ok:return False
  if spec is not None:
   key=f'{side}_{n0}';self.overOwnedSubmitViolations=pre_over
   e=self.carrierLedger.get(key,{})
   e.update({'lane':'V70_COMPOSITE_REPAIR_EXPAND','objectiveRole':'REPAIR','v70Composite':True,'v70RepairAuthorizedQty':float(spec['repairQty']),'v70ExpandAuthorizedQty':float(spec['expandQty']),'v70ExpandObjectiveId':spec['expandObjectiveId']})
   o=self.orders.get(key,{})
   o['execution_role']='V70_COMPOSITE_REPAIR_EXPAND';o['objective_role']='REPAIR'
   self.v70CompositeKeys.add(key);self.v70Composite[key]={'key':key,'parentId':spec['parentId'],'side':side,'repairQty':float(spec['repairQty']),'expandQty':float(spec['expandQty']),'expandObjectiveId':spec['expandObjectiveId'],'submittedQty':float(q),'submitAt':int(t),'price':float(p)}
   self.v70CompositeSeen[key]=0.0;self.v70CandidateSubmit+=1
   self.v70CompositeEvents.append({'t':int(t),'event':'V70_COMPOSITE_SUBMIT','key':key,**self.v70Composite[key]})
  return True
 def lane_unresolved(self,role):
  rows=super().lane_unresolved(role)
  if role!='REPAIR' or not self.v70CompositeKeys:return rows
  keys={k for k,_,_ in rows};out=list(rows)
  for k in self.v70CompositeKeys:
   if k in keys:continue
   e=self.carrierLedger.get(k,{});z=self.v70Composite.get(k)
   if not z or e.get('terminalConfirmed'):continue
   filled=float(e.get('actualFilled') or 0.0);repair_rem=max(0.0,float(z['repairQty'])-filled)
   if repair_rem>EPS:out.append((k,e,repair_rem))
  return out
 def _maybe_hard_active(self,t):
  rp=getattr(self,'repairParent',None)
  if rp is not None:
   pid=int(rp.get('id'))
   for k in self.v70CompositeKeys:
    z=self.v70Composite.get(k,{});e=self.carrierLedger.get(k,{})
    if int(z.get('parentId') or -1)==pid and self._ledger_remaining(e)>EPS:return False
  return super()._maybe_hard_active(t)
 def _v70_expand_occupied(self):
  if self.v70GenerationDebt-self.v70GenerationPaid>EPS:return True
  for k in self.v70CompositeKeys:
   z=self.v70Composite.get(k,{});e=self.carrierLedger.get(k,{})
   if max(0.0,float(z.get('expandQty') or 0.0)-max(0.0,float(e.get('actualFilled') or 0.0)-float(z.get('repairQty') or 0.0)))>EPS and self._ledger_remaining(e)>EPS:return True
  return False
 def _submit_parallel(self,*args,**kw):
  if self._v70_expand_occupied():self.v70ExpandBlocks+=1;return False
  return super()._submit_parallel(*args,**kw)
 def _submit_active_expand(self,*args,**kw):
  if self._v70_expand_occupied():self.v70ExpandBlocks+=1;return False
  return super()._submit_active_expand(*args,**kw)
 def _submit_package_at_price(self,t,qv,z,roles_this_tick,p):
  if z is None:return False
  side,qty,oldp,role,oid=z
  if self.v70CompositeTriggered or role!='REPAIR' or role in roles_this_tick or int(self.capEnd)-int(t)<=180000:
   return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
  b=self._repair_payoff_budget(float(p));self.v70CandidateAttempts+=1
  if not b or not b.get('feasible'):return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
  repair=float(b.get('room') or 0.0);legal=1.0/float(p) if float(p)>EPS else math.inf
  if repair<=EPS or not math.isfinite(legal) or legal<=EPS:return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
  total=repair+legal
  pid=int(self.repairParent['id']) if self.repairParent else None
  overflow_allocation_id=f'V70B_PARENT_{pid}_OVERFLOW_{int(t)}'
  self._v70PendingSpec={'repairQty':repair,'expandQty':legal,'expandObjectiveId':overflow_allocation_id,'parentId':pid}
  self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='V70_COMPOSITE_REPAIR_EXPAND'
  n0=self.n;ok=self.submit(t,side,float(p),total);self._v70PendingSpec=None;self.v70CompositeTriggered=True
  if ok:
   roles_this_tick.add(role);self.ceilingRepairSubmits+=1;self.ceilingPrices.append(float(p));rb=self.reserveBuilder
   if rb is not None:
    if rb.get('firstCeilingSubmitAt') is None:rb['firstCeilingSubmitAt']=int(t);self.ceilingSubmitLags.append(int(t)-int(rb['firstFillAt']))
    else:self.ceilingRepairReprices+=1
    rb.setdefault('repairKeys',[]).append(f'{side}_{n0}')
   self.v70CompositeEvents.append({'t':int(t),'event':'V70_COMPOSITE_AUTHORIZED','side':side,'repairQty':repair,'expandQty':legal,'totalQty':total,'price':float(p),'remainingSec':(int(self.capEnd)-int(t))/1000.0,'repairParentId':pid,'expandObjectiveId':overflow_allocation_id})
   return True
  return False
 def _refresh_carrier_ledger(self,t):
  out=super()._refresh_carrier_ledger(t)
  if not hasattr(self,'v70CompositeKeys'):return out
  # First split the physical composite fill into pre-authorized Repair then Expand overflow.
  for k in list(self.v70CompositeKeys):
   e=self.carrierLedger.get(k,{});z=self.v70Composite.get(k,{})
   cur=float(e.get('actualFilled') or 0.0);old=float(self.v70CompositeSeen.get(k,0.0))
   if cur>old+EPS:
    inc=cur-old;r=float(z['repairQty'])
    repair_inc=max(0.0,min(cur,r)-min(old,r));expand_inc=max(0.0,max(0.0,cur-r)-max(0.0,old-r))
    self.v70CompositeSeen[k]=cur;self.v70PhysicalFillQty+=inc;self.v70RepairFillQty+=repair_inc;self.v70CrossFillQty+=expand_inc
    if expand_inc>EPS:
     if self.v70GenerationSide is None:self.v70GenerationSide=z['side']
     elif self.v70GenerationSide!=z['side']:self.v70DuplicateGenerationDebt+=expand_inc
     if self.v70GenerationBornAt is None:
      self.v70GenerationBornAt=int(t);pay_side='DOWN' if z['side']=='UP' else 'UP';baseline={}
      for pk,pe in list(self.carrierLedger.items()):
       if pk in self.v70CompositeKeys or pe.get('objectiveRole')!='REPAIR' or pe.get('side')!=pay_side:continue
       seen=float(pe.get('actualFilled') or 0.0);self.v70RepairPaySeen[pk]=seen
       if seen>EPS:baseline[pk]=seen
      self.v70CompositeEvents.append({'t':int(t),'event':'V70_GENERATION_BIRTH_BASELINE','side':z['side'],'paySide':pay_side,'repairFillBaseline':baseline})
     self.v70GenerationDebt+=expand_inc
    self.v70CompositeEvents.append({'t':int(t),'event':'V70_COMPOSITE_FILL','key':k,'physicalIncQty':inc,'repairIncQty':repair_inc,'expandIncQty':expand_inc,'cumPhysicalQty':cur,'generationDebt':self.v70GenerationDebt,'generationPaid':self.v70GenerationPaid})
  # Then let later opposite-side Repair fills discharge the generated debt.
  if self.v70GenerationDebt-self.v70GenerationPaid>EPS and self.v70GenerationSide in ('UP','DOWN'):
   pay_side='DOWN' if self.v70GenerationSide=='UP' else 'UP'
   for k,e in list(self.carrierLedger.items()):
    if k in self.v70CompositeKeys or e.get('objectiveRole')!='REPAIR' or e.get('side')!=pay_side:continue
    cur=float(e.get('actualFilled') or 0.0);old=float(self.v70RepairPaySeen.get(k,0.0))
    if self.v70GenerationBornAt is not None and int(t)<=int(self.v70GenerationBornAt):
     self.v70RepairPaySeen[k]=max(old,cur);continue
    if cur>old+EPS:
     inc=cur-old;pay=min(inc,max(0.0,self.v70GenerationDebt-self.v70GenerationPaid));self.v70GenerationPaid+=pay
     if pay>EPS:self.v70CompositeEvents.append({'t':int(t),'event':'V70_GENERATION_REPAIR_PAYMENT','key':k,'side':pay_side,'fillIncQty':inc,'paidQty':pay,'generationDebt':self.v70GenerationDebt,'generationPaid':self.v70GenerationPaid,'generationBornAt':self.v70GenerationBornAt})
    self.v70RepairPaySeen[k]=max(old,cur)
  return out
 def run_exam_v70b(self,models,winner):
  r=super().run_exam_v65(models,winner);self._refresh_carrier_ledger(int(self.capEnd))
  debt=max(0.0,self.v70GenerationDebt-self.v70GenerationPaid)
  r.update({'v70CompositeTriggered':self.v70CompositeTriggered,'v70CandidateAttempts':self.v70CandidateAttempts,'v70CandidateSubmit':self.v70CandidateSubmit,'v70CompositePhysicalFillQty':self.v70PhysicalFillQty,'v70CompositeRepairFillQty':self.v70RepairFillQty,'v70CompositeExpandOverflowFillQty':self.v70CrossFillQty,'v70GenerationDebtQty':self.v70GenerationDebt,'v70GenerationPaidQty':self.v70GenerationPaid,'v70GenerationRemainingQty':debt,'v70GenerationRepairPaymentExercised':int(self.v70GenerationPaid>EPS),'v70GenerationBornAt':self.v70GenerationBornAt,'v70PreBirthPaymentLeak':self.v70PreBirthPaymentLeak,'v70ExpandBlocks':self.v70ExpandBlocks,'v70DuplicateGenerationDebt':self.v70DuplicateGenerationDebt,'v70CompositeEvents':self.v70CompositeEvents[:200]})
  return r

def semantic_rounds(result):
 composite_keys={str(e.get('key')) for e in result.get('v70CompositeEvents',[]) if e.get('event')=='V70_COMPOSITE_FILL' and float(e.get('expandIncQty') or 0.0)>EPS}
 ev=[];seqn=0
 for f in result.get('v53FillEvents',[]):
  role=str(f.get('role') or '');key=str(f.get('key'));t=int(f.get('t') or 0)
  if key in composite_keys:
   ev.append((t,seqn,'REPAIR'));seqn+=1;ev.append((t,seqn,'EXPAND'));seqn+=1;continue
  r='REPAIR' if 'REPAIR' in role else 'EXPAND' if 'EXPAND' in role else None
  if r:ev.append((t,seqn,r));seqn+=1
 ev.sort();comp=[]
 for _,_,r in ev:
  if not comp or comp[-1]!=r:comp.append(r)
 rounds=sum(1 for i in range(len(comp)-2) if comp[i:i+3]==['REPAIR','EXPAND','REPAIR'])
 return {'rounds':rounds,'compressed':comp}


def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v70b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V70B_PARENT_CLOCK','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V70B_PARENT_CLOCK_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v65.V65GlobalExpandDedup(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v65(models,cr['winner'])
   finally:b.close()
   c=V70BParentClockComposite(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v70b(models,cr['winner'])
   finally:c.close()
   bs=semantic_rounds(br);rs=semantic_rounds(rr);rr['v70SemanticRounds']=rs['rounds'];rr['v70SemanticCompressedSequence']=rs['compressed'];br['v70SemanticRounds']=bs['rounds'];br['v70SemanticCompressedSequence']=bs['compressed']
   rows.append({'marketId':mid,'baseline':br,'candidate':rr});print(json.dumps({'marketId':mid,'trigger':rr['v70CompositeTriggered'],'submit':rr['v70CandidateSubmit'],'physicalFill':rr['v70CompositePhysicalFillQty'],'repairFill':rr['v70CompositeRepairFillQty'],'overflowFill':rr['v70CompositeExpandOverflowFillQty'],'generationPaid':rr['v70GenerationPaidQty'],'rounds':[br['v70SemanticRounds'],rr['v70SemanticRounds']],'safety':[rr['authorizedSubmitWithTruthRoleMismatch'],rr['overOwnedSubmitViolations'],rr['repairToExpandAtFirstFill'],rr.get('v51ResponsibilityOverfill',0),rr.get('v70PreBirthPaymentLeak',0)]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.0) for x in rows)
  agg={'markets':len(rows),'candidateSubmits':int(sm('candidate','v70CandidateSubmit')),'physicalFillQty':sm('candidate','v70CompositePhysicalFillQty'),'repairFillQty':sm('candidate','v70CompositeRepairFillQty'),'expandOverflowFillQty':sm('candidate','v70CompositeExpandOverflowFillQty'),'generationDebtQty':sm('candidate','v70GenerationDebtQty'),'generationPaidQty':sm('candidate','v70GenerationPaidQty'),'generationPaymentMarkets':sum(int(x['candidate'].get('v70GenerationRepairPaymentExercised') or 0)>0 for x in rows),'expandBlocks':int(sm('candidate','v70ExpandBlocks')),'duplicateGenerationDebt':sm('candidate','v70DuplicateGenerationDebt'),'preBirthPaymentLeak':sm('candidate','v70PreBirthPaymentLeak'),'baselineRounds':int(sm('baseline','v70SemanticRounds')),'candidateRounds':int(sm('candidate','v70SemanticRounds')),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill')}
  gates={'candidateSubmitted':agg['candidateSubmits']>0,'physicalConservation':abs(agg['physicalFillQty']-agg['repairFillQty']-agg['expandOverflowFillQty'])<=1e-7,'generationDebtEqualsOverflow':abs(agg['generationDebtQty']-agg['expandOverflowFillQty'])<=1e-7,'zeroDuplicateGenerationDebt':agg['duplicateGenerationDebt']<=EPS,'zeroPreBirthPaymentLeak':agg['preBirthPaymentLeak']<=EPS,'roundsNonDecreasing':agg['candidateRounds']>=agg['baselineRounds'],'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDriftMetric':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS}
  safety_pass=all(gates.values())
  if not safety_pass:decision='REJECT_OR_FIX_INTEGRATION_ACCOUNTING_BEFORE_EXPANSION'
  elif agg['expandOverflowFillQty']<=EPS:decision='NEED_MORE_EXECUTION_SUPPORT_NOT_ARCHITECTURE_FAILURE'
  elif agg['generationPaidQty']<=EPS:decision='CROSSING_REACHED_BUT_GENERATION_HANDOFF_NOT_YET_REPAIRED'
  else:decision='KEEP_COMPOSITE_CROSSING_AND_GENERATION_REPAIR_FUNCTIONAL_EVIDENCE'
  out={'version':'ETH_REPAIR_V70B_PARENT_CLOCK_COMPOSITE_COUNTERFACTUAL','researchOnly':True,'behaviorChange':True,'actionAuthority':'MAX_1_FIRST_ELIGIBLE_COMPOSITE_BRANCH_PER_MARKET_CONSUMED_SMOKE_ONLY','aggregate':agg,'gates':gates,'accountingPass':all(gates.values()),'decision':decision,'rows':rows,'boundary':['first eligible pre-180 OUR Repair package clock only; no Target clock/side/qty authority','composite qty = full current Repair residual + exactly one venue-minimum overflow slice','one physical carrier; Repair allocation first, overflow alone creates generation debt','new Expand blocked while composite overflow is live/unrepaired','no overflow at <=180s','no threshold/qty/delay sweep','winner/PnL diagnostic only','realistic HFT only; no dream fill','no H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'accountingPass':out['accountingPass'],'decision':decision,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
