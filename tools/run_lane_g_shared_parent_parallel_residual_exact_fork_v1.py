from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import audit_lane_g_partial_payment_residual_carrier_reachability_v1 as base
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
from tools import run_eth_role_separated_multislot_v7_monetary_credit_smoke as v7
v2=base.v2;EPS=1e-9

FORKS={
1946468:{'t':1788534917463,'generation':1,'scopeSide':'UP','repairSide':'DOWN','scopeDebt':2.30272960247929,'existingRepairKeys':['DOWN_12'],'price':0.52,'qty':1.923076923076923,'fixedRepairQty':0.41593714964910133,'fixedOverflowQty':1.5071397734278216,'parentOverflowQtyCap':1.5071397734278218,'worstOverflowNotional':0.7987840799167456,'combinedAuthoritySlack':1.7857142857142856},
1946792:{'t':1788538509914,'generation':1,'scopeSide':'DOWN','repairSide':'UP','scopeDebt':3.483301750310736,'existingRepairKeys':['UP_3','UP_9'],'price':0.61,'qty':1.639344262295082,'fixedRepairQty':0.17548327026699617,'fixedOverflowQty':1.4638609920280858,'parentOverflowQtyCap':1.4638609920280858,'worstOverflowNotional':0.9075938150574132,'combinedAuthoritySlack':1.587301587301587},
1946876:{'t':1788539162674,'generation':2,'scopeSide':'UP','repairSide':'DOWN','scopeDebt':2.292806995696337,'existingRepairKeys':['DOWN_18'],'price':0.62,'qty':1.6129032258064517,'fixedRepairQty':0.8435316333744024,'fixedOverflowQty':0.7693715924320492,'parentOverflowQtyCap':0.7693715924320492,'worstOverflowNotional':0.5308663987781139,'combinedAuthoritySlack':0.4285714285714286,'negative':True},
}
BRANCHES=('INHERITED_SERIAL_REPAIR','SHARED_PARENT_PARALLEL_RESIDUAL_PASSIVE')

def near(a,b,tol=1e-7):return abs(float(a)-float(b))<=tol

