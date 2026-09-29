"""Research-only policy-neutral world around the accepted native receipt kernel.

Not a trained policy or Target teacher. Historical V1/live sources are unchanged.
Economic preferences belong in the whole-plan producer, not in legality masks.
"""
from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass, asdict, replace, is_dataclass
import math

from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Carrier, nonnegative, EPS
from tools.hft244_pair_route_legality_v1 import crossing_owners
from tools.pair_core_asset_route_sizing_v2 import validate_size
from tools.minimal_student_system_plan_v1 import SystemPlanGateway, SystemPlan, PlanAction, PlanRejected
from tools.minimal_student_native_system_plan_v1 import (
    Envelope, frame_id as legacy_frame_id, digest, make_native_class as make_v1_class,
)


@dataclass(frozen=True)
class WorldProfile:
    profile_id: str = 'MINIMAL_SYSTEM_TRAINING_WORLD_V2'
    asset: str = 'BTC'
    tick: float = .01
    quantity_step: float = .01
    max_live_owners: int = 32
    profile_provenance: str = 'RESEARCH_FIXTURE_NOT_TARGET_OR_LIVE_CERTIFICATION'
    def validate(self):
        if self.asset not in ('BTC','ETH') or not self.profile_id or not self.profile_provenance:
            raise ValueError('explicit world profile required')
        if type(self.max_live_owners) is not int or not 1<=self.max_live_owners<=10000:
            raise ValueError('bounded process-resource ceiling required')
        if any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or x<=0
               for x in (self.tick,self.quantity_step)):
            raise ValueError('declared positive venue grids required')


@dataclass(frozen=True)
class BudgetAllocation:
    parent_id: int
    cash_limit: float
    total_add_authority: float
    evidence: str


@dataclass(frozen=True)
class TrainingPlan(SystemPlan):
    budget_allocations: tuple[BudgetAllocation,...] = ()


class TrainingGrantLedger(EconomicGrantLedger):
    """Inherited receipt/accounting invariants; no hard-coded route4/1 pool."""
    def __init__(self,capital,profile):
        profile.validate();super().__init__(capital);self.profile=profile
    def reserve(self,key,parent_id,route,qty,limit,fee_cap,*,now_ms,market_end_ms):
        if not isinstance(key,str) or not key or key in self.carriers:raise ValueError('carrier identity cannot be rebound')
        if parent_id not in self.grants:raise ValueError('no economic grant')
        if route not in ('PASSIVE','ACTIVE'):raise ValueError('unknown execution route')
        nonnegative(qty,limit,fee_cap,now_ms,market_end_ms)
        if now_ms>=market_end_ms:raise ValueError('market ended')
        if qty<=EPS or not 0<limit<1:raise ValueError('invalid carrier')
        live=[c for c in self.carriers.values() if c.state!='TERMINAL']
        if len(live)>=self.profile.max_live_owners:raise ValueError('RESOURCE_CENSOR_OPEN_OWNER_LIMIT_NOT_HOLD')
        g=self.grants[parent_id];a=self.account(parent_id)
        if qty+a['reserved_qty']>a['repair_remaining']+a['add_remaining']+EPS:
            raise ValueError('shared quantity authority already reserved')
        if qty*limit+fee_cap+a['spent']+a['reserved_cash']>g.cash_limit+EPS:
            raise ValueError('shared cash authority already reserved')
        owners=[dict(key=c.key,side=self.grants[c.parent_id].side,price=c.limit) for c in live]
        if crossing_owners(g.side,limit,owners):raise ValueError('potential own cross including pending')
        self.allocation.register_carrier(key,parent_id,g.repair_qty)
        self.carriers[key]=Carrier(key,parent_id,route,float(qty),float(limit),float(fee_cap))
    def reallocate(self,allocations):
        if len({u.parent_id for u in allocations})!=len(allocations):raise ValueError('duplicate budget allocation')
        proposed=dict(self.grants)
        for u in allocations:
            if not isinstance(u,BudgetAllocation) or type(u.parent_id) is not int or u.parent_id not in self.grants or not u.evidence:
                raise ValueError('explicit existing authority and provenance required')
            nonnegative(u.cash_limit,u.total_add_authority)
            a=self.account(u.parent_id)
            if u.cash_limit+EPS<a['spent']+a['reserved_cash']:
                raise ValueError('cannot transfer spent or pending cash')
            if u.total_add_authority+EPS<a['add_filled']+max(0.,a['reserved_qty']-a['repair_remaining']):
                raise ValueError('cannot revoke acquired or pending quantity authority')
            # Immutable identity/side/generation/repair debt are NOT reset.
            proposed[u.parent_id]=replace(self.grants[u.parent_id],cash_limit=float(u.cash_limit),add_qty=float(u.total_add_authority))
        if sum(g.cash_limit for g in proposed.values())>self.capital+EPS:
            raise ValueError('total capital cannot increase')
        self.grants=proposed;self.invariants()
    def invariants(self):
        super().invariants()
        assert sum(g.cash_limit for g in self.grants.values())<=self.capital+EPS
        assert sum(c.state!='TERMINAL' for c in self.carriers.values())<=self.profile.max_live_owners
        return True


