"""Read-only recurrence and path audit of the supplied outcome-selected cases.

All 508 terminal records are checked; eight BTC paths are rebuilt from indexed
SQLite fill queries. No CSV mutation, fitting, runtime policy, or native jobs.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from audit_btc5m_post_exposure_response_v1 import reconstruct, sum_flow
from btc5m_exposure_suppression_metrics_v1 import geometry

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'data/research/target_casepacks_v1'
SOURCE = DIR / 'TARGET_ONE_NORMAL_ONE_SMALL_OUTCOME_ALL_V1_20260913.json'
OUT = DIR / 'TARGET_NORMAL_SMALL_OUTCOME_PATH_AUDIT_V1_20260913.json'
DB = ROOT / 'data/target_wallet_official_v1.db'
# Descriptive, outcome-selected sample fixed before reconstructing these paths.
# Covers both orientations, loss/gain, and winners on either payoff branch.
MIDS = (2084104, 1977248, 1963934, 1884370, 1942969, 1941817, 1760051, 2059306)
SIDES = ('UP', 'DOWN')
ROUTES = ('MAKER', 'TAKER')
POP_SQL = """select market_id,asset,winner,buy_notional_usdt,sell_proceeds_usdt,
up_position_shares,down_position_shares from target_market_results
where winner in ('UP','DOWN') order by market_id"""
FILL_SQL = """select id,role,side,quote_type,event_ms,price,shares
from wallet_shadow_target_events where asset=? and market_id=? order by event_ms,id"""


def sha_file(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha_rows(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def close(a, b):
    assert math.isfinite(a) and math.isfinite(b) and abs(a - b) < 1e-7, (a, b)


def side_set(row):
    return {s for s in SIDES if sum(row['flow'][s][r]['qty'] for r in ROUTES) > 0}


def inspect_path(db, case):
    mid = case['marketId']
    src = [dict(r) for r in db.execute(FILL_SQL, ('BTC', mid))]
    end = db.execute('select window_end_ms from target_markets where market_id=?', (mid,)).fetchone()[0]
    start = end - 300000
    assert len(src) == case['fillCount'] and len({r['id'] for r in src}) == len(src)
    assert all(r['quote_type'] == 'BID' and start <= r['event_ms'] < end
               and r['event_ms'] % 1000 == 0 and 0 < r['price'] < 1 and r['shares'] > 0 for r in src)
    legs = [dict(t=r['event_ms'], side=r['side'], route=r['role'], qty=r['shares'],
                 cash=r['shares'] * r['price']) for r in src]
    rows = reconstruct(legs)
    assert rows == reconstruct(list(reversed(legs)))
    last = rows[-1]
    for field, original in [('up', 'pnlIfUp'), ('down', 'pnlIfDown'), ('cost', 'buyNotionalUsdt'),
                            ('inventory_up', 'upPositionShares'), ('inventory_down', 'downPositionShares')]:
        close(last['geometry'][field], case[original])
    flow = sum_flow(rows)
    for route, original in [('MAKER', 'makerNetPnlUsdt'), ('TAKER', 'takerNetPnlUsdt')]:
        cost = sum(flow[s][route]['cash'] for s in SIDES)
        close(flow[case['winner']][route]['qty'] - cost, case[original])
    small, normal = case['smallSide'], case['normalSide']
    # Outcome labels describe endpoints only. This is not a causal selector or actor.
    before = last['before']['geometry']
    final_sides = side_set(last)
    if final_sides == {small} and before[small.lower()] < 0:
        mechanism = 'LAST_BUCKET_REPAIRS_SMALL_BRANCH_FROM_NEGATIVE'
    elif final_sides == {normal} and before['floor'] > 0:
        mechanism = 'LAST_BUCKET_SPENDS_POSITIVE_PROTECTION_ON_NORMAL_BRANCH'
    else:
        mechanism = 'OTHER_OR_MIXED'
    first_run = len(rows) - 1
    if len(final_sides) == 1:
        while first_run > 0 and side_set(rows[first_run - 1]) == final_sides:
            first_run -= 1
    run = rows[first_run:]
    run_before = run[0]['before']['geometry']
    run_flow = sum_flow(run)
    delta_cash = sum(run_flow[s][r]['cash'] for s in SIDES for r in ROUTES)
    qty = {s: sum(run_flow[s][r]['qty'] for r in ROUTES) for s in SIDES}
    changes = {s: qty[s] - delta_cash for s in SIDES}
    for side in SIDES:
        close(last['geometry'][side.lower()] - run_before[side.lower()], changes[side])
    return dict(market_id=mid, validation='PASS', source_rows_sha256=sha_rows(src),
        recorded_fill_legs=len(src), event_second_buckets=len(rows),
        terminal=case, last_bucket_mechanism=mechanism,
        last_fill_seconds=(last['t'] - start) / 1000,
        no_further_observed_fills_seconds=(end - last['t']) / 1000,
        last_bucket_before=before, last_bucket_after=last['geometry'], last_bucket_flow=last['flow'],
        last_four_buckets=[dict(seconds=(r['t'] - start) / 1000, geometry=r['geometry'],
                               before=r['before']['geometry'], flow=r['flow']) for r in rows[-4:]],
        final_one_sided_run=dict(first_fill_seconds=(run[0]['t'] - start) / 1000,
            last_fill_seconds=(last['t'] - start) / 1000, before=run_before,
            after=last['geometry'], delta_payoff=changes, cost=delta_cash, qty=qty, flow=run_flow),
        curve=[dict(seconds=(r['t'] - start) / 1000, geometry=r['geometry'], flow=r['flow']) for r in rows])


def main():
    started = time.perf_counter()
    input_hash = sha_file(SOURCE)
    supplied = json.loads(SOURCE.read_text(encoding='utf-8-sig'))
    cases = supplied['smallLossCases'] + supplied['smallGainCases']
    assert len(cases) == 508 and len({(c['asset'], c['marketId']) for c in cases}) == 508
    assert len(supplied['smallLossCases']) == supplied['summary']['smallLoss'] == 265
    assert len(supplied['smallGainCases']) == supplied['summary']['smallGain'] == 243
    asset_counts = collections.defaultdict(collections.Counter)
    for c in cases:
        cash = c['sellProceedsUsdt'] - c['buyNotionalUsdt']
        pu, pd = c['upPositionShares'] + cash, c['downPositionShares'] + cash
        close(pu, c['pnlIfUp']); close(pd, c['pnlIfDown'])
        close(max(pu, pd), c['normalPnl']); close(min(pu, pd), c['smallPnl'])
        assert c['normalSide'] == ('UP' if pu > pd else 'DOWN')
        assert c['smallSide'] != c['normalSide'] and c['smallSide'] in SIDES
        assert c['normalPnl'] > 2 and c['sellProceedsUsdt'] == 0
        assert c['accountingVersion'] == 'FILLED_CASHFLOW_PLUS_WINNER_V1_NO_EXPLICIT_FEE'
        if c['caseType'] == 'NORMAL_PLUS_SMALL_LOSS':
            assert -1 < c['smallPnl'] < 0
            asset_counts[c['asset']]['smallLoss'] += 1
        else:
            assert c['caseType'] == 'NORMAL_PLUS_SMALL_GAIN' and 0 < c['smallPnl'] <= 1
            asset_counts[c['asset']]['smallGain'] += 1
        payoff = dict(UP=pu, DOWN=pd)
        close(payoff[c['winner']], c['actualWinnerPnl'])
        close(payoff['DOWN' if c['winner'] == 'UP' else 'UP'], c['oppositeWinnerPnl'])
        close(c['makerNetPnlUsdt'] + c['takerNetPnlUsdt'], c['actualWinnerPnl'])
    assert dict(asset_counts) == supplied['summary']['byAsset']
    btc = [c for c in cases if c['asset'] == 'BTC']
    by_mid = {c['marketId']: c for c in btc}
    with sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        pop = [dict(r) for r in db.execute(POP_SQL)]
        by_key = {(r['asset'], r['market_id']): r for r in pop}
        for c in cases:
            r = by_key[c['asset'], c['marketId']]
            assert r['winner'] == c['winner']
            for raw, export in [('buy_notional_usdt', 'buyNotionalUsdt'),
                                ('sell_proceeds_usdt', 'sellProceedsUsdt'),
                                ('up_position_shares', 'upPositionShares'), ('down_position_shares', 'downPositionShares')]:
                close(r[raw], c[export])
        btc_all = [r for r in pop if r['asset'] == 'BTC']
        eligible = []
        for r in btc_all:
            cash = r['sell_proceeds_usdt'] - r['buy_notional_usdt']
            pu, pd = r['up_position_shares'] + cash, r['down_position_shares'] + cash
            if max(pu, pd) > 2:
                eligible.append(dict(market_id=r['market_id'], floor=min(pu, pd), best=max(pu, pd)))
        population_selected = {r['market_id'] for r in eligible if -1 < r['floor'] <= 1 and r['floor'] != 0}
        assert population_selected == set(by_mid)
        hist = [dict(lower_exclusive=a, upper_inclusive=a + 1,
                     count=sum(a < r['floor'] <= a + 1 for r in eligible)) for a in range(-5, 5)]
        paths = [inspect_path(db, by_mid[mid]) for mid in MIDS]
    out = dict(version='TARGET_NORMAL_SMALL_OUTCOME_PATH_AUDIT_V1', status='COMPLETE', runtime_eligible=False,
        source=str(SOURCE.relative_to(ROOT)), source_sha256=input_hash,
        script_sha256=sha_file(Path(__file__)), source_generated_utc=supplied['generatedUtc'],
        dependency_sha256={p: sha_file(ROOT / 'tools' / p) for p in
                           ('audit_btc5m_post_exposure_response_v1.py', 'btc5m_exposure_suppression_metrics_v1.py')},
        source_thresholds=supplied['thresholds'], source_summary=supplied['summary'],
        validation=dict(terminal_accounting_records=508, terminal_db_matches=508, btc_summary_records=37,
            raw_path_markets=len(paths), raw_fill_legs=sum(p['recorded_fill_legs'] for p in paths),
            event_second_buckets=sum(p['event_second_buckets'] for p in paths), all_checks='PASS',
            source_unchanged=sha_file(SOURCE) == input_hash),
        database_snapshot=dict(mode='mode=ro plus query_only plus one read transaction',
            path=str(DB.relative_to(ROOT)), population_query=POP_SQL, fill_query=FILL_SQL,
            population_rows_sha256=sha_rows(pop), all_settled=len(pop), btc_settled=len(btc_all),
            btc_best_gt_2=len(eligible), btc_selected=37, btc_selected_over_all=len(btc) / len(btc_all),
            floor_histogram=hist, exact_zero=sum(r['floor'] == 0 for r in eligible),
            population_selected_btc_matches_export=True),
        sample_selection=dict(market_ids=list(MIDS), purpose='Descriptive mechanism contrasts; not random or held out.',
            criteria='Both normal orientations; four small-loss and four small-gain cases, including small-side winners.',
            unknowns='Unfilled orders, decision/send/receipt timing, contemporaneous executable depth, fees.'),
        constraints=dict(normal_side='Terminal higher-payoff label, not identified intention or settlement winner.',
            timing='Second-level event aggregates, no assumed order within a second.',
            observed_stop='Last recorded fill does not establish order cancellation or controller stopping.',
            minimum='Fill notional and partial quantity do not identify NEW order size or violate a minimum.',
            inference='Outcome-selected recurrence cannot by itself identify a unique controller or prove a zero target.',
            bins='Descriptive equal-width neighboring bins, not a statistical test or scale-matched counterfactual.',
            execution='No fitting, parameter sweep, native run, live mutation, or worker dispatch.'),
        btc_terminal_records=btc, path_mechanism_counts=dict(collections.Counter(p['last_bucket_mechanism'] for p in paths)),
        paths=paths, elapsed_seconds=time.perf_counter() - started)
    assert out['validation']['source_unchanged']
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(OUT.relative_to(ROOT)), validation=out['validation'],
        path_mechanism_counts=out['path_mechanism_counts'], elapsed_seconds=out['elapsed_seconds'],
        population={k: out['database_snapshot'][k] for k in ('all_settled', 'btc_settled', 'btc_best_gt_2', 'floor_histogram')})))


if __name__ == '__main__':
    main()
