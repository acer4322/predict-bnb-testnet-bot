"""Research-only OpenFunding native ACTIVE lifecycle smoke.

Purpose: prove one explicitly authorized ACTIVE carrier can pass through native
HftBacktest send -> receipt/partial/zero-fill -> cancel/terminal -> OpenFunding
ownership/accounting. This is NOT a strategy or Target imitation test.

No Target actions/winner/PnL are read. No live authority. Qty=18 is a fixed
functional fixture only, not an Active minimum/cap/strategy parameter.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import gzip,hashlib,importlib.util,json,math,os
from pathlib import Path
import shutil,sys,time,traceback

BUNDLE=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/open_funding_recovery_train_20260911_v3')
TEMPLATE_ROOT=Path(r'C:/BTC5M-worker/.tmp/open_funding_recovery_train_20260911_v3')
BACKEND=Path(r'C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
OPEN_POLICY_SHA='507bd3724ec534e6c9c64a479aa3985f77304f796529bad765780e9e538ca1dd'
RECOVERY_SHA='2468fd631c277e114c3f943bfc9f4867c4f06bdab5f69a26b24d8a8685dd00f1'
FIXTURE_QTY=18.0

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(262144),b''):h.update(b)
 return h.hexdigest()

def load_source(mid,old):
 p=BUNDLE/f'input_{mid}.json.gz'
 with gzip.open(p,'rb') as f:data=f.read(8*1024*1024+1)
 if len(data)>8*1024*1024:raise RuntimeError('source too large')
 s=json.loads(data);assert int(s['market']['market_id'])==mid
 assert [s['market']['window_start_ms'],s['market']['window_end_ms']]==old['marketWindows'][str(mid)]
 return s

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market',type=int,required=True);ap.add_argument('--side',choices=('UP','DOWN'),default='UP');a=ap.parse_args()
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);started=time.monotonic();sim=None
 result=dict(version='OPEN_FUNDING_NATIVE_ACTIVE_LIFECYCLE_SMOKE_V1',status='RUNNING',market_id=a.market,side=a.side,
  researchOnly=True,strategyClaim=False,targetDataUsed=False,winnerRead=False,pnlUsed=False,dreamFill=False,
  funding_mode='VIRTUAL_NONBINDING_RESEARCH',capital_cap=None,fixture_qty=FIXTURE_QTY,
  fixture_qty_semantics='FUNCTIONAL_FIXTURE_NOT_ACTIVE_MINIMUM_CAP_OR_POLICY',live_changes=0,worker=os.environ.get('COMPUTERNAME'))
 try:
  assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
  root=Path(r'C:/BTC5M-worker/.tmp')/('open_funding_native_active_smoke_'+out.name)
  if root.exists():shutil.rmtree(root)
  shutil.copytree(TEMPLATE_ROOT,root);assert sha(root/'tools/minimal_student_open_funding_v1.py')==OPEN_POLICY_SHA;assert sha(root/'tools/open_funding_recovery_runtime_v3.py')==RECOVERY_SHA
  old=json.loads((Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')/'MANIFEST.json').read_text(encoding='utf-8'))
  sys.path.insert(0,str(root));os.chdir(root);binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
  sys.path.insert(0,str(BACKEND));import hftbacktest as h;assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
  from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
  from tools.hft244_minimal_pair_accounting_v1 import install
  from tools.minimal_student_quantity_seam_v1 import make_student_class,TERMINAL
  from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger,make_recovered_student
  from tools.minimal_student_open_funding_v1 import OpenFundingProfile
  from tools.minimal_student_training_world_v2 import TrainingPlanGateway,TrainingPlan,training_frame_id,full_input
  from tools.minimal_student_native_system_plan_v1 import Envelope
  from tools.minimal_student_system_plan_v1 import PlanAction,PlanRejected
  from tools.pair_core_asset_route_sizing_v2 import validate_size
  from tools.pair_core_economic_grant_ledger_v1 import Grant
  install(minimal.v2.base,binary);base=minimal.v2.base;oldsend=base.ex.submit_native
  def strict_send(*args,**kw):
   rc=oldsend(*args,**kw)
   if int(rc)!=0:raise RuntimeError('passive native submit rejected/uncertain:'+str(rc))
   return rc
  base.ex.submit_native=strict_send
  Parent=make_recovered_student(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim))
  profile=OpenFundingProfile(max_live_owners=4096);source=load_source(a.market,old);start,end=old['marketWindows'][str(a.market)]

  class Trace:
   def __init__(self):self.now=0;self.states=[];self.actions=[];self.receipts=[];self.receipt_ids=set();self.plans=[];self.terminal_logged=set()
   def emit(self,kind,x):
    if kind=='canonical_receipt':self.receipts.append(deepcopy(x))
   def action(self,x):self.actions.append(deepcopy(x))
   def process(self,student,t):
    for rr in student._receipt_delta_rows:
     seq=rr.get('sequence')
     if seq not in self.receipt_ids:self.receipt_ids.add(seq);self.receipts.append(deepcopy(rr))
    self.states.append(dict(t=int(t),inv=dict(student.inv),cost=float(student.cost)))
   def plan(self,x):self.plans.append(deepcopy(x))

  class Producer:
   policy_id='OPEN_FUNDING_ACTIVE_SMOKE_V1';continuation_id=policy_id+':LIFECYCLE';provenance='FUNCTIONAL_ACTIVE_ACTUATOR_TEST_NOT_TARGET_POLICY'
   def __init__(self):self.calls=0;self.sent=False;self.key=None;self.cancel_issued=False
   def produce(self,f):
    self.calls+=1;actions=[];ops=[];live={k:c for k,c in f['ledger'].carriers.items() if c.state!='TERMINAL'}
    # First public frame with a valid same-side ask: send exactly one explicit ACTIVE fixture.
    if not self.sent and f['start']<=f['t']<f['end']:
     q=f.get('quotes') or {};ask=(q.get(a.side) or {}).get('ask')
     if ask is not None and 0<float(ask)<1:
      validate_size('BTC','ACTIVE',float(ask),FIXTURE_QTY,quantity_step=.01)
      pid=1 if a.side=='UP' else 2;key=f'{a.side}_{f["own_view"]["n"]}';self.key=key;self.sent=True
      actions.append(PlanAction('NEW',key,pid,'ACTIVE',float(ask),FIXTURE_QTY));ops.append(dict(kind='NEW',key=key,parent_id=pid,side=a.side,route='ACTIVE',price=float(ask),qty=FIXTURE_QTY,role='ACTIVE_FUNCTIONAL_FIXTURE'))
    elif self.key in live:
     # Event-driven close: the first later frame on which native marks the order cancellable.
     # This is a functional lifecycle boundary, not a trading timeout.
     if not self.cancel_issued and f['cancellable'].get(self.key,False):
      actions.append(PlanAction('CANCEL',self.key));ops.append(dict(kind='CANCEL',key=self.key,origin='WHOLE_POLICY',reason='ACTIVE_FUNCTIONAL_CLOSE'));self.cancel_issued=True
     else:actions.append(PlanAction('KEEP',self.key))
    for key,c in live.items():
     if key!=self.key:actions.append(PlanAction('KEEP',key))
    plan=TrainingPlan(f'{self.policy_id}:{f["index"]}',f['gateway_state_id'],self.policy_id,self.continuation_id,tuple(actions),())
    return Envelope(training_frame_id(f),plan,ops,self.provenance)

  def validate_active(frame,env,policy_id,continuation_id):
   if not isinstance(env,Envelope) or not isinstance(env.plan,TrainingPlan):raise PlanRejected('TRAINING_WHOLE_PLAN_REQUIRED')
   if env.frame_id!=training_frame_id(frame):raise PlanRejected('STALE_WORLD_MARKET_OR_OWN_FRAME')
   p=env.plan
   if p.policy_id!=policy_id or p.continuation_id!=continuation_id or p.state_id!=frame['gateway_state_id']:raise PlanRejected('POLICY_OR_STATE_MISMATCH')
   oldlive={k for k,c in frame['ledger'].carriers.items() if c.state!='TERMINAL'};maint={x.key for x in p.actions if x.kind in ('KEEP','CANCEL')}
   if oldlive!=maint:raise PlanRejected('WHOLE_LIVE_OWNER_SET_MUST_BE_EXPLICIT')
   if len({x.key for x in p.actions})!=len(p.actions):raise PlanRejected('DUPLICATE_ACTION')
   acts={x.key:x for x in p.actions};seen=set();n=frame['own_view']['n']
   for op in env.operations:
    key=op['key'];seen.add(key);aa=acts.get(key)
    if aa is None or aa.kind!=op['kind']:raise PlanRejected('OPERATION_ACTION_IDENTITY')
    if aa.kind=='NEW':
     if aa.route!='ACTIVE':raise PlanRejected('SMOKE_ONLY_ACTIVE')
     if aa.parent_id not in frame['ledger'].grants:raise PlanRejected('NO_OWNER')
     side=frame['ledger'].grants[aa.parent_id].side
     if side!=op['side'] or key!=f'{side}_{n}' or aa.qty!=op['qty'] or aa.price!=op['price']:raise PlanRejected('ACTIVE_METADATA_MISMATCH')
     validate_size('BTC','ACTIVE',aa.price,aa.qty,quantity_step=.01);ask=((frame.get('quotes') or {}).get(side) or {}).get('ask')
     if ask is None or abs(float(ask)-float(aa.price))>1e-9:raise PlanRejected('ACTIVE_PRICE_NOT_CURRENT_ASK_FIXTURE')
     n+=1
    elif aa.kind=='CANCEL':
     if not frame['cancellable'].get(key,False):raise PlanRejected('CANCEL_NOT_CANCELLABLE')
    else:raise PlanRejected('NO_PHYSICAL_KEEP_OPERATION')
   if seen!={x.key for x in p.actions if x.kind!='KEEP'}:raise PlanRejected('UNREPRESENTED_OPERATION')
   return True

  class ActiveStudent(Parent):
   def __init__(self,*args,**kw):
    super().__init__(*args,**kw)
    self.gateway=TrainingPlanGateway(self.gateway.ledger,asset='BTC',policy_id=self.producer.policy_id,capabilities=('PASSIVE','ACTIVE'),tick=self.gateway.ledger.profile.tick,quantity_step=self.gateway.ledger.profile.quantity_step)
   def consume(self,frame,env):
    if self.gateway.snapshot_id()!=frame['gateway_state_id']:raise PlanRejected('STALE_NATIVE_OWN_STATE')
    validate_active(frame,env,self.producer.policy_id,self.producer.continuation_id);captured=full_input(frame)
    self.gateway.commit(env.plan,now_ms=frame['t'],market_end_ms=frame['end']);self._executed_policy_ids.add(env.plan.policy_id);counts=Counter(x.kind for x in env.plan.actions);self.plan_kinds.update(counts);self.keep_count+=counts['KEEP']
    if self.receipt_frame:self.post_receipt_plans+=1;self.receipt_frame=False
    self._refresh_slots(frame['t']);self.book=deepcopy(frame['book'])
    for op in env.operations:
     if op['kind']=='CANCEL':
      o=self.orders[op['key']];rc=int(self.bt.cancel(0,o['n'],False))
      if rc!=0:raise RuntimeError('ACTIVE_CANCEL_TRANSPORT_UNCERTAIN')
      o['cancelRequested']=True;self.cancel_count+=1;self.trace.action(dict(kind='CANCEL',t=frame['t'],n=o['n'],key=op['key'],rc=rc))
     elif op['kind']=='NEW':
      free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
      if free is None:raise RuntimeError('ACTIVE_SMOKE_NO_SLOT')
      n=self.n;self.n+=1;native_side,native_price=base.ex.native_order(op['side'],op['price'])
      try:
       if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.GTC,base.ex.hbt.LIMIT,False))
       else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.GTC,base.ex.hbt.LIMIT,False))
      except Exception:
       self.gateway.record_send(op['key'],'UNKNOWN',evidence='ACTIVE_NATIVE_SEND_EXCEPTION_KEEP_RESERVED');raise
      if rc!=0:self.gateway.record_send(op['key'],'UNKNOWN',evidence='ACTIVE_NATIVE_NONZERO_KEEP_RESERVED');raise RuntimeError('ACTIVE_NATIVE_SUBMIT_NONZERO:'+str(rc))
      self.orders[op['key']]=dict(n=n,side=op['side'],price=float(op['price']),qty=float(op['qty']),cum=0.,placed=int(frame['t']),status='NEW');self.placeHist.append((int(frame['t']),op['side'],float(op['qty']),float(op['price'])));self.submits+=1
      self.slot_key[int(free)]=op['key'];self.key_role[op['key']]=op['role'];self.role_submits[op['role']]+=1;self.gateway.record_send(op['key'],'SENT',evidence='ACTIVE_NATIVE_SUBMIT_RC0');self.trace.action(dict(kind='NEW_ACTIVE',t=frame['t'],n=n,key=op['key'],side=op['side'],price=op['price'],qty=op['qty'],rc=rc))
    self.trace.plan(dict(t=frame['t'],input_frame=captured,plan=asdict(env.plan),operations=env.operations,own_after_plan=self.gateway.own_state(),policy_provenance=env.provenance,target_data_in_model_input=False));self.frame_count+=1

  ledger=FastOpenFundingLedger(profile)
  for pid,side in ((1,'UP'),(2,'DOWN')):ledger.issue(Grant(pid,'ACTIVE_SMOKE',side,0.,0.,0.,'USER_AUTHORIZED_VIRTUAL_NONBINDING_FUNDING'))
  producer=Producer();trace=Trace();sim=ActiveStudent(root/f'tapes/{a.market}.json.xz',profile.max_live_owners,False,producer=producer,ledger=ledger,trace=trace,verified_window=[start,end],window_source_sha256=old['marketWindowSourceSha256'])
  sim.run_whole(base);drain=sim.drain_queued_responses(base);actual=sim.gateway.ledger;actual.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
  if sim._receipt_invalid:raise RuntimeError('receipt invalid')
  unresolved=[k for k,c in actual.carriers.items() if c.state!='TERMINAL']
  if unresolved:raise RuntimeError('UNRESOLVED_AFTER_NATIVE_DRAIN:'+','.join(unresolved))
  if producer.key is None or producer.key not in actual.carriers:raise RuntimeError('ACTIVE_OWNER_NOT_MATERIALIZED')
  c=actual.carriers[producer.key];account=actual.account(c.parent_id);native_actions=trace.actions
  accounting_valid=abs(sum(cc.payment+cc.fees for cc in actual.carriers.values())-sim.cost)<1e-7 and all(abs(sum(cc.filled for cc in actual.carriers.values() if actual.grants[cc.parent_id].side==s)-sim.inv[s])<1e-7 for s in ('UP','DOWN'))
  result.update(status='COMPLETE',key=producer.key,route=c.route,requested_qty=c.qty,filled_qty=c.filled,payment=c.payment,fees=c.fees,carrier_state=c.state,
   native_actions=native_actions,canonical_receipts=trace.receipts,canonical_receipt_count=len(trace.receipts),receipt_conditioned_continuations=sim.post_receipt_plans,
   cancel_issued=producer.cancel_issued,terminal_drain=drain,final_inventory=dict(sim.inv),final_cost=float(sim.cost),account=account,
   funding_demand=actual.funding_demand(),unresolved_owners=0,execution_accounting_valid=accounting_valid,
   gate=dict(active_native_submit_observed=any(x['kind']=='NEW_ACTIVE' for x in native_actions),route_is_active=c.route=='ACTIVE',terminal=True,ownership_closed=c.state=='TERMINAL',accounting_valid=accounting_valid))
  result['gate']['pass']=all(result['gate'].values())
 except Exception as ex:
  result['status']='ERROR';result['error']=type(ex).__name__+':'+str(ex);result['traceback']=traceback.format_exc(limit=14)
 finally:
  if sim is not None:
   try:sim.close()
   except Exception:pass
  result['elapsed_seconds']=time.monotonic()-started;(out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
 print(json.dumps({k:result.get(k) for k in ('status','market_id','side','route','requested_qty','filled_qty','carrier_state','canonical_receipt_count','cancel_issued','execution_accounting_valid','gate','error','elapsed_seconds')},ensure_ascii=False),flush=True)
 if result['status']!='COMPLETE' or not result['gate']['pass']:raise SystemExit(2)
if __name__=='__main__':main()
