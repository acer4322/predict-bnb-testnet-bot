from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_satellite_overflow_r239_ownership_handoff_exact_v1 import SatelliteOverflowR239OwnershipHandoff
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
from tools.run_lane_g_shared_parent_parallel_residual_exact_fork_v1 import EPS,near
MID=1946468; T=1788534920674; SOURCE='DOWN_13'; GEN=2
SPEC={'price':0.48,'qty':2.0833333333333335,'debt':1.5071397734278216,'repair':1.5071397734278216,'overflow':0.5761935599055119,'overflowNotional':0.2765729087546457,'authoritySlack':0.5840628503508984,'currentFloor':-0.9115561118064246,'fullFloor':-0.4044163383786028}
BRANCHES=('INHERITED_R239_PURE_REPAIR','R269_MIXED_VENUE_MIN_PASSIVE')

def ob_copy(sim,oid):
 x=next((z for z in getattr(sim,'obligations',[]) if int(z.get('id') or -1)==int(oid)),None)
 return None if x is None else dict(x)

class OwnedSuccessorMixedVenueMinFork(SatelliteOverflowR239OwnershipHandoff):
 def __init__(self,tape,mid,branch):
  super().__init__(tape,mid); self.mixedBranch=branch; self.mixedTrigger=None; self.branchAttempted=False; self.mixedKey=None; self.submitDelta=0
  self.ledger=GenerationAwareSharedParentDebtAllocationLedgerV3(); self.ledgerPid=990002; self.ledgerAlloc=[]; self.localEvent=None; self.localSuccessorEvents=[]; self.errors=[]
 def _payoff(self):
  u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);return {'best':max(u,d)-c,'floor':min(u,d)-c,'gap':abs(u-d),'upQty':u,'downQty':d,'cost':c}
 def _trigger_state(self,t,ob):
  return {'t':int(t),'obligationId':int(ob['id']),'sourceKeys':list(ob.get('sourceKeys') or []),'ownerSide':ob['side'],'generation':int(ob['generation']),'outstanding':float(ob['outstanding']),
   'scopeSide':self.scopeSide,'scopeGeneration':int(self.scopeGeneration),'repairSide':'UP','slots':len(self.slot_key),'active':len(self.activeKeys),'payoff':self._payoff(),
   'scopeRiskCreditTotal':float(self.scopeRiskCreditTotal),'scopeRiskCreditConsumed':float(self.scopeRiskCreditConsumed),'riskAuthorityCurrentGeneration':float(self._risk_authority_current_generation()),
   'reservedCurrentExpandRisk':float(self._reserved_current_expand_risk()),'serviceClaim':float(self._service_claim()),'usedPrices':sorted(float(x) for x in self._used_prices('UP'))}
 def _candidate_submit(self,t,ob):
  p=SPEC['price'];q=SPEC['qty'];debt=SPEC['debt'];overflow=SPEC['overflow'];risk=SPEC['overflowNotional']
  allowance=float(self.scopeRiskCreditTotal)+float(self._risk_authority_current_generation()); committed=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+float(self._service_claim()); slack=max(0.0,allowance-committed)
  if risk>slack+EPS: self.errors.append('AUTHORITY_INSUFFICIENT'); return False
  if len(self.slot_key)+len(self.activeKeys)>=4: self.errors.append('NO_CAPACITY'); return False
  if p in self._used_prices('UP'): self.errors.append('PRICE_ALREADY_USED'); return False
  levels=[float(self.__class__.__mro__[1].__mro__[0].__dict__.get('__doc__') is None)] if False else [float(x) for x in self._live_price_levels('UP')]
  # exact prereg price must still be on the inherited strict-past live level set
  if not any(near(float(x),p,1e-9) for x in levels): self.errors.append('PRICE_NOT_LIVE'); return False
  full=float(self._candidate_alone_floor('UP',p,q));
  if not near(full,SPEC['fullFloor'],2e-6): self.errors.append('FULL_FLOOR_MISMATCH'); return False
  sp={'repairQty':debt,'overflowQty':overflow,'overflowRisk':risk,'repairOnlyFloor':float(self._candidate_alone_floor('UP',p,debt)),'fullFloor':full,'debt':debt,'reservedRepairBefore':0.0,'availableDebtBefore':debt,'laneGOwnedSuccessorMixed':True}
  before_n=int(self.n); before_sub=int(self.submits)
  ok=self._submit_role_v8(int(t),'UP','SATELLITE_REPAIR',p,q,full,sp)
  if not ok: self.errors.append('PHYSICAL_SUBMIT_BLOCKED'); return False
  key=f'UP_{before_n}'; self.mixedKey=key; self.submitDelta=int(self.submits)-before_sub; self.handoffKeys[key]=int(ob['id'])
  self.ledger.register_carrier(key,self.ledgerPid,debt)
  self.slot_history.append({'t':int(t),'event':'LANE_G_OWNED_SUCCESSOR_MIXED_VENUE_MIN_SUBMIT','key':key,'obligationId':int(ob['id']),'price':p,'qty':q,'repairDebt':debt,'overflowQtyCap':overflow,'overflowNotionalCap':risk,'authoritySlackAtSubmit':slack})
  return True
 def _try_overflow_handoff(self,t):
  ob=self._active_obligation()
  exact=bool(ob and int(t)==T and SOURCE in (ob.get('sourceKeys') or []) and int(ob.get('generation') or -1)==GEN and self.scopeSide=='DOWN' and int(self.scopeGeneration)==GEN)
  if exact and not self.branchAttempted:
   self.branchAttempted=True; self.mixedTrigger=self._trigger_state(t,ob)
   if not near(float(ob['outstanding']),SPEC['debt'],2e-7): self.errors.append('DEBT_MISMATCH')
   if self.mixedBranch=='R269_MIXED_VENUE_MIN_PASSIVE': return self._candidate_submit(t,ob)
   return super()._try_overflow_handoff(t)
  return super()._try_overflow_handoff(t)
 def process(self,t):
  before_split=len(getattr(self,'splitEvents',[]) or []); before_r239=len(getattr(self,'r239events',[]) or []); old_ob=ob_copy(self,1)
  super().process(t)
  if not self.mixedKey: return
  new_split=list((getattr(self,'splitEvents',[]) or [])[before_split:])
  cand=[e for e in new_split if e.get('event')=='ROLE_FILL_SPLIT' and str(e.get('key'))==self.mixedKey and float(e.get('fillInc') or 0)>EPS]
  allocs=[]
  if cand:
   o=self.orders.get(self.mixedKey); cum=float(o.get('cum') or 0.0) if o else 0.0
   a=self.ledger.allocate_cumulative(self.mixedKey,self.ledgerPid,cum,SPEC['debt'])
   if a is not None:
    ad=a.__dict__.copy(); allocs.append(ad); self.ledgerAlloc.append(ad)
    ev=cand[-1]
    if not near(float(ev.get('repairAllocated') or 0),float(a.repair_increment),2e-7): self.errors.append('R269_NATIVE_REPAIR_MISMATCH')
    if not near(float(ev.get('overflowRealized') or 0),float(a.overflow_increment),2e-7): self.errors.append('R269_NATIVE_OVERFLOW_MISMATCH')
    if float(a.overflow_increment)>EPS:
     before=len(getattr(self,'r239events',[]) or [])
     self._register_overflow(int(t),{'event':'ROLE_FILL_SPLIT','key':self.mixedKey,'role':'SATELLITE_REPAIR','side':'UP','price':SPEC['price'],'generationAtSubmit':GEN,'overflowRealized':float(a.overflow_increment)})
     born=list((getattr(self,'r239events',[]) or [])[before:]); self.localSuccessorEvents.extend(born)
  if self.localEvent is None:
   status=''
   if self.mixedKey in self.orders:
    try: status=str(self.snap(self.orders[self.mixedKey]).get('status') or '').upper()
    except Exception: status=''
   terminal=status in {'FILLED','CANCELED','EXPIRED','REJECTED'}
   if cand or terminal:
    self.localEvent={'t':int(t),'candidateSplitEvents':cand,'r269Allocations':allocs,'candidateStatus':status,'oldOwnerBefore':old_ob,'oldOwnerAfter':ob_copy(self,1),'payoff':self._payoff(),'scopeSide':self.scopeSide,'scopeGeneration':int(self.scopeGeneration),'newR239Events':list((getattr(self,'r239events',[]) or [])[before_r239:])+list(self.localSuccessorEvents)}
 def run_fork(self,winner):
  r=self.run_audit(winner); owner1=ob_copy(self,1)
  successor=[e for e in getattr(self,'r239events',[]) or [] if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'} and str(e.get('sourceKey'))==str(self.mixedKey)] if self.mixedKey else []
  par=self.ledger.describe_parent(self.ledgerPid) if self.mixedKey else None
  repair=float((par or {}).get('repairPaid') or 0.0); overflow=float((par or {}).get('overflowBorn') or 0.0); filled=repair+overflow
  authority_excess=float((r.get('r247ServiceChecks') or {}).get('combinedAuthorityExcessMax') or 0.0)
  local_exercised=filled>EPS
  old_paid=bool(owner1 and float(owner1.get('repaidQty') or 0)>EPS)
  overflow_owned=(overflow<=EPS or sum(float(e.get('overflowQty') or e.get('addQty') or 0.0) for e in successor)+1e-7>=overflow)
  expected_submit=1 if self.mixedBranch=='R269_MIXED_VENUE_MIN_PASSIVE' else 0
  correct=bool(self.branchAttempted and self.mixedTrigger and not self.errors and (self.submitDelta if self.mixedBranch!='INHERITED_R239_PURE_REPAIR' else 0)==expected_submit and bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS and authority_excess<=EPS)
  if self.mixedBranch=='R269_MIXED_VENUE_MIN_PASSIVE':
   correct=correct and repair<=SPEC['debt']+EPS and overflow<=SPEC['overflow']+2e-7 and repair+overflow<=SPEC['qty']+2e-7 and overflow*SPEC['price']<=SPEC['overflowNotional']+2e-7 and overflow_owned
  return {'marketId':MID,'branch':self.mixedBranch,'trigger':self.mixedTrigger,'branchAttempted':self.branchAttempted,'mixedKey':self.mixedKey,'submitDelta':self.submitDelta,'localEvent':self.localEvent,'r269Parent':par,'r269Allocations':self.ledgerAlloc,'successorEvents':successor,'oldOwnerFinal':owner1,'r239Stats':dict(getattr(self,'r239',{}) or {}),'errors':self.errors,
   'terminal':{'submits':int(r.get('submits') or 0),'fills':int(r.get('fillEvents') or 0),'pnl':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'best':float(r.get('best') or 0.0),'scopeGeneration':int(r.get('scopeGeneration') or 0),'scopeSide':r.get('scopeSide'),'obligationBirths':int((getattr(self,'r239',{}) or {}).get('OVERFLOW_OBLIGATION_BORN',0)),'handoffFills':int((getattr(self,'r239',{}) or {}).get('HANDOFF_REPAIR_FILL',0))},
   'mechanism':{'physicalFillQty':filled,'repairPaidByMixed':repair,'overflowBornByMixed':overflow,'oldOwnerPaid':old_paid,'successorOwned':overflow_owned and bool(successor),'executionExercised':local_exercised},
   'nativeUnauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0)),'nativeRepairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'combinedAuthorityExcessMax':authority_excess,'correct':correct}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_owned_mixed_fork_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  rows=[]
  for b in BRANCHES:
   s=OwnedSuccessorMixedVenueMinFork(tmp/f'{MID}.json.xz',MID,b)
   try:r=s.run_fork(co[MID]['winner'])
   finally:s.close()
   rows.append(r); print(json.dumps({'branch':b,'trigger':r['trigger'],'submitDelta':r['submitDelta'],'mechanism':r['mechanism'],'localEvent':r['localEvent'],'terminal':r['terminal'],'correct':r['correct'],'errors':r['errors']},ensure_ascii=False),flush=True)
  base,cand=rows
  parity=base['trigger']==cand['trigger']
  td={k:cand['terminal'][k]-base['terminal'][k] for k in ['submits','fills','pnl','floor','best']}
  substantive=bool(cand['mechanism']['executionExercised'] and cand['mechanism']['oldOwnerPaid'] and cand['correct'] and (cand['mechanism']['overflowBornByMixed']<=EPS or cand['mechanism']['successorOwned']))
  if substantive: value='SUBSTANTIVE_LOCAL_MECHANISM_EVIDENCE'
  elif cand['correct'] and cand['mixedKey'] and not cand['mechanism']['executionExercised']: value='EXECUTION_INCONCLUSIVE_LOW_VALUE_UNTIL_FILLED'
  else: value='LOCAL_OR_FAILED_DO_NOT_EXPAND'
  out={'version':'LANE_G_OWNED_SUCCESSOR_MIXED_VENUE_MIN_EXACT_FORK_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'triggerParity':parity,'terminalDeltaCandidateMinusBaseline':td,
   'gates':{'triggerParity':parity,'baselineNoMixedSubmit':base['submitDelta']==0,'candidateOneMixedSubmit':cand['submitDelta']==1,'candidateCorrectnessPass':cand['correct'],'baselineCorrectnessPass':base['correct'],'mixedPhysicalExecutionExercised':cand['mechanism']['executionExercised'],'oldSuccessorPaid':cand['mechanism']['oldOwnerPaid'],'confirmedOverflowOwnedIfAny':cand['mechanism']['overflowBornByMixed']<=EPS or cand['mechanism']['successorOwned']},
   'valueClassification':value,'boundary':['single consumed market exact fork','one preregistered mixed venue-min passive carrier only','R269 Repair-first allocation','existing R239 owner/service lifecycle','no new authority','realistic HFT','fresh untouched','no dream fill','no fixed time/rank gate','no 8781','terminal metrics secondary']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'valueClassification':value,'terminalDelta':td},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
