"""V36: observed Active price/value and first-decision sensitivity, no simulator."""
import ast
import collections
import inspect
import math
import sys

from audit_btc5m_target_down_timing_v1 import ROOT,R,START,SOURCE,SOURCE_SHA,read,sha,same,verify_target
from btc5m_partial_reexposure_experiment_v1 import JOB
from prepare_btc5m_transfer_structural_v1 import load,once
from hft244_pair_route_legality_v1 import crossing_owners

STEM='BTC5M_ACTIVE_PRICE_VALUE_V1_20260913'
PACKAGE=ROOT/'.lan_worker_v1/partial_reexposure_2028352_20260913_v1'
EPS=1e-8


def dump(tag,x):
    import json
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def price_value(inv,cost,side,price):
    other='DOWN' if side=='UP' else 'UP'
    gain=inv[other]-cost;loss=cost-inv[side]
    valid=gain>EPS and loss>EPS
    return dict(opposite_gain=gain,bought_loss=loss,one_positive_one_negative=valid,
        prefix_weak=inv[side]<inv[other]-EPS,
        price_boundary=gain/(gain+loss) if valid else None,
        margin=(1-price)*gain-price*loss,
        ratio_improving_at_prefix=(1-price)*gain-price*loss>=-EPS if valid else None,
        weak_lift_per_cash=(1-price)/price)


def unknown_order_bounds(inv,cost,side,price,other_legs):
    """All possible subsets of other same-second or pending fills; no ordering assumed.

    H=(1-p)*Q_other+p*Q_side-C. Each other leg adds q*(weight-price).
    Independently summing negative/positive contributions bounds every subset,
    including any partially filled quantity. It is not an inferred actual sequence.
    """
    base=price_value(inv,cost,side,price);contributions=[]
    gmin=base['opposite_gain'];lmin=base['bought_loss']
    for leg in other_legs:
        q=leg['qty'];p=leg['price'];same_side=leg['side']==side
        contributions.append(q*((price if same_side else 1-price)-p))
        if same_side:gmin-=q*p;lmin-=q*(1-p)
    low=base['margin']+math.fsum(min(0,x) for x in contributions)
    high=base['margin']+math.fsum(max(0,x) for x in contributions)
    regime_stable=gmin>EPS and lmin>EPS
    classification='REGIME_NOT_GUARANTEED'
    if regime_stable:
        classification='FAVORABLE_ALL_SUBSETS' if low>=-EPS else 'UNFAVORABLE_ALL_SUBSETS' if high<-EPS else 'ORDER_SENSITIVE'
    return dict(margin_lower=low,margin_upper=high,opposite_gain_lower=gmin,bought_loss_lower=lmin,
        positive_gain_negative_loss_for_all_subsets=regime_stable,classification=classification)


