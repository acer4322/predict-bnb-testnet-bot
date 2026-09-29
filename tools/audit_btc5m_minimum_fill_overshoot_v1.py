"""Bounded offline audit of fills, observed parent totals and net-side crossings.

Uses the existing eight consumed sources. Observed parent volume is a lower
bound on original submitted volume, never a reconstructed original request.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import json

from audit_btc5m_target_core_loop_topology_v1 import ROOT, R, read, sha, MIDS

STEM = 'BTC5M_MINIMUM_FILL_OVERSHOOT_V1_20260913'
D = lambda x: Decimal(str(x))
EPS = D('0.00000001')


def key(a):
    return (a['role'], a['side'], a['order_hash'])


def analyze(source):
    parents = defaultdict(list)
    buckets = defaultdict(list)
    actions = source['targetActions']
    assert len({a['source_leg_id'] for a in actions}) == len(actions)
    for a in actions:
        assert a['quote_type'] == 'BID' and a['side'] in ('UP', 'DOWN')
        assert a['role'] in ('MAKER', 'TAKER')
        assert 0 < D(a['price']) < 1 and D(a['shares']) > 0
        parents[key(a)].append(a)
        buckets[int(a['event_ms'])].append(a)
    declared = {key(p): p for p in source['targetParents']}
    assert set(parents) == set(declared)
    parent_rows = []
    stats = {s: Counter() for s in ('ALL', 'MAKER', 'TAKER')}
    for k, legs in parents.items():
        q = sum(D(a['shares']) for a in legs)
        cash = sum(D(a['shares']) * D(a['price']) for a in legs)
        times = sorted({int(a['event_ms']) for a in legs})
        p = declared[k]
        assert abs(q - D(p['shares'])) < EPS
        assert abs(cash - D(p['average_price']) * D(p['shares'])) < D('0.000001')
        assert len(legs) == p['fill_legs'] and times[0] == p['first_event_ms'] and times[-1] == p['last_event_ms']
        small = [a for a in legs if D(a['shares']) * D(a['price']) < 1 - EPS]
        boundary = [a for a in legs if abs(D(a['shares']) * D(a['price']) - 1) <= EPS]
        row = dict(role=k[0], side=k[1], order_hash=k[2], observed_shares=float(q),
                   observed_cash=float(cash), fill_legs=len(legs), subdollar_legs=len(small),
                   dollar_boundary_legs=len(boundary),
                   first_event_ms=times[0], last_event_ms=times[-1], original_requested=None, terminal=None)
        parent_rows.append(row)
        for cohort in ('ALL', k[0]):
            c = stats[cohort]
            c['legs'] += len(legs)
            c['parents'] += 1
            c['subdollar_legs'] += len(small)
            c['dollar_boundary_legs'] += len(boundary)
            c['subdollar_legs_with_parent_observed_cash_ge_one'] += len(small) if cash >= 1 - EPS else 0
            c['subdollar_legs_with_parent_observed_cash_lt_one'] += len(small) if cash < 1 - EPS else 0
            c['parents_observed_cash_lt_one'] += int(cash < 1 - EPS)
            c['parents_multiple_legs'] += int(len(legs) > 1)
            c['parents_multiple_event_seconds'] += int(len(times) > 1)
            c['parents_containing_subdollar_legs'] += int(bool(small))
            c['subdollar_parent_original_request_unknown'] += int(cash < 1 - EPS)
    inventory = dict(UP=D(0), DOWN=D(0))
    cost = D(0)
    crossings = []
    gross_exceeds_without_cross = []
    for t, legs in sorted(buckets.items()):
        before = inventory.copy()
        net = before['UP'] - before['DOWN']
        fill = {s: sum((D(a['shares']) for a in legs if a['side'] == s), D(0)) for s in inventory}
        cash = sum(D(a['shares']) * D(a['price']) for a in legs)
        for s in inventory:
            inventory[s] += fill[s]
        cost += cash
        after_net = inventory['UP'] - inventory['DOWN']
        if abs(net) < EPS:
            continue
        strong, weak = ('UP', 'DOWN') if net > 0 else ('DOWN', 'UP')
        weak_legs = [a for a in legs if a['side'] == weak]
        if fill[weak] <= abs(net) + EPS:
            continue
        weak_cash = sum(D(a['shares']) * D(a['price']) for a in weak_legs)
        crosses = net * after_net < -EPS
        average = weak_cash / fill[weak]
        row = dict(t=t, before_inv={s: float(v) for s, v in before.items()},
                   before_net=float(net), after_net=float(after_net), weak_side=weak,
                   weak_fill=float(fill[weak]), strong_fill=float(fill[strong]),
                   weak_fill_cash=float(weak_cash), weak_roles=sorted({a['role'] for a in weak_legs}),
                   weak_parents=len({key(a) for a in weak_legs}),
                   earlier_observed_weak_parent=any(declared[key(a)]['first_event_ms'] < t for a in weak_legs),
                   weak_only=fill[strong] == 0, net_crosses=crosses,
                   after_payoff={s: float(inventory[s] - cost) for s in inventory},
                   prior_gap_cost_at_observed_weak_average_price=float(abs(net) * average),
                   prior_gap_cost_lt_one=abs(net) * average < 1 - EPS,
                   subdollar_weak_legs=sum(D(a['shares']) * D(a['price']) < 1 - EPS for a in weak_legs),
                   original_order_request=None, submit_time=None, private_pending=None)
        (crossings if crosses else gross_exceeds_without_cross).append(row)
    return dict(market=int(source['market']['market_id']), stats={k: dict(v) for k, v in stats.items()},
                bucket_count=len(buckets), crossing_count=len(crossings), crossings=crossings,
                gross_weak_exceeds_before_gap_without_net_cross=gross_exceeds_without_cross,
                parent_rows=parent_rows, final_inventory={s: float(v) for s, v in inventory.items()})


def main():
    dataset = ROOT / '.lan_worker_v1/concurrent_flow_probe_20260913_v1/dataset.json'
    sources = read(dataset)['sources']
    rows = []
    for src in sources:
        p = ROOT / src['path']
        assert sha(p) == src['sha256']
        source = read(p)
        assert source['targetObservationOnly'] and source['originalOrderQuantity'] is None
        assert source['originalTerminalState'] is None and source['zeroFillOrderUniverse'] is None
        rows.append(analyze(source))
    assert tuple(r['market'] for r in rows) == MIDS
    combined = {cohort: dict(sum((Counter(r['stats'][cohort]) for r in rows), Counter()))
                for cohort in ('ALL', 'MAKER', 'TAKER')}
    crosses = [dict(market=r['market'], **c) for r in rows for c in r['crossings']]
    checkpoint_path = R / 'BTC5M_CORE_LOOP_CONTROL_GAP_V1_20260913.json'
    cp = read(checkpoint_path)['next_static_checkpoint']['first']
    gap = cp['state']['inv']['UP'] - cp['state']['inv']['DOWN']
    cp_summary = dict(t=cp['t'], original_candidate=cp['original_requested'], price=cp['price'],
                      original_notional=cp['original_requested'] * cp['price'], original_submitted=False,
                      actual_share_gap=gap, pending_down=cp['state']['pending_qty']['DOWN'],
                      uncovered_quantity=cp['quantity_need'], proposed=cp['proposed'],
                      exceeds_manager_candidate=cp['proposed'] > cp['original_requested'],
                      exceeds_uncovered_gap=cp['proposed'] > cp['quantity_need'],
                      native_submitted_in_referenced_v13_static_audit=None,
                      later_native_result='BTC5M_SINGLE_REPAIR_DEMAND_V1_20260913_RESULT.json; separate experiment')
    out = dict(status='COMPLETE', scope='Eight previously consumed markets; descriptive fill-only accounting, no fitting or HFT.',
               dataset_sha256=sha(dataset), sources=sources, stats=combined,
               dollar_comparison_tolerance=float(EPS),
               crossings=dict(total=len(crosses), markets=len({c['market'] for c in crosses}),
                   weak_only=sum(c['weak_only'] for c in crosses),
                   with_earlier_observed_weak_parent=sum(c['earlier_observed_weak_parent'] for c in crosses),
                   prior_gap_cost_lt_one=sum(c['prior_gap_cost_lt_one'] for c in crosses),
                   all_weak_legs_maker=sum(c['weak_roles'] == ['MAKER'] for c in crosses),
                   all_weak_legs_taker=sum(c['weak_roles'] == ['TAKER'] for c in crosses)),
               selected_checkpoint=cp_summary, checkpoint_sha256=sha(checkpoint_path), markets=rows,
               limitations=[
                   'Sub-dollar execution legs are not invalid new-order requests. Larger observed totals for the same parent establish only more cumulative executed volume.',
                   'Even a parent whose observed total is below one may be a partial/censored order. Original quantity, remaining quantity and terminal state are unknown.',
                   'Net-side crossings are observed outcomes. They do not identify deliberate oversizing, minimum-size causation, placement time or private pending orders.',
                   'Whole-second two-side batches have no private within-second ordering. Gross weak fills can exceed the old gap without a net crossing when strong fills also arrive.',
                   'Prior-gap notional at observed average fill price is retrospective arithmetic, not the decision quote or a strategy threshold.',
                   'BTC18 and notional1 are the existing OUR Passive research contract. This does not verify Target original-order share minima.',
                   'No original-order sizing labels, HOLD labels or new actor parameters are inferred from these observations.'])
    (R / (STEM + '.json')).write_text(json.dumps(out, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=out['status'], stats=combined, crossings=out['crossings'],
                         per_market=[dict(market=r['market'], crossings=r['crossing_count'], buckets=r['bucket_count']) for r in rows],
                         selected_checkpoint=cp_summary), indent=2))


if __name__ == '__main__':
    main()
