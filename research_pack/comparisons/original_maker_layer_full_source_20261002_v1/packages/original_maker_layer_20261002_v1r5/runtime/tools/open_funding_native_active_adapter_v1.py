"""Research-only mixed PASSIVE/ACTIVE native adapter for the OpenFunding student.

This module adds actuator capability only. It does not decide side, role, price,
quantity, objective, timing, or risk allowance. ACTIVE is a GTC LIMIT at the
current same-side ask supplied by the caller's complete whole-plan envelope.
All existing ownership, receipt, send-uncertainty, terminal and OpenFunding
accounting remain authoritative.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from dataclasses import asdict

from tools.open_funding_recovery_runtime_v3 import make_recovered_student
from tools.minimal_student_training_world_v2 import (
    TrainingPlanGateway, TrainingPlan, training_frame_id, full_input, passive_ask,
)
from tools.minimal_student_native_system_plan_v1 import Envelope
from tools.minimal_student_system_plan_v1 import PlanRejected
from tools.pair_core_asset_route_sizing_v2 import validate_size


def validate_mixed_envelope(frame, env, policy_id, continuation_id):
    if not isinstance(env,Envelope) or not isinstance(env.plan,TrainingPlan):
        raise PlanRejected('TRAINING_WHOLE_PLAN_REQUIRED')
    if env.frame_id!=training_frame_id(frame):raise PlanRejected('STALE_WORLD_MARKET_OR_OWN_FRAME')
    p=env.plan
    if p.policy_id!=policy_id or p.continuation_id!=continuation_id:raise PlanRejected('UNDECLARED_POLICY_OR_CONTINUATION')
    if p.state_id!=frame['gateway_state_id']:raise PlanRejected('STALE_GATEWAY')
    old={k for k,c in frame['ledger'].carriers.items() if c.state!='TERMINAL'}
    if old!={a.key for a in p.actions if a.kind in ('KEEP','CANCEL')}:raise PlanRejected('WHOLE_LIVE_OWNER_SET_MUST_BE_EXPLICIT')
    if len({a.key for a in p.actions})!=len(p.actions):raise PlanRejected('DUPLICATE_ACTION')
    acts={a.key:a for a in p.actions};seen=set();n=frame['own_view']['n'];tick=float(frame['world_profile']['tick']);step=float(frame['world_profile']['quantity_step'])
    for op in env.operations:
        key=op['key'];a=acts.get(key)
        if key in seen or a is None or op['kind']!=a.kind:raise PlanRejected('OPERATION_ACTION_IDENTITY')
        seen.add(key)
        if a.kind=='NEW':
            if a.route not in ('PASSIVE','ACTIVE'):raise PlanRejected('UNKNOWN_ROUTE')
            if not frame['start']<=frame['t']<frame['end']:raise PlanRejected('MARKET_NOT_OPEN')
            if a.parent_id not in frame['ledger'].grants:raise PlanRejected('NO_GRANT')
            side=frame['ledger'].grants[a.parent_id].side
            if op.get('side')!=side or a.key!=f'{side}_{n}' or op.get('qty')!=a.qty or op.get('price')!=a.price or op.get('parent_id')!=a.parent_id:
                raise PlanRejected('NEW_INTENT_METADATA_MISMATCH')
            n+=1
            if not op.get('role'):raise PlanRejected('EXPLICIT_ROLE_METADATA_REQUIRED')
            validate_size(frame['world_profile']['asset'],a.route,a.price,a.qty,quantity_step=step)
            if abs(a.price/tick-round(a.price/tick))>1e-8:raise PlanRejected('PRICE_OFF_DECLARED_GRID')
            ask=passive_ask(frame['book'],side)
            if ask is None:raise PlanRejected('BOOK_SUPPORT_UNKNOWN_NOT_HOLD')
            if a.route=='PASSIVE':
                if a.price>=ask-1e-10:raise PlanRejected('PASSIVE_POSTONLY_MARKETABLE_REQUEST')
            else:
                q=(frame.get('quotes') or {}).get(side) or {};active_ask=q.get('ask')
                if op.get('role')=='ACTIVE_RENEWED_FINITE_CONTINUATION':
                    levels=list(frame['book']['asks']) if side=='UP' else [round(1-p,10) for p in frame['book']['bids']]
                    if active_ask is None or not levels or not float(active_ask)-1e-9<=float(a.price)<=max(levels)+1e-9:raise PlanRejected('ACTIVE_PRICE_OUTSIDE_VISIBLE_DEPTH')
                elif active_ask is None or abs(float(active_ask)-float(a.price))>1e-9:raise PlanRejected('ACTIVE_LIMIT_MUST_EQUAL_CURRENT_ASK')
        elif a.kind=='CANCEL':
            if not frame['cancellable'].get(key,False):raise PlanRejected('CANCEL_NOT_CURRENTLY_CANCELLABLE')
            if op.get('origin')!='WHOLE_POLICY':raise PlanRejected('ACTIVE_ADAPTER_CANCEL_REQUIRES_WHOLE_POLICY')
        else:raise PlanRejected('NO_PHYSICAL_KEEP_OPERATION')
    if seen!={a.key for a in p.actions if a.kind!='KEEP'}:raise PlanRejected('UNREPRESENTED_PHYSICAL_OPERATION')
    if len(old)+sum(a.kind=='NEW' for a in p.actions)>frame['world_profile']['max_live_owners']:
        raise PlanRejected('RESOURCE_CENSOR_OPEN_OWNER_LIMIT_NOT_HOLD')
    return True


def make_mixed_training_class(frozen_minimal, exact_class, base):
    Parent=make_recovered_student(frozen_minimal,exact_class)
    class MixedOpenFundingStudent(Parent):
        def __init__(self,*args,**kw):
            super().__init__(*args,**kw)
            self.gateway=TrainingPlanGateway(self.gateway.ledger,asset=self.gateway.ledger.profile.asset,
                policy_id=self.producer.policy_id,capabilities=('PASSIVE','ACTIVE'),
                tick=self.gateway.ledger.profile.tick,quantity_step=self.gateway.ledger.profile.quantity_step)
            self.active_native_submits=0;self.passive_native_submits=0
        def consume(self,frame,env):
            if self.gateway.snapshot_id()!=frame['gateway_state_id']:raise PlanRejected('STALE_NATIVE_OWN_STATE')
            validate_mixed_envelope(frame,env,self.producer.policy_id,self.producer.continuation_id)
            captured=full_input(frame);self.gateway.commit(env.plan,now_ms=frame['t'],market_end_ms=frame['end'])
            self._executed_policy_ids.add(env.plan.policy_id);counts=Counter(a.kind for a in env.plan.actions);self.plan_kinds.update(counts)
            self.multi_new_plans+=counts['NEW']>1;self.keep_count+=counts['KEEP']
            if self.receipt_frame:self.post_receipt_plans+=1;self.receipt_frame=False
            self._refresh_slots(frame['t']);self.book=deepcopy(frame['book'])
            for op in env.operations:
                if op['kind']=='CANCEL':
                    o=self.orders[op['key']];rc=int(self.bt.cancel(0,o['n'],False))
                    if rc!=0:raise RuntimeError('CANCEL_TRANSPORT_UNCERTAIN_KEEP_RESERVED')
                    o['cancelRequested']=True;self.cancel_count+=1
                    try:self.trace.action(dict(kind='CANCEL',t=frame['t'],n=o['n'],key=op['key'],rc=rc))
                    except AttributeError:pass
                    continue
                free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
                if free is None:raise RuntimeError('RESOURCE_CENSOR_UNEXPECTED_ACTUATOR_CAPACITY')
                if op.get('route','PASSIVE')=='PASSIVE':
                    self.send_guard=True
                    try:self.submit(frame['t'],op['side'],op['price'],op['qty'])
                    except Exception:
                        self.gateway.record_send(op['key'],'UNKNOWN',evidence='PASSIVE_NATIVE_SEND_EXCEPTION_KEEP_RESERVED');raise
                    finally:self.send_guard=False
                    self.passive_native_submits+=1
                else:
                    n=self.n;self.n+=1;native_side,native_price=base.ex.native_order(op['side'],op['price'])
                    try:
                        if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.GTC,base.ex.hbt.LIMIT,False))
                        else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.GTC,base.ex.hbt.LIMIT,False))
                    except Exception:
                        self.gateway.record_send(op['key'],'UNKNOWN',evidence='ACTIVE_NATIVE_SEND_EXCEPTION_KEEP_RESERVED');raise
                    if rc!=0:
                        self.gateway.record_send(op['key'],'UNKNOWN',evidence='ACTIVE_NATIVE_NONZERO_KEEP_RESERVED');raise RuntimeError('ACTIVE_NATIVE_SUBMIT_NONZERO:'+str(rc))
                    self.orders[op['key']]=dict(n=n,side=op['side'],price=float(op['price']),qty=float(op['qty']),cum=0.,placed=int(frame['t']),status='NEW')
                    self.placeHist.append((int(frame['t']),op['side'],float(op['qty']),float(op['price'])));self.submits+=1;self.active_native_submits+=1
                    try:self.trace.action(dict(kind='NEW_ACTIVE',t=frame['t'],n=n,key=op['key'],side=op['side'],price=op['price'],qty=op['qty'],rc=rc))
                    except AttributeError:pass
                self.slot_key[int(free)]=op['key'];self.key_role[op['key']]=op['role'];self.role_submits[op['role']]+=1
                self.gateway.record_send(op['key'],'SENT',evidence=('ACTIVE_NATIVE_SUBMIT_RC0' if op.get('route')=='ACTIVE' else 'PASSIVE_NATIVE_SUBMIT_RC0'))
            self.trace.plan(dict(t=frame['t'],input_frame=captured,plan=asdict(env.plan),operations=env.operations,
                own_after_plan=self.gateway.own_state(),policy_provenance=env.provenance,target_expert_policy_label=None,
                policy_supervision_mask=False,event_type='MIXED_ACTIVE_PASSIVE_COMPLETE_PLAN_NOT_TEACHER'))
            self.gateway.events.clear();self.frame_count+=1
    MixedOpenFundingStudent.__name__='MixedOpenFundingStudentV1'
    return MixedOpenFundingStudent