class TrainingPlanGateway(SystemPlanGateway):
    def snapshot_id(self):
        return digest(dict(accounting=super().snapshot_id(),world=asdict(self.ledger.profile)))
    def commit(self,plan,*,now_ms,market_end_ms):
        if not isinstance(plan,SystemPlan) or not plan.decision_id or not plan.continuation_id:
            raise PlanRejected('WHOLE_PLAN_AND_CONTINUATION_REQUIRED')
        if plan.policy_id!=self.policy_id:raise PlanRejected('MIXED_POLICY_FRAGMENT')
        if plan.state_id!=self.snapshot_id():raise PlanRejected('STALE_OWN_STATE_REPLAN_REQUIRED')
        if plan.decision_id in self.committed_ids:raise PlanRejected('DUPLICATE_DECISION_ID')
        if len({a.key for a in plan.actions})!=len(plan.actions):raise PlanRejected('DUPLICATE_ACTION_KEY')
        if any(a.kind=='NEW' and a.route not in self.capabilities for a in plan.actions):
            raise PlanRejected('UNSUPPORTED_COMPLETE_PLAN_NOT_HOLD')
        if type(now_ms) is not int or type(market_end_ms) is not int:raise PlanRejected('EXACT_CLOCK_REQUIRED')
        draft=deepcopy(self.ledger);transport=dict(self.transport);before=self.snapshot_id()
        try:
            draft.reallocate(getattr(plan,'budget_allocations',()))
            for a in plan.actions:
                if a.kind in ('KEEP','CANCEL'):
                    if a.key not in draft.carriers or draft.carriers[a.key].state=='TERMINAL':
                        raise PlanRejected('NO_LIVE_OWNER_FOR_MAINTENANCE')
                    if a.kind=='CANCEL':draft.request_cancel(a.key)
                elif a.kind=='NEW':
                    if a.parent_id not in draft.grants:raise PlanRejected('NO_ECONOMIC_AUTHORITY')
                    validate_size(self.asset,a.route,a.price,a.qty,quantity_step=self.step)
                    if abs(a.price/self.tick-round(a.price/self.tick))>1e-8:raise PlanRejected('PRICE_OFF_DECLARED_GRID')
                    draft.reserve(a.key,a.parent_id,a.route,a.qty,a.price,a.fee_cap,now_ms=now_ms,market_end_ms=market_end_ms)
                    transport[a.key]='RESERVED_NOT_SENT'
                else:raise PlanRejected('UNKNOWN_ACTION')
            draft.invariants()
        except (ValueError,AssertionError,TypeError) as e:
            if isinstance(e,PlanRejected):raise
            raise PlanRejected('JOINT_PLAN_REJECTED: '+str(e)) from e
        self.ledger=draft;self.transport=transport;self.active_continuation=plan.continuation_id
        self.committed_ids.add(plan.decision_id)
        event=dict(event='JOINT_RESERVATION_COMMITTED',decision=asdict(plan),before=before,after=self.snapshot_id(),
                   own=self.own_state(),venue_atomic=False,world_profile=self.ledger.profile.profile_id)
        self.events.append(event);return event


def training_frame_id(frame):
    return digest(dict(base=legacy_frame_id(frame),world=frame['world_profile'],start=frame['start'],window_source=frame.get('market_window_source_sha256')))


