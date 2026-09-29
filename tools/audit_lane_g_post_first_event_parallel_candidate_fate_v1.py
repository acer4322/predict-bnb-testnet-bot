from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_shared_parent_parallel_residual_exact_fork_v1 import SharedParentParallelResidualFork,FORKS,near,EPS,v2

EXPECTED={
 1946468:{'firstT':1788534919675,'candidate':'DOWN_13','remainingDebt':0.41593714964910133},
 1946792:{'firstT':1788538510897,'candidate':'UP_10','remainingDebt':1.8703985245042845},
}

def _copy_obligations(sim):
 out=[]
 for x in getattr(sim,'obligations',[]) or []:
  out.append({k:x.get(k) for k in ['id','bornT','closedT','closeReason','side','generation','originOverflowQty','outstanding','repaidQty','sourceKeys']})
 return out

class PostFirstEventCandidateFateAudit(SharedParentParallelResidualFork):
 def __init__(self,tape,mid):
  super().__init__(tape,mid,'SHARED_PARENT_PARALLEL_RESIDUAL_PASSIVE')
  self.postFirstArmed=False;self.firstSnapshot=None;self.postEndpoint=None;self.postAlloc=[];self.nativeR239Event0=0;self.nativeOb0=[];self.scopeAtFirst=None;self.activeCumAtFirst={}

 def _candidate_state(self):
  if not self.candidateKey:return {'key':None,'status':'MISSING','cum':0.0,'remaining':None,'cancelRequested':None,'live':False}
  s=self._st(self.candidateKey);o=self.orders.get(self.candidateKey) or {};q=float(o.get('qty') or self.spec['qty'])
  return {'key':self.candidateKey,'status':s['status'],'cum':s['cum'],'remaining':max(0.0,q-s['cum']),'cancelRequested':bool(o.get('cancelRequested',False)),'live':s['live']}

 def _shared_states(self):
  return [dict(key=k,**self._st(k)) for k in self.sharedKeys]

 def _native_owner_snapshot(self):
  return {'activeObligationId':getattr(self,'activeObligationId',None),'obligations':_copy_obligations(self),'r239EventsTail':list((getattr(self,'r239events',[]) or [])[-12:])}

 def _arm_post_first(self,t):
  exp=EXPECTED[self.mid];par=self.ledger.describe_parent(self.parentId) or {}
  self.postFirstArmed=True;self.scopeAtFirst=(int(self.scopeGeneration),self.scopeSide)
  self.nativeR239Event0=len(getattr(self,'r239events',[]) or []);self.nativeOb0=_copy_obligations(self)
  self.activeCumAtFirst={k:self._st(k)['cum'] for k in list(self.activeKeys)}
  self.firstSnapshot={'t':int(t),'candidate':self._candidate_state(),'sharedSiblings':self._shared_states(),'r269Parent':par,'r257':self._ob(),'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'nativeSuccessorOwners':self._native_owner_snapshot(),'slots':len(self.slot_key),'active':len(self.activeKeys)}
  if int(t)!=int(exp['firstT']):self.errors.append('FIRST_STRUCTURAL_T_MISMATCH')
  if self.candidateKey!=exp['candidate']:self.errors.append('CANDIDATE_KEY_MISMATCH')
  if not near(float(par.get('remainingDebt') or 0.0),exp['remainingDebt'],2e-6):self.errors.append('R269_REMAINING_DEBT_MISMATCH_AT_FIRST')
  ob=self._ob() or {}
  if float(ob.get('outstanding') or 0.0)>EPS:self.errors.append('R257_NOT_REPAID_AT_FIRST')
  if float(self._candidate_state().get('cum') or 0.0)>EPS:self.errors.append('CANDIDATE_ALREADY_FILLED_AT_FIRST')

 def _classify(self,reasons,allocs):
  cand_rep=sum(float(a.get('repair_increment') or 0.0) for a in allocs if a.get('carrier_key')==self.candidateKey)
  cand_ov=sum(float(a.get('overflow_increment') or 0.0) for a in allocs if a.get('carrier_key')==self.candidateKey)
  native_new=(getattr(self,'r239events',[]) or [])[self.nativeR239Event0:]
  native_birth=[e for e in native_new if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'}]
  if cand_ov>EPS:
   owned=any(str(e.get('sourceKey'))==str(self.candidateKey) for e in native_birth)
   return 'CANDIDATE_TRANSITIONS_TO_OWNED_SUCCESSOR_OVERFLOW' if owned else 'OWNERSHIP_SEMANTIC_UNRESOLVED'
  if cand_rep>EPS:return 'CANDIDATE_PAYS_REMAINING_SHARED_REPAIR_DEBT'
  if any(r['type']=='CANDIDATE_TERMINAL' for r in reasons):return 'CANDIDATE_TERMINATES_BEFORE_PAYMENT'
  if any(r['type']=='SCOPE_GENERATION_TRANSITION' for r in reasons):return 'SCOPE_TRANSITION_REBINDS_OR_RETIRES_CANDIDATE'
  return 'OWNERSHIP_SEMANTIC_UNRESOLVED'

 def process(self,t):
  pre_cand=self._candidate_state() if self.postFirstArmed else None
  pre_scope=(int(self.scopeGeneration),self.scopeSide) if self.postFirstArmed else None
  pre_native_n=len(getattr(self,'r239events',[]) or []) if self.postFirstArmed else 0
  super().process(t)
  # Parent exact fork resolves first event and resumes inherited Manager behavior thereafter.
  if self.firstEvent is not None and not self.postFirstArmed:
   self._arm_post_first(self.firstEvent['t'])
   return
  if not self.postFirstArmed or self.postEndpoint is not None:return

  # R269 continuation is telemetry only after first event; it does not alter native order lifecycle/Manager actions.
  allocs=self._allocate_overlay();self.postAlloc.extend(allocs)
  reasons=[];cand=self._candidate_state()
  for a in allocs:
   if a.get('carrier_key')==self.candidateKey and float(a.get('fill_increment') or 0.0)>EPS:
    reasons.append({'type':'CANDIDATE_CONFIRMED_FILL','fillQty':float(a['fill_increment']),'repairQty':float(a['repair_increment']),'overflowQty':float(a['overflow_increment'])})
  if pre_cand and pre_cand.get('status') not in v2.TERMINAL_STATUSES and cand['status'] in v2.TERMINAL_STATUSES:
   reasons.append({'type':'CANDIDATE_TERMINAL','status':cand['status']})
  par=self.ledger.describe_parent(self.parentId) or {}
  if float(par.get('remainingDebt') or 0.0)<=EPS:reasons.append({'type':'R269_PARENT_DEBT_ZERO'})
  if float(par.get('overflowBorn') or 0.0)>EPS:reasons.append({'type':'R269_SHARED_OVERFLOW_BORN','qty':float(par.get('overflowBorn') or 0.0)})
  scope=(int(self.scopeGeneration),self.scopeSide)
  if scope!=pre_scope:reasons.append({'type':'SCOPE_GENERATION_TRANSITION','before':pre_scope,'after':scope})
  native_events=(getattr(self,'r239events',[]) or [])[pre_native_n:]
  native_birth=[e for e in native_events if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'}]
  if native_birth:reasons.append({'type':'EXPLICIT_NATIVE_SUCCESSOR_OWNER_BIRTH','events':native_birth})
  for k,c0 in self.activeCumAtFirst.items():
   now=self._st(k)['cum']
   if now>c0+EPS:reasons.append({'type':'INHERITED_ACTIVE_FILL','key':k,'fillQty':now-c0})
  if reasons:
   classification=self._classify(reasons,allocs)
   cand_alloc=[a for a in self.sharedAlloc if a.get('carrier_key')==self.candidateKey]
   cand_rep=sum(float(a.get('repair_increment') or 0.0) for a in cand_alloc)
   cand_ov=sum(float(a.get('overflow_increment') or 0.0) for a in cand_alloc)
   native_new=(getattr(self,'r239events',[]) or [])[self.nativeR239Event0:]
   native_birth_all=[e for e in native_new if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'}]
   self.postEndpoint={'t':int(t),'reasons':reasons,'classification':classification,'candidate':cand,'r269Parent':par,'candidateRepairAllocated':cand_rep,'candidateOverflowAllocated':cand_ov,'candidateOverflowNotional':sum(float(a.get('overflowNotional') or 0.0) for a in cand_alloc),'nativeSuccessorOwnerEvents':native_birth_all,'nativeSuccessorOwners':self._native_owner_snapshot(),'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'r257':self._ob(),'slots':len(self.slot_key),'active':len(self.activeKeys)}

 def run_fate(self,winner):
  base_result=self.run_audit(winner)
  par=self.ledger.describe_parent(self.parentId) or {};cand_alloc=[a for a in self.sharedAlloc if a.get('carrier_key')==self.candidateKey]
  total_fill=sum(float(a.get('fill_increment') or 0.0) for a in self.sharedAlloc);repair=float(par.get('repairPaid') or 0.0);overflow=float(par.get('overflowBorn') or 0.0)
  conservation=near(repair+overflow,total_fill,2e-7) and repair<=float(self.spec['scopeDebt'])+EPS and overflow<=float(self.spec['parentOverflowQtyCap'])+EPS
  native_birth=[]
  if self.postFirstArmed:
   native_new=(getattr(self,'r239events',[]) or [])[self.nativeR239Event0:]
   native_birth=[e for e in native_new if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'}]
  pending_zero=True
  if self.firstSnapshot and float((self.firstSnapshot.get('candidate') or {}).get('cum') or 0.0)<=EPS:
   pending_zero = near(float((self.firstSnapshot.get('r269Parent') or {}).get('repairPaid') or 0.0),float((self.firstEvent or {}).get('sharedParent',{}).get('repairPaid') or 0.0),2e-7)
  correctness=bool(self.triggered and self.submitDeltaAtFork==1 and self.firstSnapshot and self.postEndpoint and not self.errors and conservation and pending_zero and bool(base_result.get('r264CorrectnessPass')) and float(base_result.get('unauthorizedOverflowQty',0.0))<=EPS and float(base_result.get('repairQuotaExcessMax',0.0))<=EPS)
  endpoint_ov=float((self.postEndpoint or {}).get('candidateOverflowAllocated') or 0.0)
  endpoint_owned=any(str(e.get('sourceKey'))==str(self.candidateKey) for e in ((self.postEndpoint or {}).get('nativeSuccessorOwnerEvents') or []))
  if endpoint_ov>EPS and not endpoint_owned: correctness=False
  return {'marketId':self.mid,'trigger':self.trigger,'candidateKey':self.candidateKey,'submitDeltaAtFork':self.submitDeltaAtFork,'firstStructuralEvent':self.firstEvent,'firstSnapshot':self.firstSnapshot,'postEndpoint':self.postEndpoint,'sharedParentFinal':par,'sharedAllocations':self.sharedAlloc,'nativeR239Events':list(getattr(self,'r239events',[]) or []),'nativeObligations':_copy_obligations(self),'errors':self.errors,'conservationPass':conservation,'pendingZeroPass':pending_zero,'underlyingCorrect':bool(base_result.get('r264CorrectnessPass')),'nativeUnauthorizedOverflowQty':float(base_result.get('unauthorizedOverflowQty',0.0)),'nativeRepairQuotaExcessMax':float(base_result.get('repairQuotaExcessMax',0.0)),'correct':correctness}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946468,1946792');ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 tmp=Path(tempfile.mkdtemp(prefix='lane_g_post_first_fate_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  rows=[]
  for m in mids:
   sim=PostFirstEventCandidateFateAudit(tmp/f'{m}.json.xz',m)
   try:r=sim.run_fate(co[m]['winner'])
   finally:sim.close()
   rows.append(r);print(json.dumps({'marketId':m,'candidate':r['candidateKey'],'firstT':(r.get('firstSnapshot') or {}).get('t'),'endpoint':r.get('postEndpoint'),'correct':r['correct'],'errors':r['errors']},ensure_ascii=False),flush=True)
  classes={str(r['marketId']):(r.get('postEndpoint') or {}).get('classification') for r in rows}
  out={'version':'LANE_G_POST_FIRST_EVENT_PARALLEL_CANDIDATE_FATE_AUDIT_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'classifications':classes,'gates':{'allTriggered':all(r.get('trigger') is not None for r in rows),'candidateGeometryFrozen':all(r.get('candidateKey')==EXPECTED[r['marketId']]['candidate'] and r.get('submitDeltaAtFork')==1 for r in rows),'firstEventParity':all((r.get('firstSnapshot') or {}).get('t')==EXPECTED[r['marketId']]['firstT'] for r in rows),'structuralEndpointObserved':all(r.get('postEndpoint') is not None for r in rows),'correctnessPass':all(r['correct'] for r in rows)},'boundary':['telemetry-only continuation of preregistered candidate branch','no new physical submits added by audit','inherited Manager resumes after first structural event exactly as parent fork','R269 post-first allocation is diagnostic only','native R239 owner state audited separately; no synthetic ownership promoted','no fixed-time horizon','max4 <=180s inherited','pending zero payment/protection/credit','no authority increase','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'classifications':classes},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