def target_audit(source,curve):
    actions=source['targetActions'];rows=[]
    for bucket in curve:
        second=[a for a in actions if a['event_ms']==bucket['t']]
        before=bucket['before'];inv=before['inv'];cost=before['cost']
        for side in ('UP','DOWN'):
            active=[a for a in second if a['side']==side and a['role']=='TAKER']
            if not active:continue
            qty=math.fsum(a['shares'] for a in active);cash=math.fsum(a['shares']*a['price'] for a in active)
            detail=[]
            for a in active:
                others=[dict(side=b['side'],qty=b['shares'],price=b['price']) for b in second if b['source_leg_id']!=a['source_leg_id']]
                detail.append(dict(source_leg_id=a['source_leg_id'],qty=a['shares'],price=a['price'],
                    value=price_value(inv,cost,side,a['price']),same_second_bounds=unknown_order_bounds(inv,cost,side,a['price'],others)))
            rows.append(dict(t=bucket['t'],seconds=(bucket['t']-START)/1000,side=side,qty=qty,cash=cash,vwap=cash/qty,
                minimum_price=min(a['price'] for a in active),maximum_price=max(a['price'] for a in active),
                legs=len(active),parents=len({a['order_hash'] for a in active}),before=before,
                value=price_value(inv,cost,side,cash/qty),weak_lift=qty-cash,
                other_side_same_second_cash=math.fsum(a['shares']*a['price'] for a in second if a['side']!=side),
                robust_classes=dict(collections.Counter(x['same_second_bounds']['classification'] for x in detail)),details=detail))
    assert sum(r['legs'] for r in rows)==sum(a['role']=='TAKER' for a in actions)
    weak=[r for r in rows if r['value']['prefix_weak']]
    defined=[r for r in weak if r['value']['one_positive_one_negative']]
    good=[r for r in defined if r['value']['ratio_improving_at_prefix']]
    bad=[r for r in defined if not r['value']['ratio_improving_at_prefix']]
    # Same observed fill price can occur under both routes; this is not a NEW/no-NEW classifier.
    common=[]
    for side in ('UP','DOWN'):
        prices=sorted({a['price'] for a in actions if a['side']==side})
        for p in prices:
            groups={route:[a for a in actions if a['side']==side and a['role']==route and a['price']==p] for route in ('MAKER','TAKER')}
            if all(groups.values()):common.append(dict(side=side,price=p,routes={route:dict(legs=len(v),qty=math.fsum(a['shares'] for a in v),
                seconds=sorted({(a['event_ms']-START)/1000 for a in v})) for route,v in groups.items()}))
    return dict(rows=rows,counts=dict(active_side_seconds=len(rows),prefix_weak_side_seconds=len(weak),
        defined_positive_gain_negative_loss=len(defined),prefix_favorable=len(good),prefix_unfavorable=len(bad),
        favorable_qty=math.fsum(x['qty'] for x in good),unfavorable_qty=math.fsum(x['qty'] for x in bad)),
        same_price_both_observed_routes=common,
        warning='Only observed fill-side seconds, not independent decisions or an unfilled/no-action denominator. Price boundary is prior V21 arithmetic, not a fitted Target formula.')