def envelope(frame,producer,operations,budget_allocations=()):
    actions=[];specified=set()
    for op in operations:
        if op['kind']=='NEW':
            actions.append(PlanAction('NEW',op['key'],op['parent_id'],op.get('route','PASSIVE'),op['price'],op['qty']))
        else:actions.append(PlanAction('CANCEL',op['key']))
        specified.add(op['key'])
    for key,c in frame['ledger'].carriers.items():
        if c.state!='TERMINAL' and key not in specified:actions.append(PlanAction('KEEP',key))
    plan=TrainingPlan(f'{producer.policy_id}:{frame["index"]}',frame['gateway_state_id'],producer.policy_id,
                      producer.continuation_id,tuple(actions),tuple(budget_allocations))
    return Envelope(training_frame_id(frame),plan,deepcopy(operations),producer.provenance)


def passive_ask(book,side):
    if side=='UP':return min(book['asks']) if book['asks'] else None
    return round(1.-max(book['bids']),10) if book['bids'] else None


def validate_envelope(frame,env,policy_id,continuation_id):
    if not isinstance(env,Envelope) or not isinstance(env.plan,TrainingPlan):raise PlanRejected('TRAINING_WHOLE_PLAN_REQUIRED')
    if env.frame_id!=training_frame_id(frame):raise PlanRejected('STALE_WORLD_MARKET_OR_OWN_FRAME')
    p=env.plan
    if p.policy_id!=policy_id or p.continuation_id!=continuation_id:raise PlanRejected('UNDECLARED_POLICY_OR_CONTINUATION')
    if p.state_id!=frame['gateway_state_id']:raise PlanRejected('STALE_GATEWAY')
    old={k for k,c in frame['ledger'].carriers.items() if c.state!='TERMINAL'}
    if old!={a.key for a in p.actions if a.kind in ('KEEP','CANCEL')}:raise PlanRejected('WHOLE_LIVE_OWNER_SET_MUST_BE_EXPLICIT')
    if len({a.key for a in p.actions})!=len(p.actions):raise PlanRejected('DUPLICATE_ACTION')
    acts={a.key:a for a in p.actions};seen=set();n=frame['own_view']['n']
    for op in env.operations:
        key=op['key']
        if key in seen or key not in acts or op['kind']!=acts[key].kind:raise PlanRejected('OPERATION_ACTION_IDENTITY')
        seen.add(key);a=acts[key]
        if a.kind=='NEW':
            if a.route!='PASSIVE':raise PlanRejected('NATIVE_ACTIVE_UNSUPPORTED_NOT_HOLD')
            if not frame['start']<=frame['t']<frame['end']:raise PlanRejected('MARKET_NOT_OPEN')
            if a.parent_id not in frame['ledger'].grants:raise PlanRejected('NO_GRANT')
            side=frame['ledger'].grants[a.parent_id].side
            if op['side']!=side or a.key!=f'{side}_{n}' or op['qty']!=a.qty or op['price']!=a.price or op['parent_id']!=a.parent_id:
                raise PlanRejected('NEW_INTENT_METADATA_MISMATCH')
            n+=1
            if not op.get('role'):raise PlanRejected('EXPLICIT_ROLE_METADATA_REQUIRED')
            ask=passive_ask(frame['book'],side)
            if ask is None:raise PlanRejected('BOOK_SUPPORT_UNKNOWN_NOT_HOLD')
            if not isinstance(a.price,(int,float)) or isinstance(a.price,bool) or not math.isfinite(a.price):raise PlanRejected('INVALID_PRICE')
            if a.price>=ask-1e-10:raise PlanRejected('PASSIVE_POSTONLY_MARKETABLE_REQUEST')
        elif a.kind=='CANCEL':
            if not frame['cancellable'].get(key,False):raise PlanRejected('CANCEL_NOT_CURRENTLY_CANCELLABLE')
            if op.get('origin')!='WHOLE_POLICY':raise PlanRejected('HIDDEN_LEGACY_CANCEL_RULE_REJECTED')
        else:raise PlanRejected('NO_PHYSICAL_KEEP_OPERATION')
    if seen!={a.key for a in p.actions if a.kind!='KEEP'}:raise PlanRejected('UNREPRESENTED_PHYSICAL_OPERATION')
    if len(old)+sum(a.kind=='NEW' for a in p.actions)>frame['world_profile']['max_live_owners']:
        raise PlanRejected('RESOURCE_CENSOR_OPEN_OWNER_LIMIT_NOT_HOLD')
    return True


