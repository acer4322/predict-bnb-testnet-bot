"""Read-only V42 case diagnosis: price tolerance, own-cross and repair cost.

Reads completed artifacts only; no worker, native execution or policy mutation.
Price caps below use relative maximum BUY price, not an SDK minimum-shares model.
"""
from decimal import Decimal, ROUND_FLOOR
import json

import btc5m_pending_repair_pair_v1 as previous
from hft244_pair_route_legality_v1 import crossing_owners

STEM = 'BTC5M_ACTIVE_SLIPPAGE_FRONTIER_V1_20260913'


def dec(x):
    return Decimal(str(x))


def floor_step(x, step):
    return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step


def main():
    root, research = previous.ROOT, previous.R
    package = previous.package('current')
    manifest = previous.read(package / 'manifest.json')
    assert all(previous.sha(package / k) == v for k, v in manifest['files'].items())
    adapter = (package / 'adapter.py').read_text(encoding='utf-8')
    assert 'ACTIVE_LIMIT_MUST_EQUAL_CURRENT_ASK' in adapter
    assert 'base.ex.hbt.GTC,base.ex.hbt.LIMIT,False' in adapter
    world = (root / 'tools/minimal_student_training_world_v2.py').read_text(encoding='utf-8')
    assert 'tick: float = .01' in world
    folder = research / 'lan_worker_returns' / previous.job('current')['job_id']
    trace = previous.read(folder / 'clock_trace.json.gz')
    public_path = package / 'inputs/public_1977248.json.gz'
    public = previous.read(public_path)
    source_index = 1044
    plan_index = next(i for i, r in enumerate(trace['direction_rows']) if r['index'] == source_index)
    plan = trace['plans'][plan_index]
    book = public['books'][plan_index]
    row = next(r for r in trace['renewal_rows'] if r['t'] == plan['t'])
    order = next(o for o in plan['operations'] if o['key'] == 'UP_701')
    assert order['price'] == book['best_ask'] == row['active_ask'] == .07
    assert order['qty'] == 87.79
    assert book['received_ms'] <= plan['t']
    reservations = [dict(key=o['key'], side=o['side'], price=o['limit']) for o in row['state']['owners']]
    reservations += [o for o in row['original_operations'] if o['kind'] == 'NEW']
    tick, step, q, ask = dec('.01'), dec('.01'), dec(order['qty']), dec(order['price'])
    goal = dec(row['status']['work']['anchor_floor'])
    weak = dec(row['state']['payoff']['UP'])
    strong = dec(row['state']['payoff']['DOWN'])
    tolerance_rows = []
    for bps in (0, 1000):
        cap = ask * (1 + dec(bps) / 10000)
        legal = floor_step(cap, tick)
        tolerance_rows.append(dict(bps=bps, raw_price_cap=float(cap), grid_price_cap=float(legal),
                                   newly_reachable_levels=[p for p, _ in book['asks'] if ask < dec(p) <= legal]))
    assert tolerance_rows[1]['grid_price_cap'] == .07
    frontier = []
    for price in (dec('.07'), dec('.08'), dec('.09')):
        cost = q * price
        lift = q - cost
        remaining, displayed_cost = q, dec(0)
        for p, qty in book['asks']:
            if dec(p) > price:
                break
            take = min(remaining, dec(qty))
            displayed_cost += take * dec(p)
            remaining -= take
            if remaining == 0:
                break
        restore_q = floor_step((goal - weak) / (1 - price), step)
        frontier.append(dict(price_cap=float(price), relative_increase_bps=float((price / ask - 1) * 10000),
            same_requested_quantity=float(q), maximum_notional_if_all_fill=float(cost),
            extra_cost_vs_ask_cap=float(cost - q * ask), minimum_weak_lift_if_all_fill=float(lift),
            weak_payoff_if_all_at_cap=float(weak + lift), strong_payoff_if_all_at_cap=float(strong - cost),
            quantity_to_reference_floor_step_if_all_at_cap=float(restore_q),
            reference_quantity_notional_at_cap=float(restore_q * price),
            displayed_depth_within_cap=sum(qty for p, qty in book['asks'] if dec(p) <= price),
            static_book_cost_for_requested_quantity=float(displayed_cost), static_book_unfilled=float(remaining),
            crossing_owner_keys=crossing_owners('UP', float(price), reservations),
            native_result=None))
    assert frontier[0]['crossing_owner_keys'] == []
    assert len(frontier[1]['crossing_owner_keys']) == 6
    assert frontier[1]['extra_cost_vs_ask_cap'] == .8779
    assert all(r['static_book_cost_for_requested_quantity'] == 6.1453 for r in frontier)
    # The actual cost kernel reads fill-level contractPrice, not requested limit.
    receipt_path = root / 'tools/minimal_student_native_system_plan_v1.py'
    assert "p+=r['qty']*r['contractPrice']" in receipt_path.read_text(encoding='utf-8')
    out = dict(status='DIAGNOSTIC_COMPLETE_NOT_NATIVE_TESTED', market=1977248, source_index=source_index,
        source_job=previous.job('current')['job_id'], order=order,
        current_semantics='GTC LIMIT at current ask; size capped at best-level depth; no adverse price tolerance',
        cap_definition='maximum individual BUY execution price relative to current ask; not SDK minimum-shares slippage',
        book=book, quote_source_age_ms=plan['t']-book['source_ms'],
        pre_submit_state={k:row['state'][k] for k in ('inv','cost','payoff','pending_qty','pending_cash')},
        reservations=reservations, repair_reference=float(goal), tolerance_rows=tolerance_rows, frontier=frontier,
        actual_cost_formula='sum(fill_qty * fill_contract_price) + actual fees',
        weak_payoff_change='total_filled_shares - actual_cost', strong_payoff_change='-actual_cost',
        dedup='V36 already studied price/repair efficiency; V42 already diagnosed maintenance. This is the existing Active actuator price cap, grid and pending own-cross feasibility before any new experiment.',
        existing_solutions='Reuse existing depth, crossing_owners, finite-work and receipt accounting. Official Predict SDK market/slippage helper is a live-API reference, not this native adapter.',
        official_sources=[
            'https://github.com/PredictDotFun/sdk-python#slippage',
            'https://github.com/PredictDotFun/sdk/blob/main/src/OrderBuilder.ts',
            'https://docs.polymarket.com/concepts/prices-orderbook'],
        api_findings='Predict SDK defaults to no additional slippage, but can already price across book levels. BUY minimum-shares-out mode differs from fixed-share worst-price tolerance; forward SDK outputs and matching API fields. No verified web UI default of 10%.',
        limitations=[
            'Static actor-visible depth is not guaranteed fill after latency; both proposed larger caps currently fail own-cross.',
            'Do not round .077 up to .08 and call that a 10 percent maximum-price tolerance.',
            'A higher limit is a reservation ceiling, not the actual average execution price.',
            'Same-plan CANCEL does not remove a pending owner; wait for canonical TERMINAL before release.',
            'No causal proof that tolerance would improve native fills or identify Target private logic.'],
        next_scope='Bounded urgent Active price tolerance with finite repair-value cost accounting and conflict-owner coordination. Separate GTC vs immediate-cancel remainder semantics; do not silently switch both in one price experiment.',
        native_jobs=0, model_fits=0, parameter_search=0, worker_connections=0, frozen_policy_changed=False,
        evidence_sha256={str(p.relative_to(root)):previous.sha(p) for p in
            [package/'manifest.json', package/'adapter.py', public_path, folder/'clock_trace.json.gz', receipt_path]})
    destination = research / (STEM + '_DIAGNOSTIC.json')
    destination.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=out['status'], tolerance_rows=tolerance_rows,
        one_tick_extra_maximum_cost=frontier[1]['extra_cost_vs_ask_cap'],
        one_tick_conflicts=frontier[1]['crossing_owner_keys'], native_jobs=0)))


if __name__ == '__main__':
    main()