def own_price_probe(trace):
    sys.path.insert(0,str(PACKAGE));import roles_runtime
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION','UP')
    base=load('frozen_active_value_reference',PACKAGE/'active_opportunity.py')
    source=inspect.getsource(base.decide)
    veto='elif passive_price > 0 and 15.0 * passive_price >= 1 - EPS:'
    extension="elif passive_price > 0 and 15.0 * passive_price >= 1 - EPS and not relative_price_allows(state, ask):"
    source=once(source,veto,extension)
    def relative_price_allows(state,ask):
        if ask is None or not 0<ask<1:return False
        v=price_value(state['inv'],state['cost'],'DOWN',ask)
        return v['ratio_improving_at_prefix'] is True
    ns=dict(base.__dict__,relative_price_allows=relative_price_allows)
    exec(compile(source,'LOCAL_PRICE_GATE_SENSITIVITY_ONLY','exec'),ns)
    alternate=ns['decide'];prefix=[];first=None
    for row in trace['opportunity_rows']:
        args=(row['state'],row['paid'],row['passive_price'],row['active_ask'],row['visible_depth'],row['original_operations'],.01,0.,crossing_owners)
        original=base.decide(*args)
        for k,v in original.items():same(v,row[k])
        proposed=alternate(*args)
        prefix.append(dict(t=row['t'],baseline_reason=original['reason'],proposed=proposed))
        if proposed['eligible'] and not original['eligible']:
            s=row['state'];pending=[dict(side=o['side'],price=o['limit'],qty=o['qty']) for o in s['owners']]
            pending.extend(dict(side=o['side'],price=o['price'],qty=o['qty']) for o in row['original_operations'] if o['kind']=='NEW')
            first=dict(t=row['t'],seconds=(row['t']-START)/1000,baseline=row,proposed=proposed,
                value=price_value(s['inv'],s['cost'],'DOWN',row['active_ask']),
                pending_all_subset_bounds=unknown_order_bounds(s['inv'],s['cost'],'DOWN',row['active_ask'],pending))
            break
    assert first is not None
    # Repeat only this causal prefix; no changed-path state is taken from the old future.
    for cut in (1,len(prefix)//2,len(prefix)):
        for i,row in enumerate(trace['opportunity_rows'][:cut]):
            decision=alternate(row['state'],row['paid'],row['passive_price'],row['active_ask'],row['visible_depth'],row['original_operations'],.01,0.,crossing_owners)
            same(decision,prefix[i]['proposed'])
    return dict(status='PREFIX_ONLY_COMPONENT_PASS',first=first,checked_prefix_rows=len(prefix),prefix=prefix,
        original_first_active=trace['opportunity_submissions'][0],
        single_change='Existing first-Active price veto admits an additional G>0,L>0,ask<=G/(G+L) case. Quantity/depth/cash/pending/crossing remain unchanged.',
        disposition='NOT_FROZEN_NOT_DISPATCHED: the pure decision function becomes eligible early at a non-low absolute price. Integration could advance the sole first-Active selection; no complete NEW or changed continuation was simulated. This does not isolate the observed later repair episode.',
        eligibility_is_not_a_new_or_fill=True,model_fit=0,native_jobs=0)


def verify_linear_bounds():
    import itertools
    inv=dict(UP=100.,DOWN=40.);cost=80.;p=.3
    legs=[dict(side='UP',qty=15.,price=.9),dict(side='DOWN',qty=12.,price=.4),dict(side='DOWN',qty=10.,price=.1)]
    bound=unknown_order_bounds(inv,cost,'DOWN',p,legs);margins=[]
    for weights in itertools.product((0.,.5,1.),repeat=3):
        q=dict(inv);c=cost
        for w,l in zip(weights,legs):q[l['side']]+=w*l['qty'];c+=w*l['qty']*l['price']
        v=price_value(q,c,'DOWN',p);margins.append(v['margin'])
        assert bound['margin_lower']-EPS<=v['margin']<=bound['margin_upper']+EPS
    same(min(margins),bound['margin_lower']);same(max(margins),bound['margin_upper'])
    # Buying q weak shares at the specified price changes H by exactly zero.
    for q in (0.,15.,30.):
        same(price_value(dict(UP=100.,DOWN=40.+q),80.+q*p,'DOWN',p)['margin'],price_value(inv,cost,'DOWN',p)['margin'])
    return dict(status='PASS',enumerated_partial_fill_subsets=27,exact_extrema=True)


def main():
    assert sha(SOURCE)==SOURCE_SHA
    manifest=read(PACKAGE/'manifest.json');assert all(sha(PACKAGE/k)==h for k,h in manifest['files'].items())
    dump('PROTOCOL',dict(status='RETROSPECTIVE_MECHANISM_DIAGNOSIS',market=2028352,
        question='Can sufficiently favorable price explain a component of Target active repair, and what happens if that alone extends OUR price gate?',
        dedup='V21/V30 already derived G/(G+L) and reported exceptions, not an Active route policy. V35 aligned actual timing. New: all Target Active-side seconds using pre-second physical weak side, bounds over unknown same-second order, and first-decision sensitivity of the current first-Active veto.',
        native_jobs=0,actor_changes=0,model_fits=0,parameter_search=0,figures=0,
        interpretation='G/(G+L) is a conditional-payoff exchange boundary, not fair probability, price forecast, optimal quantity or a universal Target rule.'))
    source=read(SOURCE);_,curve,checks=verify_target(source)
    trpath=R/'lan_worker_returns'/JOB/'clock_trace.json.gz';trace=read(trpath)
    target=target_audit(source,curve);own=own_price_probe(trace)
    out=dict(status='COMPLETE',verification='PASS',target_checks=checks,linear_bounds_check=verify_linear_bounds(),target=target,our_probe=own,
        source_sha256=SOURCE_SHA,baseline_trace_sha256=sha(trpath),baseline_manifest_sha256=sha(PACKAGE/'manifest.json'),
        native_jobs=0,worker_calls=0,actor_changes=0,model_fits=0,parameter_search=0,figures=0,
        next='Treat favorable-price active repair as one possible branch. Separate economic value, outstanding realized repair need and route urgency; do not promote the early first-hit price-only probe as the learned core loop.')
    dump('RESULT',out);dump('PROGRESS',dict(status='COMPLETE',verification='PASS',new_native_jobs=0,actor_changes=0,model_fit=0,
        first_local_decision_difference_seconds=own['first']['seconds'],candidate_frozen=False,candidate_dispatched=False))
    import json
    print(json.dumps(dict(status='PASS',counts=target['counts'],own_first_seconds=own['first']['seconds'],
        own_ask=own['first']['proposed']['active_ask'],own_qty=own['first']['proposed']['quantity'],
        examples=[{k:r[k] for k in ('seconds','side','qty','cash','weak_lift','robust_classes')}
            for r in target['rows'] if r['seconds'] in (54,168,172,181,187)])))


if __name__=='__main__':main()