def serializable(x):
    if is_dataclass(x):return serializable(asdict(x))
    if isinstance(x,dict):return {str(k):serializable(v) for k,v in x.items()}
    if isinstance(x,(tuple,list,deque,set)):return [serializable(v) for v in x]
    return x


def full_input(frame):
    ledger=frame['ledger']
    return serializable(dict(t=frame['t'],start=frame['start'],end=frame['end'],index=frame['index'],
        world_profile=frame['world_profile'],market_window_source_sha256=frame.get('market_window_source_sha256'),book=frame['book'],quotes=frame['quotes'],
        own_view=frame['own_view'],snapshots=frame['snapshots'],cancellable=frame['cancellable'],
        authority=dict(capital=ledger.capital,grants=ledger.grants,carriers=ledger.carriers,
            accounts={k:ledger.account(k) for k in ledger.grants},allocation_parents=ledger.allocation.parents),
        gateway_state_id=frame['gateway_state_id'],frame_id=training_frame_id(frame)))


def verified_market_window(market, window, source_sha256):
    if (not isinstance(window,(tuple,list)) or len(window)!=2
            or any(type(x) is not int for x in window) or not 0<=window[0]<window[1]
            or not isinstance(source_sha256,str) or len(source_sha256)!=64):
        raise ValueError('verified market interval and source fingerprint required')
    if int(market['window_end_ms'])!=window[1]:
        raise ValueError('native tape end differs from verified market interval')
    return window[0],window[1]


def make_training_class(frozen_minimal_class,exact_class):
    V1=make_v1_class(frozen_minimal_class,exact_class)
    class TrainingWholePlanStudent(V1):
        def __init__(self,*a,producer,ledger,trace,verified_window,window_source_sha256,**kw):
            if not isinstance(ledger,TrainingGrantLedger):raise ValueError('explicit V2 world ledger required')
            super().__init__(*a,producer=producer,ledger=ledger,trace=trace,**kw)
            self.verified_market_start,self.verified_market_end=verified_market_window(self.payload['market'],verified_window,window_source_sha256)
            self.window_source_sha256=window_source_sha256
            self.max_slots=ledger.profile.max_live_owners;self.serialize_same_side=False
            self.gateway=TrainingPlanGateway(ledger,asset=ledger.profile.asset,policy_id=producer.policy_id,
                capabilities=('PASSIVE',),tick=ledger.profile.tick,quantity_step=ledger.profile.quantity_step)
            self.complete_input_frames=0;self.late_new_orders=0
        def current_frame(self,t,end,book,quotes,index):
            f=super().current_frame(t,end,book,quotes,index)
            assert int(end)==self.verified_market_end
            f['start']=self.verified_market_start;f['market_window_source_sha256']=self.window_source_sha256;f['world_profile']=asdict(self.gateway.ledger.profile)
            return f
        def consume(self,frame,env):
            if self.gateway.snapshot_id()!=frame['gateway_state_id']:raise PlanRejected('STALE_NATIVE_OWN_STATE')
            validate_envelope(frame,env,self.producer.policy_id,self.producer.continuation_id)
            captured=full_input(frame)
            self.gateway.commit(env.plan,now_ms=frame['t'],market_end_ms=frame['end'])
            self._executed_policy_ids.add(env.plan.policy_id)
            counts=Counter(a.kind for a in env.plan.actions);self.plan_kinds.update(counts)
            self.multi_new_plans+=counts['NEW']>1;self.keep_count+=counts['KEEP']
            if self.receipt_frame:self.post_receipt_plans+=1;self.receipt_frame=False
            self._refresh_slots(frame['t']);self.book=deepcopy(frame['book'])
            for op in env.operations:
                if op['kind']=='CANCEL':
                    o=self.orders[op['key']];rc=self.bt.cancel(0,o['n'],False)
                    if int(rc)!=0:raise RuntimeError('CANCEL_TRANSPORT_UNCERTAIN_KEEP_RESERVED')
                    o['cancelRequested']=True;self.cancel_count+=1
                else:
                    # No role/Pair/TTL selector in the native actuator.
                    free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
                    if free is None:raise RuntimeError('RESOURCE_CENSOR_UNEXPECTED_ACTUATOR_CAPACITY')
                    self.send_guard=True
                    try:self.submit(frame['t'],op['side'],op['price'],op['qty'])
                    except Exception:
                        self.gateway.record_send(op['key'],'UNKNOWN',evidence='NATIVE_SEND_EXCEPTION_KEEP_RESERVED');raise
                    finally:self.send_guard=False
                    self.slot_key[free]=op['key'];self.key_role[op['key']]=op['role'];self.role_submits[op['role']]+=1
                    self.gateway.record_send(op['key'],'SENT',evidence='NATIVE_RC0')
                    if frame['end']-frame['t']<=180000:self.late_new_orders+=1
            self.trace.plan(dict(t=frame['t'],input_frame=captured,plan=asdict(env.plan),operations=env.operations,
                own_after_plan=self.gateway.own_state(),policy_provenance=env.provenance,
                target_expert_policy_label=None,policy_supervision_mask=False,
                event_type='COMPLETE_INPUT_AND_PLAN_NOT_TEACHER'))
            self.complete_input_frames+=1;self.gateway.events.clear();self.frame_count+=1
    TrainingWholePlanStudent.__name__='TrainingWholePlanStudentV2'
    return TrainingWholePlanStudent


