"""Read-only size audit of the eight already-consumed Target market inputs.

Separate observed fill legs from cumulative fills under an observed order hash.
Neither identifies unfilled original order quantity or terminal order status.
No training, native replay, current collector reads, or runtime changes.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / 'data/research'
STEM = 'BTC5M_TARGET_FILL_SIZE_AUDIT_V1_20260913'
MIDS = (2022527, 2022538, 2022602, 2023438, 2026085, 2026817, 2028352, 2029246)
EPS = 1e-8
TZ = timezone(timedelta(hours=8))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b):
    return abs(a - b) <= EPS


def read(path):
    if path.suffix == '.gz':
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        assert len(raw) <= 16 * 1024 * 1024
        return json.loads(raw)
    return json.loads(path.read_text(encoding='utf-8'))


def key(a):
    return (a['market_id'], a['role'], a['side'], a['quote_type'], a['order_hash'])


def size_stats(rows):
    quantities = [a['shares'] for a in rows]
    total = math.fsum(quantities)
    counts = Counter(round(q, 6) for q in quantities)
    return dict(
        count=len(rows), shares=total, min=min(quantities), max=max(quantities),
        top_sizes_rounded_6dp=[dict(shares=q, count=n, fraction=n / len(rows))
                               for q, n in counts.most_common(10)],
        fixed_sizes={str(q): dict(
            count=sum(close(x, q) for x in quantities),
            count_fraction=sum(close(x, q) for x in quantities) / len(rows),
            volume_fraction=math.fsum(x for x in quantities if close(x, q)) / total,
            count_at_tolerance_1e6=sum(abs(x - q) <= 1e-6 for x in quantities),
        ) for q in (15, 18, 30)},
        below_15=sum(x < 15 - EPS for x in quantities),
        above_15=sum(x > 15 + EPS for x in quantities),
    )


def parent_rows(actions):
    grouped = defaultdict(list)
    for action in actions:
        grouped[key(action)].append(action)
    parents = []
    for (mid, role, side, quote, order_hash), legs in grouped.items():
        shares = math.fsum(a['shares'] for a in legs)
        decimal_sum = sum((Decimal(str(a['shares'])) for a in legs), Decimal(0))
        assert close(shares, float(decimal_sum))
        parents.append(dict(market_id=mid, role=role, side=side, quote_type=quote,
                            order_hash=order_hash, shares=shares, fill_legs=len(legs),
                            cash=math.fsum(a['shares'] * a['price'] for a in legs),
                            first_event_ms=min(a['event_ms'] for a in legs),
                            last_event_ms=max(a['event_ms'] for a in legs),
                            prices=sorted({a['price'] for a in legs}),
                            leg_shares=[a['shares'] for a in legs],
                            source_leg_ids=[a['source_leg_id'] for a in legs]))
    return parents


def summarize(actions, parents):
    summary = {}
    for role in ('MAKER', 'TAKER'):
        legs = [a for a in actions if a['role'] == role]
        grouped = [a for a in parents if a['role'] == role]
        full15 = [p for p in grouped if close(p['shares'], 15)]
        full15_keys = {key(p) for p in full15}
        contributing_legs = sum(p['fill_legs'] for p in full15)
        assert close(math.fsum(a['shares'] for a in legs), math.fsum(p['shares'] for p in grouped))
        summary[role] = dict(
            fill_legs=size_stats(legs), observed_order_totals=size_stats(grouped),
            fixed15_total_with_multiple_fills=sum(p['fill_legs'] > 1 for p in full15),
            legs_belonging_to_observed15_total=contributing_legs,
            fraction_legs_belonging_to_observed15_total=contributing_legs / len(legs),
            fill_notional_below_one=sum(a['shares'] * a['price'] < 1 - EPS for a in legs),
            below_one_legs_in_observed15_total=sum(a['shares'] * a['price'] < 1 - EPS
                                                  and key(a) in full15_keys for a in legs),
            min_fill_price=min(a['price'] for a in legs),
            max_fill_price=max(a['price'] for a in legs),
            observed_orders_with_multiple_prices=sum(len(p['prices']) > 1 for p in grouped),
            by_side={side: dict(fill_legs=size_stats([a for a in legs if a['side'] == side]),
                               observed_order_totals=size_stats([p for p in grouped if p['side'] == side]))
                     for side in ('UP', 'DOWN')},
        )
    return summary


def main():
    started = time.perf_counter()
    previous_path = RESEARCH / 'BTC5M_EXPOSURE_SUPPRESSION_METRIC_V1_20260913.json'
    previous = read(previous_path)['target_markets']
    actions, parents, markets = [], [], {}
    for mid in MIDS:
        folder = ('open_funding_recovery_train_20260911_v3' if mid in MIDS[:3]
                  else 'v20_consumed_btc5_transfer5_20260912_v1')
        path = ROOT / '.lan_worker_v1' / folder / f'input_{mid}.json.gz'
        digest = sha(path)
        assert digest == previous[str(mid)]['source_sha256']
        source = read(path)
        aa = source['targetActions']
        assert source['originalOrderQuantity'] is None
        assert len(aa) == source['market']['target_actions']
        assert len({a['source_leg_id'] for a in aa}) == len(aa)
        assert all(a['market_id'] == mid and a['role'] in ('MAKER', 'TAKER')
                   and a['side'] in ('UP', 'DOWN') and a['quote_type'] == 'BID'
                   and a['order_hash'] and 0 < a['price'] < 1 and a['shares'] > 0
                   for a in aa)
        pp = parent_rows(aa)
        frozen_parents = {key(p): p for p in source['targetParents']}
        assert len(frozen_parents) == len(source['targetParents']) == len(pp)
        for p in pp:
            frozen = frozen_parents[key(p)]
            assert close(p['shares'], frozen['shares'])
            assert close(p['cash'] / p['shares'], frozen['average_price'])
            assert all(p[f] == frozen[f] for f in ('fill_legs', 'first_event_ms', 'last_event_ms'))
        markets[str(mid)] = dict(
            source=path.relative_to(ROOT).as_posix(), source_sha256=digest,
            start_local=datetime.fromtimestamp(source['market']['window_start_ms'] / 1000, TZ).isoformat(),
            end_local=datetime.fromtimestamp(source['market']['window_end_ms'] / 1000, TZ).isoformat(),
            routes=summarize(aa, pp),
        )
        actions.extend(aa)
        parents.extend(pp)

    # Match only the already-consumed markets to their original exported wallet
    # records. Other market payloads are neither decoded nor analyzed.
    raw_path = RESEARCH / 'market_capsule_v1/source_bundle_50_v1/target_actions.jsonl'
    originals = {}
    with raw_path.open('rb') as stream:
        for line in stream:
            match = re.search(rb'"market_id"\s*:\s*(\d+)', line)
            assert match
            if int(match[1]) not in MIDS:
                continue
            row = json.loads(line)
            assert row['source_leg_id'] not in originals
            originals[row['source_leg_id']] = row
    assert len({a['source_leg_id'] for a in actions}) == len(actions) == len(originals)
    assert all(a == {k: originals[a['source_leg_id']][k] for k in a} for a in actions)
    normalized = json.dumps([originals[k] for k in sorted(originals)], sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode()
    results = dict(
        version=STEM, status='COMPLETE', verification='PASS',
        scope='Eight already-consumed BTC5M markets; observed Target BUY fills only',
        grouping=['market_id', 'role', 'side', 'quote_type', 'order_hash'],
        quantity_tolerance=EPS, histogram_rounding_decimals=6,
        reference_quantities_checked=[15, 18, 30],
        candidate_note='18 and 30 user-supplied; 15 discovered as the sample mode before this descriptive audit',
        aggregate=summarize(actions, parents), markets=markets,
        observed_order_rows=parents,
        provenance=dict(
            prior_sha_registry=previous_path.relative_to(ROOT).as_posix(),
            prior_sha_registry_sha256=sha(previous_path),
            original_export=raw_path.relative_to(ROOT).as_posix(),
            selected_original_rows_sha256=hashlib.sha256(normalized).hexdigest(),
            matching_original_rows=len(actions),
            all_input_fields_match_original_export=True,
            quantity_scaling_between_export_and_input=False,
            script_sha256=sha(Path(__file__)),
        ),
        verification_details=dict(
            frozen_source_hashes_all8=True, unique_fill_ids=True,
            order_total_quantity_count_price_and_times_match_frozen_targetParents=True,
            independent_decimal_sum=True, total_volume_preserved=True,
        ),
        limitations=[
            'An individual fill leg is not necessarily the original submitted quantity.',
            'Order-hash totals include only observed fills, not unfilled or cancelled remainder.',
            'All sampled markets date to 2026-09-07; current 18/30 regimes are not tested.',
            'Raw-export parity rules out research-package rescaling, not an upstream collector defect.',
            'No original order NEW/terminal messages: fixed15 is strongly supported, not proven universal.',
            'MAKER/TAKER are observed execution roles, not inferred hidden strategy intent.',
            'No new markets, independent validation, parameter fit, or causal claim.',
        ],
        native_jobs=0, model_fits=0, runtime_changes=False,
        elapsed_seconds=time.perf_counter() - started,
    )
    for item in [results['aggregate'], *(m['routes'] for m in markets.values())]:
        for role in ('MAKER', 'TAKER'):
            for unit in ('fill_legs', 'observed_order_totals'):
                assert all(v['count'] == v['count_at_tolerance_1e6']
                           for v in item[role][unit]['fixed_sizes'].values())
    (RESEARCH / (STEM + '.json')).write_text(
        json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    def brief(routes):
        return {role: {unit: dict(total=routes[role][unit]['count'],
                                  exact15=routes[role][unit]['fixed_sizes']['15']['count'],
                                  fraction15=routes[role][unit]['fixed_sizes']['15']['count_fraction'])
                       for unit in ('fill_legs', 'observed_order_totals')}
                for role in ('MAKER', 'TAKER')}
    print(json.dumps(dict(status=results['status'], aggregate=brief(results['aggregate']),
                         primary_market=brief(markets['2026085']['routes']),
                         elapsed_seconds=results['elapsed_seconds']), indent=2))


if __name__ == '__main__':
    main()
