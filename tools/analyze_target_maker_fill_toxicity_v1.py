from __future__ import annotations

import bisect
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUR_DB = ROOT / 'data' / 'strategy_target_compare_v1.db'
BOOK_DB = ROOT / 'data' / 'wallet_maker_book_inference.db'
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
REPORT = ROOT / 'data' / 'research' / 'target_maker_fill_toxicity_v1_report.json'
VERSION = 'TARGET_MAKER_FILL_TOXICITY_V1'
BASE_PREFIX = 'UNIFIED_CONTROLLER_PAPER_V1'
GRID = 0.01


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro', uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    pos = (len(ys) - 1) * p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos)); w = pos - lo
    return ys[lo] * (1 - w) + ys[hi] * w


def stats(xs: list[float]) -> dict[str, Any]:
    ys = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {
        'n': len(ys),
        'min': min(ys) if ys else None,
        'max': max(ys) if ys else None,
        'mean': statistics.mean(ys) if ys else None,
        'median': statistics.median(ys) if ys else None,
        'p25': q(ys, .25),
        'p75': q(ys, .75),
        'p90': q(ys, .90),
    }


def pv(s: dict[str, Any], camel: str, snake: str) -> float | None:
    x = num(s.get(camel))
    return x if x is not None else num(s.get(snake))


def load_public(con: sqlite3.Connection) -> dict[int, list[tuple[int, dict[str, Any]]]]:
    out: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for r in con.execute('''
        SELECT market_id, decision_ms, source_snapshot_ms, public_state_json
        FROM our_decisions
        WHERE strategy_version LIKE ?
        ORDER BY market_id, decision_ms
    ''', (BASE_PREFIX + '%',)):
        try:
            s = json.loads(str(r['public_state_json']))
        except Exception:
            continue
        if not isinstance(s, dict):
            continue
        ms = int(r['source_snapshot_ms'] or r['decision_ms'])
        out[int(r['market_id'])].append((ms, s))
    return out


def at_or_before(rows: list[tuple[int, dict[str, Any]]], ms: int, max_age_ms: int = 2000) -> tuple[int, dict[str, Any]] | None:
    if not rows:
        return None
    times = [x[0] for x in rows]
    i = bisect.bisect_right(times, ms) - 1
    if i < 0:
        return None
    t, s = rows[i]
    if ms - t > max_age_ms:
        return None
    return t, s


def at_or_after(rows: list[tuple[int, dict[str, Any]]], ms: int, max_delay_ms: int = 2000) -> tuple[int, dict[str, Any]] | None:
    if not rows:
        return None
    times = [x[0] for x in rows]
    i = bisect.bisect_left(times, ms)
    if i >= len(rows):
        return None
    t, s = rows[i]
    if t - ms > max_delay_ms:
        return None
    return t, s


def side_mid(s: dict[str, Any], side: str) -> float | None:
    if side == 'UP':
        return pv(s, 'predictUpMid', 'predict_up_mid')
    return pv(s, 'predictDownMid', 'predict_down_mid')


def side_bid(s: dict[str, Any], side: str) -> float | None:
    if side == 'UP':
        return pv(s, 'predictUpBid', 'predict_up_bid')
    return pv(s, 'predictDownBid', 'predict_down_bid')


def seconds_left(s: dict[str, Any]) -> float | None:
    return pv(s, 'secondsLeft', 'seconds_left')


def time_bucket(sec: float | None) -> str:
    if sec is None: return 'UNKNOWN'
    if sec > 240: return 'T300_240'
    if sec > 180: return 'T240_180'
    if sec > 120: return 'T180_120'
    if sec > 60: return 'T120_60'
    if sec > 30: return 'T60_30'
    if sec > 15: return 'T30_15'
    return 'T15_0'


def absnet_bucket(x: float) -> str:
    if x < 18: return 'NET_0_17'
    if x < 36: return 'NET_18_35'
    if x < 54: return 'NET_36_53'
    if x < 90: return 'NET_54_89'
    return 'NET_90_PLUS'


def toxicity_label(move_ticks: float) -> str:
    if move_ticks <= -1.0:
        return 'TOXIC_1T_PLUS'
    if move_ticks >= 1.0:
        return 'FAVORABLE_1T_PLUS'
    return 'NEUTRAL_LT1T'


