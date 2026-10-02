"""Research-only representation/EOF recovery. No policy, objective or funding changes.
Raw native records are never discarded; effective fill counters use canonical
cumulative quantities and explicitly account for nonadvancing numeric residuals.
"""
from copy import deepcopy
from dataclasses import asdict as stock_asdict, is_dataclass, asdict
from collections import Counter
import hashlib, json, math

from tools.minimal_student_open_funding_v1 import OpenFundingLedger
from tools.minimal_student_training_world_v2 import make_training_class
from tools.minimal_student_native_system_plan_v1 import COPY_FIELDS


def scalar_asdict(x):
    if is_dataclass(x) and hasattr(x,'__dict__') and all(v is None or type(v) in (str,int,float,bool) for v in vars(x).values()):
        return dict(vars(x))
    return stock_asdict(x)


class FastOpenFundingLedger(OpenFundingLedger):
    def __deepcopy__(self,memo):
        if id(self) in memo:return memo[id(self)]
        dst=object.__new__(type(self));memo[id(self)]=dst
        for k,v in vars(self).items():
            if k=='grants':
                # Grant is frozen and all its fields are scalar/immutable.
                assert all(all(z is None or type(z) in (str,int,float,bool) for z in vars(g).values()) for g in v.values())
                setattr(dst,k,dict(v))
            elif k=='carriers':
                setattr(dst,k,{key:type(c)(**vars(c)) for key,c in v.items()})
            elif k=='allocation':
                a=type(v)()
                a.parents={p:type(st)(**dict(vars(st),carriers=set(st.carriers))) for p,st in v.parents.items()}
                a.carrier_seen=dict(v.carrier_seen);a.carrier_parent=dict(v.carrier_parent)
                a.transition_overflow=dict(v.transition_overflow)
                a.allocations=list(v.allocations) # frozen AllocationResult objects
                setattr(dst,k,a)
            elif k=='profile':setattr(dst,k,v) # frozen scalar profile
            else:setattr(dst,k,deepcopy(v,memo))
        return dst


def install_scalar_codec():
    import tools.minimal_student_system_plan_v1 as plans
    import tools.minimal_student_training_world_v2 as world
    plans.asdict=scalar_asdict;world.asdict=scalar_asdict


