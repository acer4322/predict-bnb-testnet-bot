from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
CANONICAL = ROOT / 'data' / 'research' / 'execution_aware_fill_lifecycle_v0' / 'pair_completion_canonical_cohort_v1.json'
EXAM_REGISTRY = ROOT / 'data' / 'research' / 'execution_aware_fill_lifecycle_v0' / 'r2_execution_graduation_exam_registry_v1.json'
OUT_DIR = ROOT / 'data' / 'research' / 'execution_aware_fill_lifecycle_v0'
EPS = 1e-9
BURST_GAP_MS = 5000


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('pragma query_only=on')
    return c


def signed(side: str, quote_type: str, shares: float) -> tuple[float, float]:
    q = float(shares) if quote_type == 'BID' else -float(shares)
    return (q, 0.0) if side == 'UP' else (0.0, q)


def directional(side: str, quote_type: str, shares: float) -> float:
    up, down = signed(side, quote_type, shares)
    return up - down


def phase(seconds_left: float) -> str:
    if seconds_left > 240: return 'EARLY'
    if seconds_left > 60: return 'MID'
    return 'TAIL'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--asset', default='BTC')
    ap.add_argument('--burst-gap-ms', type=int, default=BURST_GAP_MS)
    ap.add_argument('--output-prefix', default='target_repair_responsibility_teacher_v0')
    args = ap.parse_args()
    asset = args.asset.upper()

    canonical = json.loads(CANONICAL.read_text(encoding='utf-8'))
    canonical_ids = set(int(x) for x in canonical['marketIds'])
    registry = json.loads(EXAM_REGISTRY.read_text(encoding='utf-8'))
    exam_ids = [int(x['marketId']) for x in registry.get('examMarkets', []) if x.get('scoreStatus') == 'SCORED']
    exam_set = set(exam_ids)
    wanted = canonical_ids | exam_set

    db = ro(TARGET_DB)
    try:
        market_meta = {
            int(r['market_id']): dict(r)
            for r in db.execute(
                'select market_id,window_end_ms,first_seen_ms,status,winner from target_markets where asset=?',
                (asset,),
            ) if int(r['market_id']) in wanted
        }
        results = {
            int(r['market_id']): dict(r)
            for r in db.execute(
                'select market_id,net_pnl_usdt,maker_net_pnl_usdt,taker_net_pnl_usdt from target_market_results where asset=?',
                (asset,),
            ) if int(r['market_id']) in wanted
        }
        rows = [dict(r) for r in db.execute(
            '''select parent_id,market_id,role,side,quote_type,first_event_ms,last_event_ms,
                      average_price,shares,fill_legs
                 from target_parent_orders
                where asset=? and market_id in (%s) and role in ('MAKER','TAKER')
                order by market_id,first_event_ms,last_event_ms,parent_id'''
                % ','.join('?' for _ in wanted),
            [asset, *sorted(wanted)],
        )] if wanted else []
    finally:
        db.close()

    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows: by_market[int(r['market_id'])].append(r)

    event_rows: list[dict[str, Any]] = []
    market_summaries: list[dict[str, Any]] = []
    for mid in sorted(wanted):
        parents = by_market.get(mid, [])
        meta = market_meta.get(mid)
        if not parents or not meta or meta.get('window_end_ms') is None:
            market_summaries.append({'marketId': mid, 'cohort': 'FORMAL10' if mid in exam_set else 'CANONICAL149', 'coverage': 'NO_OFFICIAL_PARENTS_OR_MARKET_META'})
            continue
        end_ms = int(meta['window_end_ms']); open_ms = end_ms - 300_000
        maker_up = maker_down = taker_up = taker_down = 0.0
        prior_maker_parents = 0; prior_taker_parents = 0
        prior_maker_sizes: list[float] = []; prior_maker_directional: list[float] = []
        last_maker_event_ms = None
        mid_events: list[dict[str, Any]] = []
        for p in parents:
            role = str(p['role']); side = str(p['side']); qt = str(p['quote_type']); sh = float(p['shares'])
            at = int(p['first_event_ms']); up_eff, down_eff = signed(side, qt, sh)
            maker_delta = maker_up - maker_down
            combined_delta = (maker_up + taker_up) - (maker_down + taker_down)
            before_abs = abs(combined_delta)
            effect = directional(side, qt, sh)
            after_delta = combined_delta + effect if role == 'TAKER' else combined_delta
            reduction = before_abs - abs(after_delta) if role == 'TAKER' else 0.0
            opposes_maker = abs(maker_delta) > EPS and abs(effect) > EPS and ((maker_delta > 0) != (effect > 0))
            has_prior_maker = prior_maker_parents > 0
            seconds_from_open = (at - open_ms) / 1000.0
            seconds_left = (end_ms - at) / 1000.0
            if role == 'TAKER':
                maker_gross_before = abs(maker_up) + abs(maker_down)
                typical_maker_parent = statistics.median(prior_maker_sizes) if prior_maker_sizes else None
                worsening_streak = 0
                if abs(combined_delta) > EPS:
                    for eff in reversed(prior_maker_directional):
                        if abs(eff) <= EPS: break
                        if (eff > 0) == (combined_delta > 0): worsening_streak += 1
                        else: break
                if not has_prior_maker:
                    responsibility = 'OPENING_OR_NO_PRIOR_MAKER'
                elif opposes_maker and reduction > EPS:
                    responsibility = 'REPAIR_BALANCE'
                elif opposes_maker:
                    responsibility = 'MAKER_OFFSET_WITHOUT_NET_REDUCTION'
                elif reduction > EPS:
                    responsibility = 'BALANCE_NOT_MAKER_OPPOSE'
                else:
                    responsibility = 'ADD_OR_OTHER'
                row = {
                    'market_id': mid,
                    'cohort': 'FORMAL10' if mid in exam_set else 'CANONICAL149',
                    'parent_id': p['parent_id'],
                    'first_event_ms': at,
                    'last_event_ms': int(p['last_event_ms']),
                    'seconds_from_open': seconds_from_open,
                    'seconds_left': seconds_left,
                    'phase': phase(seconds_left),
                    'side': side,
                    'quote_type': qt,
                    'shares': sh,
                    'average_price': float(p['average_price']),
                    'fill_legs': int(p['fill_legs']),
                    'has_prior_maker_fill': int(has_prior_maker),
                    'prior_maker_parent_count': prior_maker_parents,
                    'prior_taker_parent_count': prior_taker_parents,
                    'ms_since_last_maker_parent': (at - last_maker_event_ms) if last_maker_event_ms is not None else None,
                    'maker_delta_before': maker_delta,
                    'combined_delta_before': combined_delta,
                    'combined_abs_before': before_abs,
                    'maker_gross_before': maker_gross_before,
                    'typical_prior_maker_parent_shares': typical_maker_parent,
                    'imbalance_to_maker_gross': (before_abs / maker_gross_before) if maker_gross_before > EPS else None,
                    'imbalance_in_maker_parent_units': (before_abs / typical_maker_parent) if typical_maker_parent is not None and typical_maker_parent > EPS else None,
                    'worsening_maker_parent_streak': worsening_streak,
                    'parent_directional_effect': effect,
                    'combined_delta_counterfactual_after': after_delta,
                    'combined_abs_reduction': reduction,
                    'opposes_maker_heavy': int(opposes_maker),
                    'responsibility': responsibility,
                    'repair_fraction_of_prior_abs': min(1.0, max(0.0, reduction) / before_abs) if before_abs > EPS else 0.0,
                }
                mid_events.append(row); event_rows.append(row)
                prior_taker_parents += 1
                taker_up += up_eff; taker_down += down_eff
            else:
                prior_maker_parents += 1
                prior_maker_sizes.append(sh); prior_maker_directional.append(directional(side, qt, sh))
                maker_up += up_eff; maker_down += down_eff
                last_maker_event_ms = at

        repair = [x for x in mid_events if x['responsibility'] == 'REPAIR_BALANCE']
        bursts = []
        current = None
        for r in repair:
            if current is None or r['side'] != current['side'] or int(r['first_event_ms']) - int(current['last_event_ms']) > int(args.burst_gap_ms):
                if current is not None: bursts.append(current)
                current = {
                    'marketId': mid, 'side': r['side'], 'first_event_ms': r['first_event_ms'], 'last_event_ms': r['last_event_ms'],
                    'onset_seconds_from_open': r['seconds_from_open'], 'onset_seconds_left': r['seconds_left'], 'phase': r['phase'],
                    'onset_maker_delta': r['maker_delta_before'], 'onset_combined_delta': r['combined_delta_before'],
                    'onset_combined_abs': r['combined_abs_before'], 'onset_maker_gross': r['maker_gross_before'],
                    'onset_typical_maker_parent_shares': r['typical_prior_maker_parent_shares'],
                    'onset_imbalance_to_maker_gross': r['imbalance_to_maker_gross'],
                    'onset_imbalance_in_maker_parent_units': r['imbalance_in_maker_parent_units'],
                    'onset_worsening_maker_parent_streak': r['worsening_maker_parent_streak'], 'parentCount': 1, 'shares': r['shares'],
                    'combinedAbsReductionSum': r['combined_abs_reduction'], 'weightedPriceNumerator': r['shares'] * r['average_price'],
                }
            else:
                current['last_event_ms'] = r['last_event_ms']; current['parentCount'] += 1; current['shares'] += r['shares']; current['combinedAbsReductionSum'] += r['combined_abs_reduction']; current['weightedPriceNumerator'] += r['shares'] * r['average_price']
        if current is not None: bursts.append(current)
        for b in bursts:
            b['average_price'] = b.pop('weightedPriceNumerator') / b['shares'] if b['shares'] > EPS else None
        res = results.get(mid, {})
        market_summaries.append({
            'marketId': mid,
            'cohort': 'FORMAL10' if mid in exam_set else 'CANONICAL149',
            'coverage': 'OK',
            'makerParents': sum(1 for p in parents if p['role'] == 'MAKER'),
            'takerParents': sum(1 for p in parents if p['role'] == 'TAKER'),
            'repairParents': len(repair),
            'repairBursts': len(bursts),
            'repairBurstDetails': bursts,
            'targetPnlDiagnostic': res.get('net_pnl_usdt'),
            'targetMakerPnlDiagnostic': res.get('maker_net_pnl_usdt'),
            'targetTakerPnlDiagnostic': res.get('taker_net_pnl_usdt'),
        })

    repair_rows = [r for r in event_rows if r['responsibility'] == 'REPAIR_BALANCE']
    def cohort_summary(name: str, ids: set[int]) -> dict[str, Any]:
        es = [r for r in event_rows if int(r['market_id']) in ids]
        rs = [r for r in es if r['responsibility'] == 'REPAIR_BALANCE']
        ms = [r for r in market_summaries if int(r['marketId']) in ids and r.get('coverage') == 'OK']
        bursts = [b for m in ms for b in m.get('repairBurstDetails', [])]
        return {
            'marketsRequested': len(ids), 'marketsWithOfficialCoverage': len(ms), 'takerParents': len(es),
            'responsibilityCounts': dict(Counter(r['responsibility'] for r in es)),
            'repairParents': len(rs), 'repairMarkets': len({int(r['market_id']) for r in rs}),
            'repairBursts': len(bursts), 'repairBurstMarkets': len({int(b['marketId']) for b in bursts}),
            'repairParentShareTotal': sum(float(r['shares']) for r in rs),
            'repairReductionTotal': sum(float(r['combined_abs_reduction']) for r in rs),
            'repairPhaseCounts': dict(Counter(r['phase'] for r in rs)),
            'burstOnsetAbsMedian': sorted(abs(float(b['onset_combined_delta'])) for b in bursts)[len(bursts)//2] if bursts else None,
            'burstOnsetAbsMean': sum(abs(float(b['onset_combined_delta'])) for b in bursts)/len(bursts) if bursts else None,
            'burstSharesMean': sum(float(b['shares']) for b in bursts)/len(bursts) if bursts else None,
            'burstOnsetImbalanceToMakerGrossMedian': statistics.median([float(b['onset_imbalance_to_maker_gross']) for b in bursts if b.get('onset_imbalance_to_maker_gross') is not None]) if any(b.get('onset_imbalance_to_maker_gross') is not None for b in bursts) else None,
            'burstOnsetMakerParentUnitsMedian': statistics.median([float(b['onset_imbalance_in_maker_parent_units']) for b in bursts if b.get('onset_imbalance_in_maker_parent_units') is not None]) if any(b.get('onset_imbalance_in_maker_parent_units') is not None for b in bursts) else None,
            'burstOnsetWorseningStreakMedian': statistics.median([float(b['onset_worsening_maker_parent_streak']) for b in bursts]) if bursts else None,
        }

    report = {
        'version': 'TARGET_REPAIR_RESPONSIBILITY_TEACHER_V0',
        'researchOnly': True,
        'runtimeLeakageGuard': 'Labels use only Target fill chronology and signed inventory state at/before each Taker parent. winner/PnL are diagnostic fields only and never define REPAIR_BALANCE.',
        'definition': {
            'repairBalance': 'has prior Maker parent AND Taker directional effect opposes current Maker-heavy delta AND counterfactually reduces current combined absolute delta',
            'burst': f'consecutive REPAIR_BALANCE Taker parents on same side with <= {int(args.burst_gap_ms)} ms gap',
            'purpose': 'Identify Target execution-responsibility transitions, especially repair-burst onset, without conflating opening directional Taker with inventory repair.',
        },
        'canonical149': cohort_summary('CANONICAL149', canonical_ids),
        'formal10': cohort_summary('FORMAL10', exam_set),
        'formal10MarketDiagnostics': [r for r in market_summaries if int(r['marketId']) in exam_set],
        'coverageWarnings': [r for r in market_summaries if r.get('coverage') != 'OK'],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / f'{args.output_prefix}.csv'
    json_path = OUT_DIR / f'{args.output_prefix}_report.json'
    fields = list(event_rows[0].keys()) if event_rows else []
    with csv_path.open('w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(event_rows)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'csv': str(csv_path), 'report': str(json_path), 'canonical149': report['canonical149'], 'formal10': report['formal10']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
