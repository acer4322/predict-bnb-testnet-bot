from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v70f=sib('eth_v70f_for_v70g','run_eth_repair_v70f_early_clock_parallel_relay_smoke.py')
v65=v70f.v65;v38=v70f.v38;EPS=1e-9

class V70GGenerationScopedRelay(v70f.V70FEarlyClockParallelRelay):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v70gGenerationId=1
  self.v70gGenerationAuthorized=False
  self.v70gGenerationCarrier=None
  self.v70gGenerationCarrierBaselineFill=0.0
  self.v70gGenerationResponsibilityCount={1:0}
  self.v70gGenerationEvents=[]
  self.v70gDuplicateObjectiveBlocks=0
  self.v70gExistingCarrierBinds=0
  self.v70gInheritedExpandBinds=0
  self.v70gAuthorityBirthDebt=0.0
  self.v70gLastDebtPaid=0.0
  self.v70gGenerationDebtBaseline=float(self.v70dGenerationDebt or 0.0)
  self.v70gGenerationPaidBaseline=float(self.v70dGenerationPaid or 0.0)

 def _generation_unlocked(self):
  if not self.v70gGenerationAuthorized:return True
  debt_inc=max(0.0,float(self.v70dGenerationDebt or 0.0)-float(self.v70gGenerationDebtBaseline or 0.0))
  paid_inc=max(0.0,float(self.v70dGenerationPaid or 0.0)-float(self.v70gGenerationPaidBaseline or 0.0))
  # A newly authorized generation cannot unlock until it has created its own physical Expand debt.
  if debt_inc<=EPS:return False
  return (debt_inc-paid_inc)<=EPS

 def _maybe_advance_generation(self,t):
  if self.v70gGenerationAuthorized and self._generation_unlocked():
   old=self.v70gGenerationId
   self.v70gGenerationId+=1
   self.v70gGenerationAuthorized=False
   self.v70gGenerationCarrier=None
   self.v70gGenerationCarrierBaselineFill=0.0
   self.v70gGenerationDebtBaseline=float(self.v70dGenerationDebt or 0.0)
   self.v70gGenerationPaidBaseline=float(self.v70dGenerationPaid or 0.0)
   self.v70gGenerationResponsibilityCount.setdefault(self.v70gGenerationId,0)
   self.v70gGenerationEvents.append({'t':int(t),'event':'GENERATION_UNLOCK','fromGeneration':old,'toGeneration':self.v70gGenerationId,'debt':self.v70dGenerationDebt,'paid':self.v70dGenerationPaid})

 def _bind_existing_expand(self,t,key,reason):
  e=self.carrierLedger.get(key,{})
  self.v70gGenerationAuthorized=True
  self.v70gGenerationDebtBaseline=float(self.v70dGenerationDebt or 0.0)
  self.v70gGenerationPaidBaseline=float(self.v70dGenerationPaid or 0.0)
  self.v70gGenerationCarrier=key
  self.v70gGenerationCarrierBaselineFill=float(e.get('actualFilled') or 0.0)
  self.v70gGenerationResponsibilityCount[self.v70gGenerationId]=self.v70gGenerationResponsibilityCount.get(self.v70gGenerationId,0)+1
  self.v70dKeys.add(key);self.v70dFillSeen[key]=self.v70gGenerationCarrierBaselineFill
  self.v70gGenerationEvents.append({'t':int(t),'event':'GENERATION_BIND','generation':self.v70gGenerationId,'carrier':key,'reason':reason,'baselineFill':self.v70gGenerationCarrierBaselineFill})

 def _reserve_expand_at_repair_submit(self,t,repair_key,repair_entry,pE):
  self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
  if self.v70gGenerationAuthorized:
   self.v70gDuplicateObjectiveBlocks+=1
   self.v70gGenerationEvents.append({'t':int(t),'event':'DUPLICATE_AUTH_BLOCK','generation':self.v70gGenerationId,'repairKey':repair_key,'carrier':self.v70gGenerationCarrier})
   return
  # If V44/V64 already owns Expand, bind this generation to that carrier rather than create another objective.
  existing=[]
  for k,e in self.carrierLedger.items():
   if str(e.get('objectiveRole') or '')!='EXPAND' or bool(e.get('terminalConfirmed')):continue
   rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
   if rem>EPS:existing.append(k)
  if existing:
   self.v70gExistingCarrierBinds+=1;self._bind_existing_expand(t,existing[0],'EXISTING_V44_V64_OCCUPANCY');return
  before=set(self.v70dKeys)
  super()._reserve_expand_at_repair_submit(t,repair_key,repair_entry,pE)
  new=[k for k in self.v70dKeys if k not in before]
  if new:
   self.v70gGenerationAuthorized=True;self.v70gGenerationCarrier=new[0]
   self.v70gGenerationDebtBaseline=float(self.v70dGenerationDebt or 0.0);self.v70gGenerationPaidBaseline=float(self.v70dGenerationPaid or 0.0)
   self.v70gGenerationResponsibilityCount[self.v70gGenerationId]=self.v70gGenerationResponsibilityCount.get(self.v70gGenerationId,0)+1
   self.v70gGenerationEvents.append({'t':int(t),'event':'EARLY_AUTH_BIND','generation':self.v70gGenerationId,'carrier':new[0],'repairKey':repair_key})

 def _score_state(self,t,after_kind):
  # Preserve inherited V44/V65 scoring/action path unless generation ownership requires dedup.
  self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
  if self.v70gGenerationAuthorized:
   self.v70gDuplicateObjectiveBlocks+=1
   self.v70gGenerationEvents.append({'t':int(t),'event':'INHERITED_EXPAND_DEDUP_BLOCK','generation':self.v70gGenerationId,'afterKind':after_kind,'carrier':self.v70gGenerationCarrier})
   return None
  before=set(k for k,e in self.carrierLedger.items() if str(e.get('objectiveRole') or '')=='EXPAND')
  out=v65.V65GlobalExpandDedup._score_state(self,t,after_kind)
  self._refresh_carrier_ledger_no_v70d()
  after=set(k for k,e in self.carrierLedger.items() if str(e.get('objectiveRole') or '')=='EXPAND')
  new=list(after-before)
  if new:
   self.v70gInheritedExpandBinds+=1;self._bind_existing_expand(t,new[0],'INHERITED_V44_V65_ACTION')
  return out

 def _refresh_carrier_ledger(self,t):
  out=super()._refresh_carrier_ledger(t)
  if hasattr(self,'v70gGenerationId'):
   self._maybe_advance_generation(t)
  return out

 def run_exam_v70g(self,models,winner):
  r=super().run_exam_v70f(models,winner)
  max_resp=max(self.v70gGenerationResponsibilityCount.values()) if self.v70gGenerationResponsibilityCount else 0
  r.update({'v70gGenerationCount':len(self.v70gGenerationResponsibilityCount),'v70gGenerationResponsibilityCount':self.v70gGenerationResponsibilityCount,'v70gMaxResponsibilitiesPerGeneration':max_resp,'v70gDuplicateObjectiveBlocks':self.v70gDuplicateObjectiveBlocks,'v70gExistingCarrierBinds':self.v70gExistingCarrierBinds,'v70gInheritedExpandBinds':self.v70gInheritedExpandBinds,'v70gGenerationEvents':self.v70gGenerationEvents[:300]})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v70g_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V70G_GENERATION_SCOPED_RELAY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V70G_GENERATION_SCOPED_RELAY_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v65.V65GlobalExpandDedup(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v65(models,cr['winner'])
   finally:b.close()
   c=V70GGenerationScopedRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v70g(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':rr});print(json.dumps({'marketId':mid,'responsibilities':rr['v70gGenerationResponsibilityCount'],'maxPerGeneration':rr['v70gMaxResponsibilitiesPerGeneration'],'reservations':rr['v70dParallelReservations'],'fill':rr['v70dPhysicalExpandFillQty'],'debt':rr['v70dGenerationDebtQty'],'paid':rr['v70dGenerationPaidQty'],'rounds':[br['v64Rounds'],rr['v70dSemanticRounds']],'dedupBlocks':rr['v70gDuplicateObjectiveBlocks']},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.0) for x in rows)
  max_resp=max(int(x['candidate'].get('v70gMaxResponsibilitiesPerGeneration') or 0) for x in rows)
  agg={'markets':len(rows),'parallelReservations':int(sm('candidate','v70dParallelReservations')),'expandFillQty':sm('candidate','v70dPhysicalExpandFillQty'),'generationDebtQty':sm('candidate','v70dGenerationDebtQty'),'generationPaidQty':sm('candidate','v70dGenerationPaidQty'),'baselineRounds':int(sm('baseline','v64Rounds')),'candidateRounds':int(sm('candidate','v70dSemanticRounds')),'maxResponsibilitiesPerGeneration':max_resp,'duplicateObjectiveBlocks':int(sm('candidate','v70gDuplicateObjectiveBlocks')),'existingCarrierBinds':int(sm('candidate','v70gExistingCarrierBinds')),'inheritedExpandBinds':int(sm('candidate','v70gInheritedExpandBinds')),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'preBirthPaymentLeak':sm('candidate','v70dPreBirthPaymentLeak'),'duplicateGenerationDebt':sm('candidate','v70dDuplicateGenerationDebt'),'repairPreservationViolations':int(sm('candidate','v70fRepairPreservationViolations'))}
  gates={'oneMarketCompletes':len(rows)==1,'atMostOneResponsibilityPerGeneration':agg['maxResponsibilitiesPerGeneration']<=1,'zeroDuplicateExpandObjective':agg['maxResponsibilitiesPerGeneration']<=1,'physicalFillConservation':abs(agg['expandFillQty']-agg['generationDebtQty'])<=1e-7,'generationDebtExact':abs(agg['expandFillQty']-agg['generationDebtQty'])<=1e-7,'zeroPreBirthPaymentLeak':agg['preBirthPaymentLeak']<=EPS,'zeroDoubleSpend':agg['duplicateGenerationDebt']<=EPS,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroOverOwned':agg['overOwned']==0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroUnauthorizedRoleDrift':agg['repairDrift']==0,'repairCarrierPreserved':agg['repairPreservationViolations']==0,'semanticRoundsNonDecreasing':agg['candidateRounds']>=agg['baselineRounds']}
  safety=all(gates.values())
  behavior_same=(agg['candidateRounds']==agg['baselineRounds'] and agg['expandFillQty']<=EPS and agg['parallelReservations']==0)
  if not safety:decision='REJECT_V70G_SAFETY_ACCOUNTING_OR_ROUND_GATE'
  elif behavior_same:decision='KEEP_NOOP_OWNERSHIP_CORRECTION'
  elif agg['expandFillQty']>EPS:decision='KEEP_V70G_GENERATION_SCOPED_RELAY_FUNCTIONAL_SMOKE'
  else:decision='KEEP_V70G_OWNERSHIP_SCHEDULER_NO_PHYSICAL_EXPAND_FILL_THIS_SMOKE'
  out={'version':'ETH_REPAIR_V70G_GENERATION_SCOPED_RELAY_HFT_SMOKE','date':'2026-09-03','researchOnly':True,'behaviorChange':'ownership scheduler only','aggregate':agg,'gates':gates,'decision':decision,'rows':rows,'boundary':['matched V65 baseline','complete inherited V44/V65/V64 path preserved except ownership-dedup blocks','at most one Expand responsibility per Repair generation','existing Expand carrier absorbs generation responsibility','confirmed Expand fill alone births exact debt','strictly post-birth opposite Repair increments alone pay debt','no next-generation authority before debt discharge','<=180s blocks new Expand not Repair','realistic HFT/Predict Tape only; no dream fill','no threshold/price/delay/qty/winner/PnL tuning','no Stage-A/H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