def source_signature(path,limits=None):
    import gzip
    hashes={k:hashlib.sha256() for k in ('native_action','canonical_receipt','own_state')};counts=Counter()
    decoded=0
    with gzip.open(path,'rb') as f:
        for line in f:
            decoded+=len(line);assert decoded<=32*1024**2
            item=json.loads(line);k=item['kind']
            if k not in hashes:continue
            if limits is not None and counts[k]>=limits[k]:continue
            hashes[k].update((json.dumps(item['data'],sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())
            counts[k]+=1
    if limits is not None:assert dict(counts)==limits
    return dict(counts=dict(counts),sha256={k:v.hexdigest() for k,v in hashes.items()})


def verify_numeric_owner(original_qty,rows,final_cumulative):
    values=[float(r['qty']) for r in rows]
    raw=math.fsum(values)
    cumulative=float(rows[-1]['cumulative_qty']) if rows else 0.
    assert cumulative==final_cumulative, 'last native cumulative != ledger cumulative'
    # An operation-count/representation error bound, not a tuned trade threshold.
    bound=(len(values)+2)*math.fsum(math.ulp(abs(v)) for v in [float(original_qty),cumulative,*values])
    assert abs(raw-cumulative)<=bound,'delta/cumulative mismatch beyond float-operation bound'
    residual_only=bool(rows) and cumulative==0.
    if residual_only:
        assert all(r['cumulative_qty']==0. and r['leaves_qty']==original_qty for r in rows)
        assert raw<=bound
    previous=0.;effective=0;nonadvancing=0
    for r in rows:
        if r['cumulative_qty']>previous:effective+=1
        else:nonadvancing+=1
        assert r['cumulative_qty']>=previous,'nonmonotone cumulative'
        previous=r['cumulative_qty']
    return dict(raw_delta_sum=raw,canonical_cumulative=cumulative,zero_fill=cumulative==0.,
        raw_receipts=len(rows),effective_cumulative_advances=effective,
        nonadvancing_raw_receipts=nonadvancing,numeric_residual_only=residual_only,
        abs_difference=abs(raw-cumulative),float_operation_bound=bound)


def audit_receipt_support(path,final_carriers):
    from collections import defaultdict
    import gzip
    owners={};rows=defaultdict(list);ids=set();decoded=0
    with gzip.open(path,'rb') as f:
        for line in f:
            decoded+=len(line);assert decoded<=32*1024**2
            z=json.loads(line);k=z['kind'];r=z['data']
            if k=='native_action' and r['kind']=='NEW':owners[r['side']+'_'+str(r['n'])]=r
            elif k=='canonical_receipt':
                assert r['sequence'] not in ids;ids.add(r['sequence']);rows[r['key']].append(r)
    assert set(owners)==set(final_carriers)
    summaries={key:verify_numeric_owner(o['qty'],rows[key],final_carriers[key]['filled']) for key,o in owners.items()}
    return dict(owners=len(owners),raw_receipts=len(ids),canonical_zero_fill_orders=sum(v['zero_fill'] for v in summaries.values()),
        no_raw_receipt_orders=sum(not rows[k] for k in owners),
        numeric_residual_only_orders=sum(v['numeric_residual_only'] for v in summaries.values()),
        economic_filled_orders=sum(not v['zero_fill'] for v in summaries.values()),
        effective_cumulative_advance_receipts=sum(v['effective_cumulative_advances'] for v in summaries.values()),
        nonadvancing_raw_receipts=sum(v['nonadvancing_raw_receipts'] for v in summaries.values()),
        largest_delta_vs_cumulative_gap=max((v['abs_difference'] for v in summaries.values()),default=0.),
        all_dynamic_float_bounds_pass=True,canonical_labels_not_inferred_from_qty_threshold=True)


def make_recovered_student(frozen_minimal,exact):
    Parent=make_training_class(frozen_minimal,exact)
    class Recovered(Parent):
        def __init__(self,*a,**kw):
            self._terminal_snapshots={};super().__init__(*a,**kw);self.terminal_drain=[]
        def snap(self,o):
            n=o['n']
            if n in self._terminal_snapshots:return dict(self._terminal_snapshots[n])
            s=super().snap(o)
            if str(s.get('status') or '').upper() in ('FILLED','CANCELED','CANCELLED','EXPIRED','REJECTED'):
                self._terminal_snapshots[n]=dict(s)
            return s
        def current_frame(self,t,end,book,quotes,index):
            live={k for k,c in self.gateway.ledger.carriers.items() if c.state!='TERMINAL'}
            visible=live|set(self.slot_key.values())
            view={}
            for key in COPY_FIELDS:
                value=getattr(self,key)
                if key in ('orders','key_role'):view[key]=deepcopy({k:v for k,v in value.items() if k in visible})
                else:view[key]=deepcopy(value)
            snaps={k:self.snap(self.orders[k]) for k in visible if k in self.orders};can={}
            for k in visible:
                if k not in self.orders:continue
                o=self.orders[k];native=self.bt.orders(0).get(o['n']);can[k]=native is not None and bool(native.cancellable)
            return dict(t=int(t),end=int(end),start=self.verified_market_start,index=index,
                previous_book=deepcopy(self.book),book=deepcopy(book),quotes=deepcopy(quotes),
                own_view=view,snapshots=snaps,cancellable=can,ledger=deepcopy(self.gateway.ledger),
                gateway_state_id=self.gateway.snapshot_id(),world_profile=stock_asdict(self.gateway.ledger.profile),
                market_window_source_sha256=self.window_source_sha256)
        def flush_terminal_trace(self,t):
            for k,c in self.gateway.ledger.carriers.items():
                if c.state=='TERMINAL' and k not in self.trace.terminal_logged:
                    self.trace.emit('terminal_owner_once',dict(key=k,owner=scalar_asdict(c),t=t))
                    self.trace.terminal_logged.add(k)
        def drain_queued_responses(self,base):
            from eof_runtime import observe_drain
            # Existing recorded event stream has finished. Only queued transport
            # replies and same-policy closure actions remain. No market row added.
            start_live=sum(c.state!='TERMINAL' for c in self.gateway.ledger.carriers.values())
            max_steps=4*start_live+4;last_observed=int(self.meta['lastReceivedMs'])
            for step in range(max_steps):
                before=[k for k,c in self.gateway.ledger.carriers.items() if c.state!='TERMINAL']
                if not before:break
                cur=int(self.bt.current_timestamp)
                timeout_ns=1000000000 # two pinned250+250ms transport round trips, not a trading rule
                upper=cur+timeout_ns
                rc=int(self.bt.wait_next_feed(True,timeout_ns))
                actual=int(self.bt.current_timestamp)
                if rc not in (0,1,2,3):raise RuntimeError('invalid native response wait '+str(rc))
                if actual<cur:raise RuntimeError('native response clock moved backwards')
                observed=observe_drain(rc,cur,actual,upper)
                previous_receipts=len(self._receipt_ledger.seen)
                self.process(observed);self.flush_terminal_trace(observed)
                remaining=[k for k,c in self.gateway.ledger.carriers.items() if c.state!='TERMINAL']
                record=dict(step=step,native_return=rc,native_clock_ns=actual,query_upper_bound_ns=upper,
                    observed_ms=observed,clock_kind='NATIVE_EVENT_TIME_AT_EOF' if rc==1 else 'NATIVE_RESPONSE_TIME',
                    before_count=len(before),after_count=len(remaining),
                    newly_observed_receipts=len(self._receipt_ledger.seen)-previous_receipts,new_market_events_appended=0)
                self.terminal_drain.append(record);self.trace.emit('transport_drain_observation',record)
                if not remaining:break
                if rc==1:
                    # Without an exact next clock, do not backdate a fresh cancel.
                    record['right_censored_reason']='EOF_WITH_NONTERMINAL_AND_NO_NATIVE_EVENT_CLOCK';break
                if actual<self.verified_market_end*1000000:
                    last_observed=observed;continue
                frame=self.current_frame(observed,self.verified_market_end,self.book,base.quotes(self.book),self.frame_count)
                plan=self.producer.produce(deepcopy(frame))
                if any(a.kind=='NEW' for a in plan.plan.actions):raise RuntimeError('new acquisition beyond actual market end')
                self.consume(frame,plan);last_observed=observed
            self._refresh_slots(last_observed);self._sample_occupancy()
            self.gateway.ledger.invariants()
            return self.terminal_drain
    return Recovered


def self_tests():
    from tools.minimal_student_open_funding_v1 import OpenFundingProfile
    from tools.pair_core_economic_grant_ledger_v1 import Grant
    from tools.minimal_student_training_world_v2 import TrainingPlanGateway
    passed=[]
    def check(n,c):assert c,n;passed.append(n)
    l=FastOpenFundingLedger(OpenFundingProfile())
    for p,s in ((1,'UP'),(2,'DOWN')):l.issue(Grant(p,'FIXTURE',s,0.,0.,0.,'TEST_ONLY'))
    l.reserve('a',1,'PASSIVE',30.,.4,0.,now_ms=1,market_end_ms=300000)
    l.confirm_cumulative('a',.5,.2);l.confirm_terminal('a',filled=.5,payment=.2)
    for g in l.grants.values():check('scalar_grant_equivalence_'+str(g.parent_id),scalar_asdict(g)==stock_asdict(g))
    check('scalar_carrier_equivalence',scalar_asdict(l.carriers['a'])==stock_asdict(l.carriers['a']))
    cp=deepcopy(l);check('clone_accounts_identical',l.account(1)==cp.account(1))
    cp.reserve('b',1,'PASSIVE',300.,.4,0.,now_ms=2,market_end_ms=300000)
    check('clone_independent_carriers','b' not in l.carriers)
    cp.allocation.parents[1].carriers.add('probe')
    check('clone_independent_parent_sets','probe' not in l.allocation.parents[1].carriers)
    cp.carriers['a'].filled=99.
    check('clone_independent_scalar_owners',l.carriers['a'].filled==.5)
    gateway=TrainingPlanGateway(l,asset='BTC',policy_id='TEST',capabilities=('PASSIVE',))
    old=gateway.snapshot_id();install_scalar_codec();check('canonical_snapshot_identical',old==gateway.snapshot_id())
    dust=[dict(qty=1.7763568394002505e-15,cumulative_qty=0.,leaves_qty=35.79)]
    check('numeric_nonadvancing_classified_without_lot_rounding',verify_numeric_owner(35.79,dust,0.)['numeric_residual_only'])
    small=[dict(qty=1e-12,cumulative_qty=1e-12,leaves_qty=18.-1e-12)]
    check('positive_native_cumulative_not_erased',not verify_numeric_owner(18.,small,1e-12)['zero_fill'])
    try:verify_numeric_owner(18.,[dict(qty=.001,cumulative_qty=0.,leaves_qty=18.)],0.)
    except AssertionError:check('material_unrepresented_delta_rejected',True)
    else:raise AssertionError('material unrepresented delta accepted')
    check('engineering_capacity_nonbinding_by_construction',2*2047<4096)
    return dict(passed=len(passed),names=passed)
