"""Research native whole-plan bridge. No teacher, optimizer or execution shortcuts.

Legacy policy is one explicit detached producer. The native consumer has no
role/price/TTL selection fallback; alternate producers receive the same causal
frame and must return complete ownership maintenance plus new actions.
"""
from collections import Counter, defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, asdict
import hashlib
import json
from types import SimpleNamespace

from tools.minimal_student_system_plan_v1 import SystemPlanGateway, PlanAction, SystemPlan, PlanRejected
from tools.minimal_student_quantity_seam_v1 import (
    make_student_class, QuantityIntent, VenueGrid, raw_passive_levels, prepare_exact, TERMINAL,
)


def digest(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


@dataclass
class Envelope:
    frame_id: str
    plan: SystemPlan
    operations: list
    provenance: str


# Native pointers, future tape, Target state and replay metadata are not included.
COPY_FIELDS=('inv','cost','un','orders','n','slot_key','key_role','placeHist','submits',
    'max_slots','serialize_same_side','veto','slot_history','role_submits',
    'role_budget_blocks','marginal_pair_credit','marginal_pair_risk_spend',
    'prebase_core_submits','prebase_same_side_satellite_submits','serialization_blocks',
    'cancel_requests','role_cancel_requests','reanchors','core_preserved_clocks',
    'minimal_pair_checks','minimal_pair_blocks','one_new_per_receipt_blocks','_last_new_receipt')


def frame_id(frame):
    v=frame['own_view']
    return digest(dict(t=frame['t'],end=frame['end'],index=frame['index'],
        current_book=frame['book'],previous_book=frame['previous_book'],
        inventory=v['inv'],cost=v['cost'],unpaired={s:list(v['un'][s]) for s in ('UP','DOWN')},
        orders=v['orders'],slots=v['slot_key'],roles=v['key_role'],next_id=v['n'],
        snapshots=frame['snapshots'],cancellable=frame['cancellable'],
        gateway_snapshot=frame['gateway_state_id']))


def complete_envelope(frame,producer,operations):
    actions=[];specified=set()
    for op in operations:
        if op['kind']=='NEW':
            actions.append(PlanAction('NEW',op['key'],op['parent_id'],'PASSIVE',op['price'],op['qty']))
        else:actions.append(PlanAction('CANCEL',op['key']))
        specified.add(op['key'])
    for key,c in frame['ledger'].carriers.items():
        if c.state!='TERMINAL' and key not in specified:actions.append(PlanAction('KEEP',key))
    p=SystemPlan(f"{producer.policy_id}:{frame['index']}",frame['gateway_state_id'],
                 producer.policy_id,producer.continuation_id,tuple(actions))
    return Envelope(frame_id(frame),p,deepcopy(operations),producer.provenance)


def check_envelope(frame,envelope,policy_id,continuation_id):
    if not isinstance(envelope,Envelope):raise PlanRejected('COMPLETE_ENVELOPE_REQUIRED')
    if envelope.frame_id!=frame_id(frame):raise PlanRejected('STALE_MARKET_FRAME')
    p=envelope.plan
    if p.policy_id!=policy_id or p.continuation_id!=continuation_id:
        raise PlanRejected('UNDECLARED_POLICY_OR_CONTINUATION')
    if p.state_id!=frame['gateway_state_id']:raise PlanRejected('STALE_GATEWAY')
    old={k for k,c in frame['ledger'].carriers.items() if c.state!='TERMINAL'}
    maintenance={a.key for a in p.actions if a.kind in ('KEEP','CANCEL')}
    if old!=maintenance:raise PlanRejected('WHOLE_LIVE_OWNER_SET_MUST_BE_EXPLICIT')
    if len({a.key for a in p.actions})!=len(p.actions):raise PlanRejected('DUPLICATE_ACTION')
    acts={a.key:a for a in p.actions};seen=set();new_n=frame['own_view']['n']
    for op in envelope.operations:
        key=op['key']
        if key in seen or key not in acts:raise PlanRejected('OPERATION_ACTION_IDENTITY')
        seen.add(key);a=acts[key]
        if op['kind']!=a.kind:raise PlanRejected('OPERATION_KIND_MISMATCH')
        if a.kind=='NEW':
            if a.route!='PASSIVE':raise PlanRejected('NATIVE_ACTIVE_UNSUPPORTED_NOT_HOLD')
            if a.parent_id not in frame['ledger'].grants:raise PlanRejected('NO_GRANT')
            side=frame['ledger'].grants[a.parent_id].side
            if (op['side']!=side or a.key!=f'{side}_{new_n}' or op['qty']!=a.qty
                or op['price']!=a.price or op['parent_id']!=a.parent_id):
                raise PlanRejected('NEW_INTENT_METADATA_MISMATCH')
            new_n+=1
            if frame['end']-frame['t']<=180000:raise PlanRejected('LEGACY_ALL_NEW_180S_FENCE')
            if a.price not in raw_passive_levels(frame['book'],side):raise PlanRejected('PRICE_NOT_IN_VISIBLE_BOOK')
            opposite='DOWN' if side=='UP' else 'UP';lots=frame['own_view']['un'][opposite]
            total=sum(q for q,_ in lots)
            if total>1e-9 and sum(q*p for q,p in lots)/total+a.price>1.0000001:
                raise PlanRejected('PRESERVED_MINIMAL_PAIR_GATE')
            if not op.get('role'):raise PlanRejected('EXPLICIT_ROLE_METADATA_REQUIRED')
        elif a.kind=='CANCEL':
            if not frame['cancellable'].get(key,False):raise PlanRejected('CANCEL_NOT_CURRENTLY_CANCELLABLE')
            if op.get('origin') not in ('TTL','REANCHOR','WHOLE_POLICY'):
                raise PlanRejected('UNKNOWN_CANCEL_ORIGIN')
        else:raise PlanRejected('NO_PHYSICAL_KEEP_OPERATION')
    if seen!={a.key for a in p.actions if a.kind!='KEEP'}:raise PlanRejected('UNREPRESENTED_PHYSICAL_OPERATION')
    if len(old)+sum(a.kind=='NEW' for a in p.actions)>frame['own_view']['max_slots']:
        raise PlanRejected('SLOT_CAP_PENDING_CANCEL_STILL_COUNTS')
    return True


class LegacyWholePlanProducer:
    policy_id='FROZEN_MINIMAL_COMPLETE_PLAN_SHAM_V1'
    continuation_id='FROZEN_MINIMAL_COMPLETE_PLAN_SHAM_V1:ALL_STEPS'
    provenance='DETACHED_FROZEN_POLICY_NO_NATIVE_OR_FUTURE_ACCESS'
    def __init__(self,exact_class,qty):self.exact_class=exact_class;self.qty=float(qty);self.calls=0
    def produce(self,frame):
        self.calls+=1;ops=[];outer=self
        class Detached(self.exact_class):
            def submit(v,t,side,p,q):
                n=v.n;key=f'{side}_{n}';intent=v.quantity_ready.intent
                ops.append(dict(kind='NEW',key=key,parent_id=intent.parent_id,side=side,
                                price=float(p),qty=float(q),role=v.quantity_context['role']))
                v.n+=1;v.orders[key]=dict(n=n,side=side,price=float(p),qty=float(q),cum=0.,placed=t,status='NEW')
                v.placeHist.append((t,side,q,p));v.submits+=1
            def snap(v,o):return deepcopy(frame['snapshots'].get(f"{o['side']}_{o['n']}",{}))
            def _request_cancel(v,t,sid,reason):
                v.bt.origin=('REANCHOR',reason,int(sid))
                try:return super()._request_cancel(t,sid,reason)
                finally:v.bt.origin=('TTL','TTL',None)
        v=object.__new__(Detached)
        v.__dict__.update(deepcopy(frame['own_view']))
        v.quantity_ledger=deepcopy(frame['ledger']);v.quantity_asset='BTC'
        v.quantity_grid=VenueGrid(.01,.01,.01,0.,'FROZEN_RESEARCH_GRID')
        v.quantity_provider=lambda ctx:QuantityIntent(1 if ctx['side']=='UP' else 2,outer.qty,'PASSIVE','PINNED_SHAM_CASE')
        v.quantity_audit_sink=None;v.quantity_event_count=0;v.quantity_context=None;v.quantity_ready=None
        v.quantity_rejections={};v.quantity_payments={};v.quantity_seen_receipts=set()
        class FakeNative:
            def __init__(b):
                b.origin=('TTL','TTL',None)
                b.rows={o['n']:SimpleNamespace(cancellable=bool(frame['cancellable'].get(k))) for k,o in v.orders.items()}
            def orders(b,_):return b.rows
            def cancel(b,asset,n,wait):
                key=next(k for k,o in v.orders.items() if o['n']==n)
                origin,reason,sid=b.origin
                ops.append(dict(kind='CANCEL',key=key,origin=origin,reason=reason,slot=sid))
                b.rows[n].cancellable=False
                return 0
        v.bt=FakeNative();v.book=deepcopy(frame['previous_book'])
        v.cancel_expired(frame['t']);v._refresh_slots(frame['t']);v.book=deepcopy(frame['book'])
        if frame['quotes']:
            v._risk_contract_if_needed(frame['t']);v._reanchor_stale(frame['t'])
            v._open_one_option(frame['t'],frame['quotes'],frame['end'])
        return complete_envelope(frame,self,ops)


class CausalWholePlanProbe:
    policy_id='CAUSAL_BILATERAL_PLAN_AUTHORITY_PROBE_V1'
    continuation_id='CAUSAL_BILATERAL_PLAN_AUTHORITY_PROBE_V1:ALL_STEPS'
    provenance='HAND_CODED_CONTROL_AUTHORITY_TEST_NOT_TARGET_OR_TRAINED_POLICY'
    def __init__(self,qty):self.qty=float(qty);self.calls=0
    def produce(self,frame):
        self.calls+=1;v=frame['own_view'];ledger=deepcopy(frame['ledger']);ops=[]
        inv=v['inv'];live={k:c for k,c in ledger.carriers.items() if c.state!='TERMINAL'}
        for key,c in live.items():
            side=ledger.grants[c.parent_id].side;opp='DOWN' if side=='UP' else 'UP'
            invalid=c.limit not in raw_passive_levels(frame['book'],side)
            reduce_same_side=inv[side]>0 and inv[opp]<=1e-9
            if (invalid or reduce_same_side) and frame['cancellable'].get(key,False):
                sid=next((sid for sid,k in v['slot_key'].items() if k==key),None)
                ops.append(dict(kind='CANCEL',key=key,origin='WHOLE_POLICY',reason='OWN_FILL_OR_BOOK_REPLAN',slot=sid))
        # One policy owns initialization and all following decisions. This is a
        # deterministic transport probe, not a recommended trading algorithm.
        if frame['quotes'] and frame['end']-frame['t']>180000:
            if max(inv.values())<=1e-9:
                wanted=['UP','DOWN'] if not live else []
            elif min(inv.values())<=1e-9:
                wanted=['UP' if inv['UP']<=1e-9 else 'DOWN']
            else:wanted=[]
            n=v['n'];new_ops=[]
            for side in wanted:
                if any(ledger.grants[c.parent_id].side==side for c in live.values()):continue
                pid=1 if side=='UP' else 2;opposite='DOWN' if side=='UP' else 'UP'
                lots=v['un'][opposite];total=sum(q for q,_ in lots)
                for p in raw_passive_levels(frame['book'],side):
                    if total>1e-9 and sum(q*price for q,price in lots)/total+p>1.0000001:continue
                    key=f'{side}_{n}'
                    rr=prepare_exact(ledger,'BTC',side,p,QuantityIntent(pid,self.qty,'PASSIVE','JOINT_PROBE'),
                        key=key,quote_reference='CURRENT_PROBE_FRAME',now_ms=frame['t'],market_end_ms=frame['end'],
                        grid=VenueGrid(.01,.01,.01,0.,'FROZEN_RESEARCH_GRID'))
                    if rr.plan is None:continue
                    ledger.reserve(key,pid,'PASSIVE',self.qty,p,0.,now_ms=frame['t'],market_end_ms=frame['end'])
                    new_ops.append(dict(kind='NEW',key=key,parent_id=pid,side=side,price=p,qty=self.qty,
                        role='WHOLE_BILATERAL_OPEN' if total<=1e-9 else 'WHOLE_OPPOSITE_CONTINUATION'))
                    n+=1;break
            # During empty initialization, require the complete bilateral pair.
            if max(inv.values())<=1e-9 and len(new_ops)!=2:new_ops=[]
            ops.extend(new_ops)
        return complete_envelope(frame,self,ops)


def make_native_class(frozen_minimal_class,exact_class):
    class NativeWholePlanStudent(frozen_minimal_class):
        def __init__(self,*args,producer,ledger,trace,**kwargs):
            super().__init__(*args,**kwargs)
            self.producer=producer;self.trace=trace
            self.gateway=SystemPlanGateway(ledger,asset='BTC',policy_id=producer.policy_id,capabilities=('PASSIVE',))
            self.frame_count=0;self.multi_new_plans=0;self.post_receipt_plans=0;self.keep_count=0
            self.plan_kinds=Counter();self.cancel_count=0;self.send_guard=False;self.payments={}
            self.receipt_frame=False;self._executed_policy_ids=set();self._last_plan_frame=None
        def submit(self,t,side,p,q):
            if not self.send_guard:raise RuntimeError('NATIVE_SUBMIT_OUTSIDE_WHOLE_PLAN')
            n=self.n;super().submit(t,side,p,q)
            self.trace.action(dict(kind='NEW',t=t,n=n,side=side,price=float(p),qty=float(q)))
        def _hidden(self,*a,**kw):raise RuntimeError('HIDDEN_LEGACY_DECISION_IN_NATIVE_CONSUMER')
        _role_decision=_hidden
        _candidate_from_levels=_hidden
        _risk_contract_if_needed=_hidden
        _reanchor_stale=_hidden
        _open_one_option=_hidden
        cancel_expired=_hidden
        def _live_price_levels(self,side):return raw_passive_levels(self.book,side)
        def process(self,t):
            self.trace.now=int(t);frozen_minimal_class.process(self,t)
            for r in self._receipt_delta_rows:
                key=r['key'];p,f=self.payments.get(key,(0.,0.))
                p+=r['qty']*r['contractPrice'];f+=r['fee'];self.payments[key]=(p,f)
                self.gateway.receipt(key,filled=r['cumulative_qty'],payment=p,fees=f,
                                     evidence=f"NATIVE_RECEIPT:{r['sequence']}")
                self.receipt_frame=True
            for key,c in self.gateway.ledger.carriers.items():
                if c.state=='TERMINAL':continue
                o=self.orders.get(key)
                if o is None:raise RuntimeError('RESERVED_OWNER_WITHOUT_NATIVE_ID')
                s=self.snap(o);status=str(s.get('status') or '').upper()
                if status in TERMINAL:
                    if abs(float(s.get('cumExecQty') or 0.)-c.filled)>1e-8:raise RuntimeError('TERMINAL_RECEIPTS_MISSING')
                    self.gateway.receipt(key,filled=c.filled,payment=c.payment,fees=c.fees,terminal=True,
                                         evidence='NATIVE_TERMINAL:'+status)
            own=self.gateway.own_state()
            assert all(abs(own['inventory'][s]-self.inv[s])<1e-8 for s in ('UP','DOWN'))
            assert abs(own['cost']-self.cost)<1e-8
            self.gateway.events.clear();self.trace.process(self,t)
        def current_frame(self,t,end,book,quotes,index):
            view={key:deepcopy(getattr(self,key)) for key in COPY_FIELDS}
            snaps={k:self.snap(o) for k,o in self.orders.items()};can={}
            for key,o in self.orders.items():
                cur=self.bt.orders(0).get(o['n']);can[key]=cur is not None and bool(cur.cancellable)
            return dict(t=int(t),end=int(end),index=index,previous_book=deepcopy(self.book),book=deepcopy(book),
                quotes=deepcopy(quotes),own_view=view,snapshots=snaps,cancellable=can,
                ledger=deepcopy(self.gateway.ledger),gateway_state_id=self.gateway.snapshot_id())
        def consume(self,frame,envelope):
            # Recompute current own/market evidence before publishing any reservation.
            if self.gateway.snapshot_id()!=frame['gateway_state_id']:raise PlanRejected('STALE_NATIVE_OWN_STATE')
            check_envelope(frame,envelope,self.producer.policy_id,self.producer.continuation_id)
            self.gateway.commit(envelope.plan,now_ms=frame['t'],market_end_ms=frame['end'])
            self._executed_policy_ids.add(envelope.plan.policy_id)
            counts=Counter(a.kind for a in envelope.plan.actions);self.plan_kinds.update(counts)
            self.multi_new_plans+=counts['NEW']>1;self.keep_count+=counts['KEEP']
            if self.receipt_frame:self.post_receipt_plans+=1;self.receipt_frame=False
            ops=envelope.operations
            # Preserve phase order: TTL transport before slot refresh/current-book maintenance.
            for op in ops:
                if op['kind']=='CANCEL' and op['origin']=='TTL':
                    o=self.orders[op['key']];rc=self.bt.cancel(0,o['n'],False)
                    if int(rc)!=0:raise RuntimeError('TTL_CANCEL_TRANSPORT_NONZERO')
                    self.cancel_count+=1
            self._refresh_slots(frame['t']);self.book=deepcopy(frame['book'])
            for op in ops:
                if op['kind']=='CANCEL' and op['origin']!='TTL':
                    sid=op['slot']
                    if sid is None:raise RuntimeError('CANCEL_SLOT_MISSING')
                    ok=frozen_minimal_class._request_cancel(self,frame['t'],sid,op['reason'])
                    if not ok:raise RuntimeError('CANCEL_TRANSPORT_FAILED_RESERVATION_RETAINED')
                    self.cancel_count+=1
                elif op['kind']=='NEW':
                    self.send_guard=True
                    try:
                        ok=frozen_minimal_class._submit_role(self,frame['t'],op['side'],op['role'],op['price'],op['qty'],None,'COMPLETE_PLAN_CONSUMER')
                    except Exception:
                        self.gateway.record_send(op['key'],'UNKNOWN',evidence='NATIVE_SEND_EXCEPTION_KEEP_RESERVED');raise
                    finally:self.send_guard=False
                    if not ok:
                        self.gateway.record_send(op['key'],'NOT_SENT',evidence='ACTUATOR_DECLINED_BEFORE_NATIVE');raise RuntimeError('WHOLE_PLAN_ACTUATOR_DECLINED')
                    self.gateway.record_send(op['key'],'SENT',evidence='NATIVE_SUBMIT_RC0')
            self.trace.plan(dict(t=frame['t'],frame_id=envelope.frame_id,plan=asdict(envelope.plan),
                operations=ops,own=self.gateway.own_state(),provenance=envelope.provenance))
            self.gateway.events.clear();self.frame_count+=1
        def run_whole(self,base):
            from eof_runtime import require_advance
            updates=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
            require_advance(base.ex,self.bt,int(self.meta['firstReceivedMs']),'initial',None)
            end=int(self.payload['market']['window_end_ms'])
            for i,u in enumerate(updates):
                t=int(u[1]);require_advance(base.ex,self.bt,t,'source',i);self.process(t)
                book=deepcopy(self.book);base.apply(book,u);qv=base.quotes(book)
                frame=self.current_frame(t,end,book,qv,i)
                env=self.producer.produce(deepcopy(frame))
                self.consume(frame,env)
                if qv:self._sample_occupancy()
            end2=int(self.meta['lastReceivedMs']);require_advance(base.ex,self.bt,end2,'end2',None);self.process(end2)
            self._refresh_slots(end2);self._sample_occupancy()
            assert self.producer.calls==len(updates)==self.frame_count
            assert self._executed_policy_ids=={self.producer.policy_id}
            self.gateway.ledger.invariants()
    NativeWholePlanStudent.__name__='NativeWholePlanStudentV1'
    return NativeWholePlanStudent