def load_parents(con: sqlite3.Connection, markets: set[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not markets:
        return out
    ids = sorted(markets)
    for start in range(0, len(ids), 300):
        batch = ids[start:start+300]
        qs = ','.join('?' for _ in batch)
        for r in con.execute(f'''
            SELECT parent_id,market_id,target_side,target_price,placement_first_ms,last_target_ms,
                   placement_coverage,fill_allocation_coverage,placement_supports_18,confidence
            FROM maker_book_inference_v21_parent_lifecycles
            WHERE market_id IN ({qs})
              AND placement_supports_18=1
              AND placement_coverage>=0.85
              AND fill_allocation_coverage>=0.70
              AND confidence>=0.75
              AND placement_first_ms IS NOT NULL
              AND last_target_ms IS NOT NULL
            ORDER BY market_id,placement_first_ms
        ''', batch):
            out[int(r['market_id'])].append(dict(r))
    return out


def load_maker_fills(con: sqlite3.Connection, markets: set[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not markets:
        return out
    ids = sorted(markets)
    for start in range(0, len(ids), 300):
        batch = ids[start:start+300]
        qs = ','.join('?' for _ in batch)
        for r in con.execute(f'''
            SELECT market_id,event_ms,side,price,shares
            FROM wallet_shadow_target_events
            WHERE market_id IN ({qs}) AND role='MAKER' AND quote_type='BID' AND side IN ('UP','DOWN')
            ORDER BY market_id,event_ms,id
        ''', batch):
            out[int(r['market_id'])].append(dict(r))
    return out


def inventory_before(fills: list[dict[str, Any]], ms: int) -> tuple[float,float]:
    up = down = 0.0
    for f in fills:
        if int(f['event_ms']) >= ms:
            break
        if str(f['side']) == 'UP': up += float(f['shares'])
        else: down += float(f['shares'])
    return up, down


def first_parent_between(parents: list[dict[str, Any]], side: str | None, lo: int, hi: int) -> dict[str, Any] | None:
    for p in parents:
        t = int(p['placement_first_ms'])
        if t <= lo:
            continue
        if t > hi:
            break
        if side is None or str(p['target_side']) == side:
            return p
    return None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [r for r in rows if r.get('eligible')]
    next_rows = [r for r in eligible if r.get('next_same')]
    switch_rows = [r for r in eligible if r.get('next_any_side') and not r.get('next_same')]
    return {
        'n': len(rows),
        'markets': len({int(r['market_id']) for r in rows}),
        'eligible': len(eligible),
        'sameSideContinuationRate': (len(next_rows)/len(eligible)) if eligible else None,
        'pauseSameSideRate': (1-len(next_rows)/len(eligible)) if eligible else None,
        'oppositeFirstAfterHorizonRate': (len(switch_rows)/len(eligible)) if eligible else None,
        'nextSameDelayMs': stats([float(r['next_same_delay_ms']) for r in next_rows]),
        'nextSameOffsetTicks': stats([float(r['next_same_offset_ticks']) for r in next_rows if r.get('next_same_offset_ticks') is not None]),
        'preAbsNetShares': stats([float(r['pre_abs_net']) for r in rows]),
        'markoutTicks': stats([float(r['markout_ticks']) for r in rows]),
    }


def main() -> int:
    our = ro(OUR_DB); book = ro(BOOK_DB); target = ro(TARGET_DB)
    try:
        public = load_public(our)
        markets = set(public)
        parents = load_parents(book, markets)
        fills = load_maker_fills(target, markets)
        all_rows: list[dict[str, Any]] = []

        # Test two strict-past reaction horizons. Outcome starts only after the markout horizon is known.
        for horizon_ms, outcome_hi_ms, label in ((1000, 5000, 'MARKOUT_1S'), (3000, 8000, 'MARKOUT_3S')):
            for m in sorted(markets):
                ps = parents.get(m, [])
                pub = public.get(m, [])
                fs = fills.get(m, [])
                if not ps or not pub or not fs:
                    continue
                for p in ps:
                    t0 = int(p['last_target_ms'])
                    side = str(p['target_side'])
                    before = at_or_before(pub, t0, 2000)
                    after = at_or_after(pub, t0 + horizon_ms, 2000)
                    if before is None or after is None:
                        continue
                    mid0 = side_mid(before[1], side); midh = side_mid(after[1], side)
                    if mid0 is None or midh is None:
                        continue
                    move_ticks = (midh - mid0) / GRID
                    sec = seconds_left(before[1])
                    # Need full outcome window before expiry.
                    eligible = sec is not None and sec * 1000 >= outcome_hi_ms
                    up, down = inventory_before(fs, t0)
                    net = up - down
                    if abs(net) <= 1e-9:
                        inv_role = 'FLAT'
                    else:
                        dominant = 'UP' if net > 0 else 'DOWN'
                        inv_role = 'DOMINANT' if side == dominant else 'MINORITY'
                    next_same = first_parent_between(ps, side, t0 + horizon_ms, t0 + outcome_hi_ms) if eligible else None
                    next_any = first_parent_between(ps, None, t0 + horizon_ms, t0 + outcome_hi_ms) if eligible else None
                    off = None
                    if next_same is not None:
                        ns = at_or_before(pub, int(next_same['placement_first_ms']), 2000)
                        if ns is not None:
                            bid = side_bid(ns[1], side)
                            if bid is not None:
                                off = (bid - float(next_same['target_price'])) / GRID
                    all_rows.append({
                        'market_id': m,
                        'horizon': label,
                        'fill_ms': t0,
                        'side': side,
                        'time_bucket': time_bucket(sec),
                        'pre_up': up,
                        'pre_down': down,
                        'pre_abs_net': abs(net),
                        'absnet_bucket': absnet_bucket(abs(net)),
                        'inventory_role': inv_role,
                        'markout_ticks': move_ticks,
                        'toxicity': toxicity_label(move_ticks),
                        'eligible': bool(eligible),
                        'next_same': next_same is not None,
                        'next_any_side': next_any is not None,
                        'next_same_delay_ms': (int(next_same['placement_first_ms']) - t0) if next_same is not None else None,
                        'next_same_offset_ticks': off,
                    })

        report: dict[str, Any] = {
            'reportVersion': VERSION,
            'researchOnly': True,
            'liveTradingChanges': False,
            'method': {
                'publicSource': 'strategy_target_compare_v1.db our_decisions.public_state_json, <=2s strict-past around Target fill',
                'markout': 'side-token mid move from latest <=fill snapshot to first snapshot at/after +1s or +3s; negative means the Maker-bought token moved against Target',
                'reactionOutcome': 'same-side new high-confidence anchored parent only AFTER markout horizon is observable: (1s,5s] for 1s markout; (3s,8s] for 3s markout',
                'toxicityBins': '<=-1 tick toxic, >=+1 tick favorable, otherwise neutral; descriptive V0, not tuned thresholds',
                'inventory': 'strict-past Target official MAKER BID fills only, before current parent fill',
                'warning': 'anchored parent placement is inferred from public book depth and later Target fill; this is not private order ground truth',
            },
            'coverage': {
                'publicMarkets': len(public),
                'parentMarkets': len(parents),
                'rows': len(all_rows),
            },
            'byHorizon': {},
        }
        for h in ('MARKOUT_1S','MARKOUT_3S'):
            hr = [r for r in all_rows if r['horizon']==h]
            block: dict[str, Any] = {'all': summarize(hr), 'byInventoryRole': {}, 'dominantByToxicity': {}, 'minorityByToxicity': {}, 'dominantByAbsNetAndToxicity': {}, 'dominantByTimeAndToxicity': {}}
            for role in ('DOMINANT','MINORITY','FLAT'):
                block['byInventoryRole'][role] = summarize([r for r in hr if r['inventory_role']==role])
            for tox in ('TOXIC_1T_PLUS','NEUTRAL_LT1T','FAVORABLE_1T_PLUS'):
                block['dominantByToxicity'][tox] = summarize([r for r in hr if r['inventory_role']=='DOMINANT' and r['toxicity']==tox])
                block['minorityByToxicity'][tox] = summarize([r for r in hr if r['inventory_role']=='MINORITY' and r['toxicity']==tox])
            for b in ('NET_0_17','NET_18_35','NET_36_53','NET_54_89','NET_90_PLUS'):
                block['dominantByAbsNetAndToxicity'][b] = {tox:summarize([r for r in hr if r['inventory_role']=='DOMINANT' and r['absnet_bucket']==b and r['toxicity']==tox]) for tox in ('TOXIC_1T_PLUS','NEUTRAL_LT1T','FAVORABLE_1T_PLUS')}
            for tb in ('T300_240','T240_180','T180_120','T120_60','T60_30','T30_15','T15_0'):
                block['dominantByTimeAndToxicity'][tb] = {tox:summarize([r for r in hr if r['inventory_role']=='DOMINANT' and r['time_bucket']==tb and r['toxicity']==tox]) for tox in ('TOXIC_1T_PLUS','NEUTRAL_LT1T','FAVORABLE_1T_PLUS')}
            report['byHorizon'][h] = block
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        our.close(); book.close(); target.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