class RemovedRuleWitness:
    """Regression witness ONLY. Boundary times/prices below are test cases, not a strategy."""
    policy_id='REMOVED_RULES_NATIVE_WITNESS_V2_NOT_TEACHER'
    continuation_id='REMOVED_RULES_NATIVE_WITNESS_V2_ALL_STEPS'
    provenance='EXPLICIT_BOUNDARY_TEST_NEVER_TARGET_OR_POLICY_TRAINING_LABEL'
    def __init__(self):
        self.calls=0;self.stage='WAIT_FOR_OLD_BOUNDARY';self.sent_at=None;self.keys=[]
        self.kept_beyond_old_ttl=False;self.non_displayed_prices=0;self.explicit_budget_updates=0
    def produce(self,f):
        self.calls+=1;ops=[];alloc=[];live={k:c for k,c in f['ledger'].carriers.items() if c.state!='TERMINAL'}
        if self.stage=='WAIT_FOR_OLD_BOUNDARY' and 0<f['end']-f['t']<=180000:
            choices=[(passive_ask(f['book'],s),s) for s in ('UP','DOWN')]
            choices=[(p,s) for p,s in choices if p is not None and p>.10+1e-9]
            if choices:
                _,side=max(choices);pid=1 if side=='UP' else 2;other=3-pid
                for i,p in enumerate((.06,.07,.08,.09,.10)):
                    key=f"{side}_{f['own_view']['n']+i}";self.keys.append(key)
                    ops.append(dict(kind='NEW',key=key,parent_id=pid,side=side,price=p,qty=18.,role='TEST_FIVE_LATE_OWNERS'))
                    native_price=p if side=='UP' else round(1.-p,10)
                    displayed=f['book']['bids'] if side=='UP' else f['book']['asks']
                    self.non_displayed_prices+=native_price not in displayed
                alloc=[BudgetAllocation(pid,65.,110.,'EXPLICIT_TEST_UNCOMMITTED_TRANSFER'),
                       BudgetAllocation(other,35.,110.,'EXPLICIT_TEST_UNCOMMITTED_TRANSFER')]
                self.explicit_budget_updates+=1;self.sent_at=f['t'];self.stage='KEEP_PAST_REMOVED_TTL'
        elif self.stage=='KEEP_PAST_REMOVED_TTL' and f['t']-self.sent_at>5000:
            self.kept_beyond_old_ttl=bool(live);self.stage='EXPLICIT_CANCEL_NEXT_OBSERVATION'
        elif self.stage=='EXPLICIT_CANCEL_NEXT_OBSERVATION':
            for k,c in live.items():
                if c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):
                    ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='TEST_FINISHED_EXPLICIT_CANCEL'))
            if not live:self.stage='DONE'
        return envelope(f,self,ops,alloc)
