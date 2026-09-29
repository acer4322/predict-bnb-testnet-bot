from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v23_queue_progress_lease as v23
except ImportError:v23=sib('v23for30','run_eth_repair_functional_exam_v23_queue_progress_lease.py')
try:
 from tools import run_eth_repair_functional_exam_v19_economic_ceiling_preposition as v19
except ImportError:v19=sib('v19for30','run_eth_repair_functional_exam_v19_economic_ceiling_preposition.py')
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17for30','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16for30','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15for30','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13for30','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9for30','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class DirectionalThesisCycleSim(v23.QueueProgressLeaseSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.thesis=None;self.nextThesisId=1
  self.thesisOpenSubmits=0;self.thesisFirstActualFills=0;self.payoffRepairCompletions=0
  self.payoffRecoveryAbsNet=[];self.payoffRecoveryFloor=[];self.payoffRepairQtyCaps=[]
  self.reexpandSubmits=0;self.reexpandActualFills=0;self.signalHoldBlocks=0;self.thesisCycleCompletions=0
  self.thesisEvents=[];self._countedFirst=set();self._countedRecovery=set();self._pendingReexpandKeys=set();self.payoffInfeasiblePriceBlocks=0;self.payoffSatisfiedRepairCancels=0;self.lastPayoffRecoveryAt=None
 def _signal_side(self,qv):
  return 'UP' if float(qv.get('imb',0.0))>=0.0 else 'DOWN'
 def _start_reserve_builder(self,t,qv):
  floor,u,d,cost=self._raw_floor();s=float(qv['UP']['bid'])+float(qv['DOWN']['bid'])
  if self.reserveBuilder is not None or self.repairParent is not None or self.outstanding_total()>EPS:return False
  # Thesis birth keeps the inherited thin-safe package gate. Re-expansion only requires recovered floor plus a cheap passive pair route.
  if self.thesis is None:
   g=u+d;absr=abs(u-d)/g if g>EPS else 0.0
   if not (0<=floor<1.0 and absr<=.02 and s<1.-EPS):return False
   side=self._signal_side(qv);is_reexpand=False
  else:
   if floor<-EPS or s>=1.-EPS:return False
   side=self._signal_side(qv)
   if side!=self.thesis['side']:
    self.signalHoldBlocks+=1;return False
   side=self.thesis['side'];is_reexpand=True
  p=float(qv[side]['bid']);q=1.0/p if p>EPS else 1e9
  if q<=EPS or q>12.+EPS:return False
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='DIRECTION_OPEN_FIRST'
  ok=self.submit(t,side,p,q)
  if not ok:return False
  key=f'{side}_{n0}'
  if self.thesis is None:
   self.thesis={'id':self.nextThesisId,'side':side,'bornAt':int(t),'materialized':False,'recoveries':0,'opens':0};self.nextThesisId+=1
  self.thesis['opens']+=1;self.thesisOpenSubmits+=1
  if is_reexpand:self.reexpandSubmits+=1;self._pendingReexpandKeys.add(key)
  self.reserveFirstLegSubmit+=1;self.reserveFirstKeys.append(key)
  self.reserveBuilder={'id':self.nextReserveBuilderId,'firstKey':key,'firstSide':side,'firstPrice':p,'firstQty':q,'submittedAt':int(t),'firstFillAt':None,'floorBefore':floor,'pairDeadline':None,'completed':False,'thesisId':self.thesis['id'],'isReexpand':is_reexpand}
  self.nextReserveBuilderId+=1
  self.thesisEvents.append({'t':int(t),'event':'REEXPAND_SUBMIT' if is_reexpand else 'OPEN_DIRECTION_SUBMIT','side':side,'price':p,'qty':q,'floor':floor})
  return True
 def _maybe_birth_parent(self,t,weak,cap):
  if self.thesis is not None:
   floor,_,_,_=self._raw_floor();rb=self.reserveBuilder
   # Non-zero absNet with a non-negative floor is intentional directional surplus, not a new Repair debt.
   if floor>=-EPS and (rb is None or rb.get('payoffRecovered') or rb.get('firstFillAt') is not None):return
  return super()._maybe_birth_parent(t,weak,cap)
 def _repair_payoff_budget(self,p):
  rb=self.reserveBuilder;parent=self.repairParent
  if rb is None or rb.get('firstFillAt') is None or parent is None:return None
  side=parent.get('side');opp='DOWN' if side=='UP' else 'UP' if side=='DOWN' else None
  if opp is None:return None
  floor,_,_,_=self._raw_floor();ai=self.auth_inv();gap=max(0.0,float(ai[opp])-float(ai[side]))
  rows=self.lane_unresolved('REPAIR');owned=0.0;reserved_gain=0.0
  for key,e,rem in rows:
   q=float(rem);owned+=q;op=float(self.orders.get(key,{}).get('price') or e.get('price') or 0.0)
   if 0.0<op<1.0:reserved_gain+=q*(1.0-op)
  deficit=max(0.0,-float(floor)-reserved_gain);room=max(0.0,gap-owned)
  if deficit<=EPS:return {'qty':0.0,'deficit':0.0,'room':room,'gap':gap,'owned':owned,'reservedGain':reserved_gain,'feasible':True}
  if p<=EPS or p>=1.0-EPS:return {'qty':0.0,'deficit':deficit,'room':room,'gap':gap,'owned':owned,'reservedGain':reserved_gain,'feasible':False}
  need=deficit/(1.0-float(p));legal=1.0/float(p)
  q=max(need,legal)
  feasible=q<=room+EPS
  return {'qty':min(q,room) if feasible else 0.0,'need':need,'legal':legal,'deficit':deficit,'room':room,'gap':gap,'owned':owned,'reservedGain':reserved_gain,'feasible':feasible}
 def _continuous_capacity_reconcile(self,t):
  self._refresh_carrier_ledger(t)
  rb=self.reserveBuilder
  if rb is None or rb.get('firstFillAt') is None or self.repairParent is None:
   return super()._continuous_capacity_reconcile(t)
  floor,_,_,_=self._raw_floor()
  if floor>=-EPS:
   for key,e,rem in self.lane_unresolved('REPAIR'):
    if rem>EPS and not e.get('cancelRequested') and self._cancel_key(t,key):self.payoffSatisfiedRepairCancels+=1
   return
  # While floor is still negative, existing Repair reservations remain authoritative.
  # Do not create/cancel against absNet gap merely because the geometric gap changed.
  return
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is None:return False
  side,qty,oldp,role,oid=z;rb=self.reserveBuilder
  if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None and int(t)>int(rb.get('pairDeadline') or t):
   self.packageRepairFallbackEval+=1;p=float(qv[side]['bid']);b=self._repair_payoff_budget(p)
   if not b or not b.get('feasible') or float(b.get('qty') or 0.0)<=EPS:
    self.payoffInfeasiblePriceBlocks+=1;return False
   z=(side,min(float(qty),float(b['qty'])),oldp,role,oid)
   return v17.PostSafeSurplusSim._submit_authorized(self,t,qv,z,roles_this_tick)
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def _submit_package_at_price(self,t,qv,z,roles_this_tick,p):
  if z is None:return False
  side,qty,oldp,role,oid=z
  rb=self.reserveBuilder
  if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None:
   floor,_,_,_=self._raw_floor();b=self._repair_payoff_budget(p)
   if not b or not b.get('feasible') or float(b.get('qty') or 0.0)<=EPS:
    self.payoffInfeasiblePriceBlocks+=1;return False
   capped=min(float(qty),float(b['qty']))
   if capped<float(qty)-EPS:self.payoffRepairQtyCaps.append({'t':int(t),'oldQty':float(qty),'payoffNeedQty':float(b.get('need') or 0.0),'newQty':float(capped),'price':float(p),'floor':float(floor),'room':float(b.get('room') or 0.0),'reservedGain':float(b.get('reservedGain') or 0.0)})
   z=(side,capped,oldp,role,oid)
  return v19.EconomicCeilingReserveSim._submit_package_at_price(self,t,qv,z,roles_this_tick,p)
 def _complete_parent_if_structural(self,t,ai):
  p=self.repairParent;rb=self.reserveBuilder
  if p is not None and rb is not None and rb.get('firstFillAt') is not None:
   floor,_,_,_=self._raw_floor()
   if floor<-EPS:return
   if self.parent_unresolved():
    self.parentCompletionDeferred+=1;return
   self.repairParentCompletions+=1;self.repairParent=None;self.repairLaneObjective=None;self.expandLaneObjective=None;self.activeObjective=None;self.awaitingReentry=False;rb['payoffRecovered']=True
   self.payoffRepairCompletions+=1;self.lastPayoffRecoveryAt=int(t);self.payoffRecoveryAbsNet.append(abs(float(ai['UP'])-float(ai['DOWN'])));self.payoffRecoveryFloor.append(float(floor))
   if self.thesis:self.thesis['recoveries']+=1
   self.thesisEvents.append({'t':int(t),'event':'PAYOFF_RECOVERED','side':rb.get('firstSide'),'floor':float(floor),'absNet':self.payoffRecoveryAbsNet[-1]})
   return
  return super()._complete_parent_if_structural(t,ai)
 def _maybe_reexpand_after_recovery(self,t):
  th=self.thesis
  if th is None or self.lastPayoffRecoveryAt is None or int(t)<=int(self.lastPayoffRecoveryAt):return False
  if int(self.capEnd)-int(t)<=180000:return False
  if self.reserveBuilder is not None or self.repairParent is not None or self.outstanding_total()>EPS:return False
  # One new directional open per completed passive-repair recovery.
  if int(th.get('opens') or 0)>int(th.get('recoveries') or 0):return False
  floor,_,_,_=self._raw_floor()
  if floor<-EPS:return False
  qv=v1.quotes(self.book)
  if not qv:return False
  return bool(self._start_reserve_builder(t,qv))
 def process(self,t):
  rb_before=self.reserveBuilder
  super().process(t)
  rb=rb_before if rb_before is not None else self.reserveBuilder
  if rb is None:return
  key=rb.get('firstKey')
  if rb.get('firstFillAt') is not None and key not in self._countedFirst:
   self._countedFirst.add(key);self.thesisFirstActualFills+=1
   if self.thesis:self.thesis['materialized']=True
   if key in self._pendingReexpandKeys:self.reexpandActualFills+=1
   self.thesisEvents.append({'t':int(rb['firstFillAt']),'event':'REEXPAND_ACTUAL_FILL' if rb.get('isReexpand') else 'OPEN_DIRECTION_ACTUAL_FILL','side':rb.get('firstSide'),'price':rb.get('firstPrice'),'qty':rb.get('firstFilledQty')})
  # V19 completion is balance-based. V30 additionally completes the package after payoff recovery, preserving nonzero directional surplus.
  if rb.get('firstFillAt') is not None and not rb.get('completed'):
   floor,_,_,_=self._raw_floor()
   if floor>=-EPS and self.repairParent is None and self.outstanding_total()<=EPS:
    rb['completed']=True;self.reserveCycleCompletion+=1;self.reserveCycleFloorGain.append(float(floor-float(rb['floorBefore'])))
    if rb.get('repairFillPrice') is not None:self.reserveCyclePairSums.append(float(rb['firstPrice']+rb['repairFillPrice']))
    self.thesisCycleCompletions+=1;self._countedRecovery.add(rb['id']);self.thesisEvents.append({'t':int(t),'event':'THESIS_PACKAGE_COMPLETE','side':rb.get('firstSide'),'floor':float(floor),'absNet':abs(self.auth_inv()['UP']-self.auth_inv()['DOWN'])})
    self.reserveBuilder=None
  self._maybe_reexpand_after_recovery(t)
 def run_exam_v30(self,models,winner):
  r=super().run_exam_v23(models,winner)
  floor,_,_,_=self._raw_floor()
  r.update({'floor':floor,'thesisSide':self.thesis.get('side') if self.thesis else None,'thesisOpenSubmits':self.thesisOpenSubmits,'thesisFirstActualFills':self.thesisFirstActualFills,'payoffRepairCompletions':self.payoffRepairCompletions,'payoffRecoveryNonzeroAbsNet':sum(x>EPS for x in self.payoffRecoveryAbsNet),'payoffRecoveryAbsNet':self.payoffRecoveryAbsNet,'payoffRecoveryFloor':self.payoffRecoveryFloor,'payoffRepairQtyCapEvents':len(self.payoffRepairQtyCaps),'payoffInfeasiblePriceBlocks':self.payoffInfeasiblePriceBlocks,'payoffSatisfiedRepairCancels':self.payoffSatisfiedRepairCancels,'payoffRepairQtyCaps':self.payoffRepairQtyCaps[:30],'reexpandSubmits':self.reexpandSubmits,'reexpandActualFills':self.reexpandActualFills,'signalHoldBlocks':self.signalHoldBlocks,'thesisCycleCompletions':self.thesisCycleCompletions,'thesisEvents':self.thesisEvents[:80]})
  return r

def load_runtime(a):
 models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);return models,life,cap,tim,econ,price,sur

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1841535,1841561,1842220,1842306');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v30_thesis_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   if mid not in by:raise KeyError(f'market {mid} not in bundle')
   cr=by[mid];sim=DirectionalThesisCycleSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v30(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'thesis':r['thesisSide'],'opens':r['thesisOpenSubmits'],'firstFills':r['thesisFirstActualFills'],'recoveries':r['payoffRepairCompletions'],'nonzeroRecovery':r['payoffRecoveryNonzeroAbsNet'],'reexpandSubmits':r['reexpandSubmits'],'reexpandFills':r['reexpandActualFills'],'cycles':r['thesisCycleCompletions'],'floor':r['floor'],'absNet':r['absNet']},ensure_ascii=False),flush=True)
  def sm(k):return sum(float(x['functional'].get(k) or 0) for x in rows)
  pairmax=max((float(x['functional'].get('reserveCyclePairSumMax') or 0) for x in rows),default=0.0)
  agg={k:sm(k) for k in ['thesisOpenSubmits','thesisFirstActualFills','payoffRepairCompletions','payoffRecoveryNonzeroAbsNet','payoffRepairQtyCapEvents','payoffInfeasiblePriceBlocks','payoffSatisfiedRepairCancels','reexpandSubmits','reexpandActualFills','signalHoldBlocks','thesisCycleCompletions','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','unresolvedCarrierQty']};agg['maxCompletedPairSum']=pairmax
  gates={'directionMaterialized':agg['thesisFirstActualFills']>0,'payoffRepairRecovered':agg['payoffRepairCompletions']>0,'nonzeroAbsNetRecoveryExercised':agg['payoffRecoveryNonzeroAbsNet']>0,'sameThesisReexpandSubmitted':agg['reexpandSubmits']>0,'sameThesisReexpandActualFill':agg['reexpandActualFills']>0,'zeroRepairDrift':agg['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':agg['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':agg['overOwnedSubmitViolations']==0,'zeroUnresolved':abs(agg['unresolvedCarrierQty'])<=EPS,'completedPairSumLt1':agg['thesisCycleCompletions']==0 or (pairmax>0 and pairmax<1.0)}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V30_DIRECTIONAL_THESIS_CYCLE','researchOnly':True,'directionProxy':'BOOK_IMBALANCE_SIGN_REPLACEABLE','selectedMarketIds':[x['marketId'] for x in rows],'aggregate':agg,'gates':gates,'functionalCorePass':all(gates.values()),'rows':rows,'boundary':['consumed realistic HFT only','Maker-only; no Taker','V23 deterministic ownership/economic ceiling/queue-progress lease preserved','direction proxy is replaceable and not graduation evidence','Repair package completion uses payoff floor recovery rather than absNet=0','winner/PnL diagnostic only and excluded from gates','no threshold tuning','<=180s inherited no-new-exposure rule']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalCorePass':out['functionalCorePass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
