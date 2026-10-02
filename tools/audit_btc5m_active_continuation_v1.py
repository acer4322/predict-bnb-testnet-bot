"""V37: ordinary Active flow and the frozen coordinator's service gates.

Offline accounting and pure decision functions only. No native or actor edits.
"""
import collections
import inspect
import json
import math
import sys

from audit_btc5m_target_down_timing_v1 import (
    ROOT, R, START, SOURCE, SOURCE_SHA, read, sha, same, verify_target,
)
from audit_btc5m_active_price_value_v1 import price_value
from btc5m_partial_reexposure_experiment_v1 import JOB, keyed_legs
from prepare_btc5m_transfer_structural_v1 import load, once
from hft244_pair_route_legality_v1 import crossing_owners

STEM = 'BTC5M_ACTIVE_CONTINUATION_V1_20260913'
PACKAGE = ROOT / '.lan_worker_v1/partial_reexposure_2028352_20260913_v1'
EPS = 1e-8


def dump(tag, value):
    (R / (STEM + '_' + tag + '.json')).write_text(
        json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def contribution(actions, side):
    out = {}
    for relation in ('same', 'opposite'):
        for route in ('MAKER', 'TAKER'):
            rows = [a for a in actions if (a['side'] == side) == (relation == 'same') and a['role'] == route]
            qty = math.fsum(a['shares'] for a in rows)
            cash = math.fsum(a['shares'] * a['price'] for a in rows)
            out[relation + '_' + route] = dict(legs=len(rows), qty=qty, cash=cash,
                bought_side_payoff_change=qty-cash if relation == 'same' else -cash)
    out['total_payoff_change'] = math.fsum(x['bought_side_payoff_change'] for x in out.values())
    return out


def target_rows(actions, curve):
    by_time = collections.defaultdict(list)
    first_parent = {}
    for a in actions:
        by_time[a['event_ms']].append(a)
        key = (a['role'], a['side'], a['quote_type'], a['order_hash'])
        first_parent[key] = min(first_parent.get(key, a['event_ms']), a['event_ms'])
    previous = {}
    rows = []
    for bucket in curve:
        t = bucket['t']; batch = by_time[t]; before = bucket['before']
        for side in ('UP', 'DOWN'):
            active = [a for a in batch if a['side'] == side and a['role'] == 'TAKER']
            if not active:
                continue
            other = 'DOWN' if side == 'UP' else 'UP'
            qty = math.fsum(a['shares'] for a in active)
            cash = math.fsum(a['shares'] * a['price'] for a in active)
            parents = {(a['role'], a['side'], a['quote_type'], a['order_hash']) for a in active}
            current = contribution(batch, side)
            row = dict(t=t, seconds=(t-START)/1000, side=side, legs=len(active),
                qty=qty, cash=cash, vwap=cash/qty, active_branch_lift=qty-cash,
                parents=len(parents), first_observed_parents=sum(first_parent[k] == t for k in parents),
                before=before, after=dict(inv=bucket['inv'], cost=bucket['cost']),
                value=price_value(before['inv'], before['cost'], side, cash/qty),
                current_second=current, previous_same_side_active=None)
            if side in previous:
                prev = previous[side]
                between = [a for a in actions if prev['t'] < a['event_ms'] < t]
                interval = contribution(between, side)
                old = prev['inv'][side] - prev['cost']
                now = before['inv'][side] - before['cost']
                same(now-old, interval['total_payoff_change'])
                assert interval['same_TAKER']['legs'] == 0
                middle = [b for b in curve if prev['t'] < b['t'] < t]
                # Monotone BID quantities: all same-side fills before any opposite
                # fills maximize the same-side inventory advantage in each second.
                weak_stable = prev['inv'][side] < prev['inv'][other]-EPS
                for b in [*middle, bucket]:
                    own_qty = math.fsum(a['shares'] for a in by_time[b['t']] if a['side'] == side)
                    weak_stable &= b['before']['inv'][side] + own_qty < b['before']['inv'][other]-EPS
                open_drag = math.fsum(interval['opposite_' + k]['cash'] for k in ('MAKER', 'TAKER'))
                current_drag = math.fsum(current['opposite_' + k]['cash'] for k in ('MAKER', 'TAKER'))
                previous_drag = math.fsum(a['shares']*a['price'] for a in by_time[prev['t']] if a['side'] == other)
                row['previous_same_side_active'] = dict(
                    t=prev['t'], seconds=(prev['t']-START)/1000,
                    end_of_previous_second_payoff=old, before_current_second_payoff=now,
                    interval=interval, payoff_worsened=now < old-EPS,
                    same_side_remains_weak_all_possible_second_prefixes=bool(weak_stable),
                    opposite_cash_between=open_drag, opposite_cash_current_second=current_drag,
                    opposite_cash_previous_active_second=previous_drag,
                    no_opposite_acquisition_between_or_in_current_second=open_drag+current_drag <= EPS,
                    no_opposite_even_in_previous_active_second=previous_drag+open_drag+current_drag <= EPS)
            rows.append(row)
            previous[side] = bucket
    assert sum(r['legs'] for r in rows) == sum(a['role'] == 'TAKER' for a in actions)
    return rows


def our_gate_audit(source, trace, native):
    sys.path.insert(0, str(PACKAGE))
    import roles_runtime
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION', 'UP')
    module = load('v37_frozen_coordinator', PACKAGE / 'coordination.py')
    code = once(inspect.getsource(module.decide), 'elif floor_price < ask - EPS:', 'elif False:')
    ns = dict(module.__dict__)
    exec(compile(code, 'DIAGNOSTIC_PRICE_VETO_MASK_ONLY', 'exec'), ns)
    price_unmasked = ns['decide']
    episode = trace['coordination_episode']
    for row in trace['coordination_rows']:
        actual = module.decide(row['state'], row['original_operations'], row['active_ask'],
            row['visible_depth'], episode if row['anchor_floor'] is not None else None,
            row['active_confirmed'], crossing_owners)
        for k, v in actual.items():
            same(v, row[k])
    assert len(trace['plans']) == len(source['books']) == 1448
    plans = {p['t']: (i, p) for i, p in enumerate(trace['plans'])}
    assert len(plans) == len(trace['plans'])  # Verified uniqueness here, not a general clock assumption.
    for p, b in zip(trace['plans'], source['books']):
        assert p['t'] == b['received_ms']
    submission = trace['coordination_submissions'][0]
    news = {o['key']: dict(t=p['t'], **o) for p in trace['plans'] for o in p['operations'] if o['kind'] == 'NEW'}
    legs = keyed_legs(native, trace, news)
    active_legs = [a for a in legs if a['key'] == submission['key']]
    fill_t = max(a['t'] for a in active_legs)
    fill_qty = math.fsum(a['qty'] for a in active_legs)
    fill_cash = math.fsum(a['cash'] for a in active_legs)
    same(fill_qty, submission['qty'])
    final_owner = next(o for o in trace['demand_final']['all_final_carriers'] if o['key'] == submission['key'])
    assert final_owner['state'] == 'TERMINAL'
    fill_state = next(r for r in trace['states'] if r['t'] == fill_t)
    after_fill_payoff = fill_state['inv']['DOWN'] - fill_state['cost']
    same(after_fill_payoff, episode['after']['payoff']['DOWN'] + fill_qty-fill_cash)
    diagnostics = []; first = None; decisions_to_first = []
    for row in trace['commitment_repair_rows']:
        if row['t'] <= submission['t']:
            continue
        index, plan = plans[row['t']]; book = source['books'][index]
        ask = 1-book['best_bid'] if book['best_bid'] is not None else None
        same(ask, row['ask'])
        depth = max(book['bids'], default=[None, 0], key=lambda b: b[0])[1]
        # Coordination already returned at the lifetime cap. Final plan is the
        # exact operations it would see, including commitment-repair NEWs.
        assert plan['operations'][:len(row['original_operations'])] == row['original_operations']
        args = (row['state'], plan['operations'], ask, depth, episode,
            row['active_confirmed'], crossing_owners)
        base = module.decide(*args)
        diagnostics.append(dict(t=row['t'], seconds=(row['t']-START)/1000,
            source_index=index, fixed_goal_confirmed_gap=max(0., episode['anchor_floor']-row['state']['payoff']['DOWN']),
            decision_if_lifetime_cap_only_removed=base))
        if first is None:
            alternate = price_unmasked(*args)
            decisions_to_first.append((args, alternate))
            if alternate['eligible']:
                assert row['t'] > fill_t
                assert all(o['key'] != submission['key'] for o in row['state']['owners'])
                first = dict(t=row['t'], seconds=(row['t']-START)/1000, source_index=index,
                    source_ms=book['source_ms'], received_ms=book['received_ms'],
                    state=row['state'], original_operations=plan['operations'],
                    baseline=base, diagnostic=alternate, earlier_active_full_fill_t=fill_t,
                    value=price_value(row['state']['inv'], row['state']['cost'], 'DOWN', ask),
                    candidate_cash=alternate['quantity']*ask,
                    isolated_down_lift=alternate['quantity']*(1-ask),
                    confirmed_gap_before=episode['anchor_floor']-row['state']['payoff']['DOWN'],
                    confirmed_gap_if_only_candidate_filled=episode['anchor_floor']-row['state']['payoff']['DOWN']-alternate['quantity']*(1-ask),
                    all_same_plan_and_pending_retained=True)
    assert first is not None
    for cut in (1, len(decisions_to_first)//2, len(decisions_to_first)):
        for args, expected in decisions_to_first[:cut]:
            same(price_unmasked(*args), expected)
    # No counterfactual suffix is inferred after the first alternative decision.
    return dict(status='PURE_DECISION_DIAGNOSIS_PASS', original_decision_rows_recomputed=len(trace['coordination_rows']),
        source_plan_clock_rows_checked=len(trace['plans']), post_submission_rows=len(diagnostics),
        actual_service=dict(submission=submission, full_fill_t=fill_t, qty=fill_qty, cash=fill_cash,
            branch_lift=fill_qty-fill_cash, anchor=episode['anchor_floor'], after_fill_down_payoff=after_fill_payoff,
            residual_to_exact_anchor=episode['anchor_floor']-after_fill_payoff, final_owner=final_owner),
        lifetime_cap_only_eligible_rows=sum(r['decision_if_lifetime_cap_only_removed']['eligible'] for r in diagnostics),
        lifetime_cap_only_reason_counts=dict(collections.Counter(r['decision_if_lifetime_cap_only_removed']['reason'] for r in diagnostics)),
        baseline_path_diagnostics=diagnostics, first_two_masks_eligible=first,
        two_masks_prefix_rows=len(decisions_to_first), prefix_cuts_checked=3,
        warning='Removing only the cap retains the already latched anchor; this does not test recurrent goal renewal. Two-mask eligibility is a pure function result, not a full NEW, native fill, or policy continuation. The original goal is a finite OUR reference, not Target desired exposure.')


def main():
    assert sha(SOURCE) == SOURCE_SHA
    manifest = read(PACKAGE / 'manifest.json')
    frozen_hashes = {k: sha(PACKAGE/k) for k in manifest['files']}
    assert frozen_hashes == manifest['files']
    folder = R / 'lan_worker_returns' / JOB
    trace_path = folder / 'clock_trace.json.gz'
    trace_hash = sha(trace_path)
    dump('PROTOCOL', dict(status='RETROSPECTIVE_DIAGNOSIS', market=2028352,
        question='Do ordinary observed Active flows require fresh exposure deterioration, and can the frozen coordinator serve an existing finite goal beyond its cheap-price route?',
        dedup=[
            'Historical V5/V8 already tested persistent residual, terminal rearm and Passive-to-Active routes; do not repeat as a new architecture.',
            '20260911 144-market route research already tested previous-Active net reference, Maker progress and price proxies, mostly repeated strong-side flows; those are not identified Target desired states.',
            'V36 established ratio-favorable cheap flow is only one component. New scope: all current-market weak-Active intervals using conditional-payoff contributions and parent first-observation checks, plus exact V34 finite-anchor and lifetime/price gate decomposition.'],
        no_action_denominator=False, private_target_intent='UNKNOWN', native_jobs=0, worker_calls=0,
        actor_changes=0, model_fits=0, parameter_search=0, figures=0))
    source = read(SOURCE); _, curve, checks = verify_target(source)
    rows = target_rows(source['targetActions'], curve)
    same(rows, target_rows(list(reversed(source['targetActions'])), curve))
    # Time-truncated inputs cannot alter earlier accounting or first-observed parent labels.
    for seconds in (86, 192, 245):
        t = START+seconds*1000
        cropped = target_rows([a for a in source['targetActions'] if a['event_ms'] <= t], [b for b in curve if b['t'] <= t])
        same(cropped, [r for r in rows if r['t'] <= t])
    weak = [r for r in rows if r['value']['prefix_weak']]
    repeat = [r for r in weak if r['previous_same_side_active'] is not None]
    no_new_drag = [r for r in repeat if r['previous_same_side_active']['no_opposite_acquisition_between_or_in_current_second']]
    strict = [r for r in no_new_drag if r['previous_same_side_active']['same_side_remains_weak_all_possible_second_prefixes']]
    previous_second_robust = [r for r in strict if r['previous_same_side_active']['no_opposite_even_in_previous_active_second']]
    counts = dict(active_side_seconds=len(rows), weak_side_seconds=len(weak), weak_with_prior_same_active=len(repeat),
        weak_repeat_without_new_prior_payoff_worsening=sum(not r['previous_same_side_active']['payoff_worsened'] for r in repeat),
        no_opposite_between_or_in_current_second=len(no_new_drag), no_opposite_and_weak_all_subsets=len(strict),
        no_opposite_including_previous_second_and_weak_all_subsets=len(previous_second_robust),
        parent_side_seconds=sum(r['parents'] for r in rows), first_observed_parent_side_seconds=sum(r['first_observed_parents'] for r in rows))
    native = read(folder/'result.json'); trace = read(trace_path)
    assert native['status'] == 'COMPLETE' and native['execution_accounting_valid'] and native['unresolved_owners'] == 0
    own = our_gate_audit(source, trace, native)
    assert {k: sha(PACKAGE/k) for k in frozen_hashes} == frozen_hashes and sha(trace_path) == trace_hash
    result = dict(status='COMPLETE', verification='PASS', source_sha256=SOURCE_SHA,
        baseline_manifest_sha256=sha(PACKAGE/'manifest.json'), baseline_trace_sha256=trace_hash,
        target_checks=checks, target_counts=counts, target_rows=rows,
        strict_no_new_opposite_examples=[dict(seconds=r['seconds'], side=r['side'], qty=r['qty'],
            vwap=r['vwap'], ratio_improving=r['value']['ratio_improving_at_prefix'],
            no_opposite_even_in_previous_active_second=r['previous_same_side_active']['no_opposite_even_in_previous_active_second']) for r in strict],
        our=own, validation=dict(target_input_order_invariance=True, target_prefix_cuts=3,
            interval_payoff_identity=True, all_frozen_files_unchanged=len(frozen_hashes), baseline_trace_unchanged=True),
        limitations='Observed Active-side seconds are not independent private decisions. First observed parent is not new submission. Branch payoff is an accounting outcome, not intent. Unknown fees/rebates and opening inventory are not invented; reconstruction uses observed BID acquisitions and zero opening inventory.',
        native_jobs=0, worker_calls=0, actor_changes=0, model_fits=0, parameter_search=0, figures=0)
    dump('RESULT', result)
    dump('PROGRESS', dict(status='COMPLETE', verification='PASS', candidate_frozen=False,
        candidate_dispatched=False, new_native_jobs=0, actor_changes=0,
        first_diagnostic_seconds=own['first_two_masks_eligible']['seconds']))
    dump('FINAL_VALIDATION', dict(status='PASS', source=checks, target_counts=counts,
        target_order_and_prefix_checks=result['validation'], our_original_rows=own['original_decision_rows_recomputed'],
        our_post_submission_rows=own['post_submission_rows'], our_prefix_cuts=3,
        frozen_actor_and_trace_unchanged=True, native_jobs=0))
    print(json.dumps(dict(status='PASS', target_counts=counts,
        strict_no_new_opposite_examples=result['strict_no_new_opposite_examples'],
        cap_only_eligible=own['lifetime_cap_only_eligible_rows'], cap_only_reasons=own['lifetime_cap_only_reason_counts'],
        service=own['actual_service'], first_diagnostic=own['first_two_masks_eligible'],
        two_mask_prefix_rows=own['two_masks_prefix_rows']), allow_nan=False))


if __name__ == '__main__':
    main()