class SharedParentParallelResidualFork(base.PartialPaymentResidualReachability):
 def __init__(self,tape,mid,branch):
  super().__init__(tape,1,4);self.mid=int(mid);self.branch=str(branch);self.spec=FORKS[self.mid]
  self.triggered=False;self.active=False;self.resolved=False;self.errors=[];self.trigger=None;self.firstEvent=None
  self.ledger=GenerationAwareSharedParentDebtAllocationLedgerV3();self.parentId=int(self.spec['generation']);self.sharedKeys=[];self.sharedBaseCum={};self.sharedAlloc=[]
  self.candidateKey=None;self.submitDeltaAtFork=0;self.managerBlocks={'risk':0,'reanchor':0,'open':0};self.activeCum0={};self.scope0=None;self.ob0=None
  self.successorOwners=[];self.authorityRejected=False

 def _st(self,key):
  o=self.orders.get(key)
  if not o:return {'status':'MISSING','cum':0.0,'live':False}
  try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
  except Exception:st='';cum=float(o.get('cum') or 0.0)
  return {'status':st,'cum':cum,'live':st not in v2.TERMINAL_STATUSES}
 def _payoff(self):
  u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);b=max(u,d)-c;f=min(u,d)-c
  return {'upQty':u,'downQty':d,'cost':c,'best':b,'floor':f,'gap':b-f}
 def _ob(self):
  ob=self.riskRepairObligations.get(int(self.spec['generation']))
  return None if not ob else {k:ob.get(k) for k in ['bornQty','outstanding','repaidQty','passiveRepaidQty','activeRepaidQty','closeReason']}
 def _candidate_geometry_ok(self):
  s=self.spec;used={v2.kprice(o['price']) for _,_,o,role in self._live_role_rows(side=s['repairSide'])}
  levels=[v2.kprice(x) for x in self._live_price_levels(s['repairSide'])]
  return v2.kprice(s['price']) in levels and v2.kprice(s['price']) not in used and near(1.0/float(s['price']),s['qty'],1e-7)
 def _snapshot(self,t):
  s=self.spec;ob=self._ob();live=[]
  for _,k,o,role in self._live_role_rows(side=s['repairSide']):
   if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} and self.key_scope_gen.get(k)==int(s['generation']):live.append((k,o))
  return {'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,'repairSide':self._repair_side(),'scopeDebt':float(self._scope_debt_qty()),
   'liveRepairKeys':[str(k) for k,_ in live],'slots':len(self.slot_key),'active':len(self.activeKeys),'payoff':self._payoff(),'obligation':ob,
   'candidate':{'price':s['price'],'qty':s['qty'],'fixedRepairQty':s['fixedRepairQty'],'fixedOverflowQty':s['fixedOverflowQty']},
   'combinedAuthoritySlack':float(s['combinedAuthoritySlack']),'worstOverflowNotional':float(s['worstOverflowNotional']),'geometryLive':self._candidate_geometry_ok()}
 def _validate_trigger(self,snap):
  s=self.spec
  if int(snap['t'])!=int(s['t']):self.errors.append('T_MISMATCH')
  if int(snap['generation'])!=int(s['generation']):self.errors.append('GEN_MISMATCH')
  if snap['scopeSide']!=s['scopeSide'] or snap['repairSide']!=s['repairSide']:self.errors.append('SIDE_MISMATCH')
  if not near(snap['scopeDebt'],s['scopeDebt'],2e-6):self.errors.append('DEBT_MISMATCH')
  if sorted(snap['liveRepairKeys'])!=sorted(s['existingRepairKeys']):self.errors.append('LIVE_KEYS_MISMATCH')
  if not snap['geometryLive']:self.errors.append('CANDIDATE_GEOMETRY_NOT_LIVE')
  if len(self.slot_key)>=self.max_slots:self.errors.append('NO_GLOBAL_SPARE_SLOT')
 def _arm_ledger(self):
  debt=float(self.spec['scopeDebt']);self.sharedKeys=list(self.spec['existingRepairKeys'])
  for k in self.sharedKeys:
   self.ledger.register_carrier(k,self.parentId,debt);c=self._st(k)['cum'];self.ledger.carrier_seen[k]=c;self.sharedBaseCum[k]=c
 def _submit_parallel(self,t):
  s=self.spec
  if float(s['worstOverflowNotional'])>float(s['combinedAuthoritySlack'])+EPS:
   self.authorityRejected=True;return False
  before_n=int(self.n);proj=float(self._candidate_alone_floor(s['repairSide'],s['price'],s['qty']))
  ok=v7.RealizedCreditMultiSlotSim._submit_role(self,int(t),s['repairSide'],'SATELLITE_REPAIR',float(s['price']),float(s['qty']),proj)
  if not ok:return False
  key=f"{s['repairSide']}_{before_n}";self.candidateKey=key;self.sharedKeys.append(key);self.ledger.register_carrier(key,self.parentId,float(s['scopeDebt']));self.sharedBaseCum[key]=0.0
  # Native per-key authorization is a compatibility envelope only; R269 overlay below is the fork allocation authority.
  self.keyRepairQuotaAuthorized[key]=float(s['fixedRepairQty']);self.keyRepairQuotaRemaining[key]=float(s['fixedRepairQty'])
  self.keyOverflowQtyAuthorized[key]=float(s['fixedOverflowQty']);self.keyOverflowQtyRemaining[key]=float(s['fixedOverflowQty'])
  self.totalRepairQuotaAuthorized+=float(s['fixedRepairQty']);self.totalOverflowQtyAuthorized+=float(s['fixedOverflowQty'])
  self.slot_history.append({'t':int(t),'event':'LANE_G_SHARED_PARENT_PARALLEL_RESIDUAL_SUBMIT','key':key,'parentId':self.parentId,'price':float(s['price']),'qty':float(s['qty']),'authorityHeld':float(s['worstOverflowNotional'])})
  return True
 def _activate(self,t):
  self.triggered=True;self.trigger=self._snapshot(t);self._validate_trigger(self.trigger);self._arm_ledger();self.scope0=(int(self.scopeGeneration),self.scopeSide);self.ob0=self._ob()
  self.activeCum0={k:self._st(k)['cum'] for k in list(self.activeKeys)}
  before=int(self.submits)
  if self.branch=='SHARED_PARENT_PARALLEL_RESIDUAL_PASSIVE':self._submit_parallel(t)
  self.submitDeltaAtFork=int(self.submits)-before
  self.active=not self.errors and not self.authorityRejected
 def _allocate_overlay(self):
  new=[]
  for k in self.sharedKeys:
   cur=self._st(k)['cum'];r=self.ledger.allocate_cumulative(k,self.parentId,cur,float(self.spec['scopeDebt']))
   if r is not None:
    d=r.__dict__.copy();o=self.orders.get(k);d['price']=float(o['price']) if o else None;d['overflowNotional']=float(d['overflow_increment'])*float(d['price'] or 0.0);new.append(d);self.sharedAlloc.append(d)
    if float(d['overflow_increment'])>EPS:
     owner={'t':None,'event':'R239_STYLE_SHARED_PARENT_OVERFLOW_OWNER_BORN','parentId':self.parentId,'sourceKey':k,'overflowQty':float(d['overflow_increment']),'overflowNotional':float(d['overflowNotional']),'generation':int(self.spec['generation'])+1}
     self.successorOwners.append(owner)
  return new
 def _resolve(self,t,reasons,allocs):
  if not self.active or self.resolved or not reasons:return
  for o in self.successorOwners:
   if o['t'] is None:o['t']=int(t)
  self.resolved=True;self.active=False;par=self.ledger.describe_parent(self.parentId)
  self.firstEvent={'t':int(t),'reasons':reasons,'allocations':allocs,'sharedParent':par,'payoff':self._payoff(),'obligation':self._ob(),'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
   'successorOwners':list(self.successorOwners),'sharedOverflowQty':sum(float(x['overflow_increment']) for x in self.sharedAlloc),'sharedOverflowNotional':sum(float(x['overflowNotional']) for x in self.sharedAlloc)}
 def process(self,t):
  pre={k:self._st(k) for k in self.sharedKeys} if self.active else {};obpre=self._ob() if self.active else None;scopepre=(int(self.scopeGeneration),self.scopeSide) if self.active else None
  super().process(t)
  if not self.active:return
  allocs=self._allocate_overlay();reasons=[]
  for a in allocs:
   if float(a['fill_increment'])>EPS:reasons.append({'type':'CONFIRMED_SHARED_SIBLING_FILL','key':a['carrier_key'],'fillQty':a['fill_increment']})
  for k in self.sharedKeys:
   p=pre.get(k,{});n=self._st(k)
   if p and p.get('status') not in v2.TERMINAL_STATUSES and n['status'] in v2.TERMINAL_STATUSES:reasons.append({'type':'SHARED_SIBLING_TERMINAL','key':k,'status':n['status']})
  obnow=self._ob()
  if obpre and obnow and (not near(obpre.get('outstanding') or 0,obnow.get('outstanding') or 0) or not near(obpre.get('repaidQty') or 0,obnow.get('repaidQty') or 0)):
   reasons.append({'type':'R257_OBLIGATION_PAYMENT_OR_TRANSITION','before':obpre,'after':obnow})
  if scopepre!=(int(self.scopeGeneration),self.scopeSide):reasons.append({'type':'SCOPE_GENERATION_TRANSITION','before':scopepre,'after':(int(self.scopeGeneration),self.scopeSide)})
  for k,c0 in self.activeCum0.items():
   if self._st(k)['cum']>c0+EPS:reasons.append({'type':'INHERITED_ACTIVE_FILL','key':k,'fillQty':self._st(k)['cum']-c0})
  if self.successorOwners:reasons.append({'type':'SHARED_OVERFLOW_SUCCESSOR_BIRTH','count':len(self.successorOwners)})
  self._resolve(t,reasons,allocs)
 def _risk_contract_if_needed(self,t):
  if self.active:self.managerBlocks['risk']+=1;return
  return super()._risk_contract_if_needed(t)
 def _reanchor_stale(self,t):
  if self.active:self.managerBlocks['reanchor']+=1;return
  return super()._reanchor_stale(t)
 def _open_one_option(self,t,qv,end):
  if self.active:self.managerBlocks['open']+=1;return
  if not self.triggered and int(t)==int(self.spec['t']):
   before=int(self.submits);super()._open_one_option(t,qv,end);native_delta=int(self.submits)-before
   if native_delta!=0:self.errors.append('BASELINE_PHYSICAL_SUBMIT_AT_FORK_RECEIPT')
   self._activate(t);return
  return super()._open_one_option(t,qv,end)
 def run_fork(self,winner):
  r=self.run_audit(winner)
  if self.spec.get('negative'):
   correct=self.triggered and self.authorityRejected and self.submitDeltaAtFork==0 and not self.errors
  else:
   expect=0 if self.branch=='INHERITED_SERIAL_REPAIR' else 1
   par=self.ledger.describe_parent(self.parentId);ov=float((par or {}).get('overflowBorn') or 0.0);rp=float((par or {}).get('repairPaid') or 0.0)
   conservation=near(rp+ov,sum(float(x['fill_increment']) for x in self.sharedAlloc),2e-7) and rp<=float(self.spec['scopeDebt'])+EPS and ov<=float(self.spec['parentOverflowQtyCap'])+EPS
   risk=sum(float(x['overflowNotional']) for x in self.sharedAlloc)
   owners=(ov<=EPS or sum(float(x['overflowQty']) for x in self.successorOwners)+EPS>=ov)
   correct=self.triggered and self.submitDeltaAtFork==expect and self.resolved and not self.errors and conservation and risk<=float(self.spec['combinedAuthoritySlack'])+EPS and owners
  return {'marketId':self.mid,'branch':self.branch,'triggered':self.triggered,'authorityRejected':self.authorityRejected,'resolved':self.resolved,'errors':self.errors,'trigger':self.trigger,'candidateKey':self.candidateKey,'submitDeltaAtFork':self.submitDeltaAtFork,'firstStructuralEvent':self.firstEvent,
   'sharedAllocations':self.sharedAlloc,'sharedParent':self.ledger.describe_parent(self.parentId),'successorOwners':self.successorOwners,'managerBlocks':self.managerBlocks,'underlyingCorrect':bool(r.get('r264CorrectnessPass')),'nativeUnauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0)),'nativeRepairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'correct':bool(correct)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946468,1946792,1946876');ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 tmp=Path(tempfile.mkdtemp(prefix='lane_g_shared_parent_fork_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  rows=[]
  for m in mids:
   branches=('SHARED_PARENT_PARALLEL_RESIDUAL_PASSIVE',) if FORKS[m].get('negative') else BRANCHES
   for b in branches:
    s=SharedParentParallelResidualFork(tmp/f'{m}.json.xz',m,b)
    try:r=s.run_fork(co[m]['winner'])
    finally:s.close()
    rows.append(r);print(json.dumps({'marketId':m,'branch':b,'triggered':r['triggered'],'rejected':r['authorityRejected'],'submitDelta':r['submitDeltaAtFork'],'resolved':r['resolved'],'event':(r.get('firstStructuralEvent') or {}).get('reasons'),'parent':r.get('sharedParent'),'correct':r['correct'],'errors':r['errors']},ensure_ascii=False),flush=True)
  parity={}
  for m in [x for x in mids if not FORKS[x].get('negative')]:
   rr=[x for x in rows if x['marketId']==m];parity[str(m)]=len(rr)==2 and rr[0].get('trigger')==rr[1].get('trigger')
  vectors=[]
  for x in rows:
   tr=x.get('trigger') or {};ev=x.get('firstStructuralEvent') or {};tp=tr.get('payoff') or {};ep=ev.get('payoff') or {};par=x.get('sharedParent') or {}
   vectors.append({'marketId':x['marketId'],'branch':x['branch'],'eventT':ev.get('t'),'eventReasons':ev.get('reasons'),'deltaBestFromFrozenTrigger':(float(ep['best'])-float(tp['best']) if ep and tp else None),'deltaFloorFromFrozenTrigger':(float(ep['floor'])-float(tp['floor']) if ep and tp else None),'deltaGapFromFrozenTrigger':(float(ep['gap'])-float(tp['gap']) if ep and tp else None),'sharedRepairAllocated':float(par.get('repairPaid') or 0.0),'sharedOverflowQty':float(par.get('overflowBorn') or 0.0),'sharedOverflowNotional':float(ev.get('sharedOverflowNotional') or 0.0),'successorResponsibilityBorn':bool(x.get('successorOwners')),'executionReachability':bool(x.get('candidateKey')),'correct':x['correct']})
  neg=[x for x in rows if FORKS[x['marketId']].get('negative')]
  out={'version':'LANE_G_SHARED_PARENT_PARALLEL_RESIDUAL_EXACT_FORK_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'actionVectors':vectors,'triggerParityByMarket':parity,
   'gates':{'allPositiveTriggered':all(x['triggered'] for x in rows if not FORKS[x['marketId']].get('negative')),'allPositiveResolvedStructural':all(x['resolved'] for x in rows if not FORKS[x['marketId']].get('negative')),'triggerParity':all(parity.values()),'negativeAuthorityRejected':all(x['authorityRejected'] and x['submitDeltaAtFork']==0 for x in neg),'correctnessPass':all(x['correct'] for x in rows)},
   'boundary':['preregistered exact receipts only','physical order path is inherited realistic HFT queue engine','candidate delta is exactly one additional distinct-price SATELLITE_REPAIR after inherited serial no-submit','R269 ledger is fork allocation authority until first structural event','manager mutations frozen during fork','no fixed-time stopping rule','no new authority','pending zero payment/protection/credit','max4 and <=180s inherited','consumed only','fresh untouched','no dream fill','no 8781']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'vectors':vectors},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
