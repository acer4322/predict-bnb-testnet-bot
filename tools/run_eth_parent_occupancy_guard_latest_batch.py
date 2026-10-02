from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
import sys

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

import joblib

import tools.run_eth_parent_occupancy_prospective_guard_ab as pg
pe = pg.pe

EPS = 1e-9


def max_drawdown(pnls: list[float]) -> float:
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for x in pnls:
        eq += float(x)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def main() -> None:
    ap = argparse.ArgumentParser()
    for n in [
        'bundle', 'lifecycle-model', 'capability-model', 'dagger-cache',
        'timing-model', 'economic-model', 'price-model', 'surplus-model',
        'v44-model', 'v47-model'
    ]:
        ap.add_argument('--' + n, required=True)
    ap.add_argument('--market-ids', default='')
    ap.add_argument('--output', required=True)
    a = ap.parse_args()

    outp = Path(a.output)
    outp.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='eth_parent_occ_latest_batch_'))
    stop = threading.Event()

    def hb() -> None:
        while not stop.wait(15):
            print(json.dumps({'heartbeat': 'PARENT_OCCUPANCY_LATEST_BATCH', 'ts': time.time()}), flush=True)

    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({'heartbeat': 'PARENT_OCCUPANCY_LATEST_BATCH_START'}), flush=True)

    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort = json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']
        by = {int(r['marketId']): r for r in cohort}
        mids = [int(x) for x in a.market_ids.split(',') if x.strip()] if a.market_ids else [int(r['marketId']) for r in cohort]
        missing = [m for m in mids if m not in by]
        if missing:
            raise KeyError(f'markets missing from bundle: {missing}')

        models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']

        rows = []
        for i, mid in enumerate(mids, 1):
            cr = by[mid]
            tape = tmp / 'tapes' / f'{mid}.json.xz'
            sim = pe.make(pg.ProspectiveGuardParentOccupancyHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
            try:
                r = sim.run_guard(models, cr['winner'])
                cons, bound, parents = pe.alloc(sim, r)
            finally:
                sim.close()

            ss = pe.safety(r)
            safety_zero = all(float(v or 0.0) <= EPS for v in ss.values())
            occ = r.get('occupancyParents') or {}
            occupancy_bound = all(float(p.get('overReservedQty') or 0.0) <= EPS for p in occ.values())
            pnl = float(r.get('pnlDiagnosticOnly') or 0.0)
            row = {
                'marketId': mid,
                'winnerPostHocOnly': cr['winner'],
                'windowEndMs': cr.get('windowEndMs'),
                'chronologyIndex': cr.get('chronologyIndex'),
                'pnlDiagnosticOnly': pnl,
                'floor': float(r.get('floor') or 0.0),
                'fills': int(r.get('actualFillEvents') or 0),
                'rounds': int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),
                'repairParentBirths': int(r.get('repairParentBirths') or 0),
                'repairParentCompletions': int(r.get('repairParentCompletions') or 0),
                'parallelRepairSubmits': int(r.get('parallelRepairSubmits') or 0),
                'parallelRepairActiveFillQty': float(r.get('parallelRepairActiveFillQty') or 0.0),
                'anchorlessActiveSubmits': int(r.get('anchorlessActiveSubmits') or 0),
                'transitionChecks': int(r.get('transitionChecks') or 0),
                'transitionBlocks': int(r.get('transitionBlocks') or 0),
                'frontierV2Binds': int(r.get('frontierV2Binds') or 0),
                'prospectiveTransitionChecks': int(r.get('prospectiveTransitionChecks') or 0),
                'prospectiveTransitionBlocks': int(r.get('prospectiveTransitionBlocks') or 0),
                'passiveEvidenceEpochCount': int(r.get('passiveEvidenceEpochCount') or 0),
                'anchorlessWaitPassiveEvidence': int(r.get('anchorlessWaitPassiveEvidence') or 0),
                'overflowAllocatedQty': float(r.get('v84OverflowAllocatedQty') or 0.0),
                'overflowPaidQty': float(r.get('v84OverflowPaidQty') or 0.0),
                'safety': ss,
                'safetyZero': safety_zero,
                'allocationConservation': bool(cons),
                'allocationParentDebtBounded': bool(bound),
                'executionOccupancyBounded': bool(occupancy_bound),
                'allocationParents': parents,
            }
            rows.append(row)
            print(json.dumps({
                'progress': f'{i}/{len(mids)}', 'marketId': mid, 'pnl': pnl,
                'fills': row['fills'], 'rounds': row['rounds'],
                'activeFillQty': row['parallelRepairActiveFillQty'],
                'safety': safety_zero and cons and bound and occupancy_bound,
            }, ensure_ascii=False), flush=True)

        rows.sort(key=lambda x: (int(x.get('windowEndMs') or 0), int(x['marketId'])))
        pnls = [float(r['pnlDiagnosticOnly']) for r in rows]
        wins = sum(x > EPS for x in pnls)
        losses = sum(x < -EPS for x in pnls)
        flats = len(pnls) - wins - losses
        positives = sum(x for x in pnls if x > EPS)
        negatives = -sum(x for x in pnls if x < -EPS)
        n = len(rows)
        active_n = wins + losses
        safe_all = all(
            r['safetyZero'] and r['allocationConservation'] and r['allocationParentDebtBounded'] and r['executionOccupancyBounded']
            for r in rows
        )
        avg_pnl = sum(pnls) / n if n else 0.0
        total_win_rate = wins / n if n else 0.0
        active_win_rate = wins / active_n if active_n else 0.0
        target = {
            'averagePnlGt2': avg_pnl > 2.0,
            'totalWinRateGt50Pct': total_win_rate > 0.5,
        }
        aggregate = {
            'markets': n,
            'wins': wins,
            'losses': losses,
            'flats': flats,
            'totalWinRate': total_win_rate,
            'activeMarkets': active_n,
            'activeWinRate': active_win_rate,
            'totalPnl': sum(pnls),
            'averagePnlPerMarket': avg_pnl,
            'averagePnlPerActiveMarket': (sum(pnls) / active_n) if active_n else 0.0,
            'maxWin': max(pnls, default=0.0),
            'maxLoss': min(pnls, default=0.0),
            'profitFactor': (positives / negatives) if negatives > EPS else (None if positives > EPS else 0.0),
            'maxDrawdown': max_drawdown(pnls),
            'totalFills': sum(r['fills'] for r in rows),
            'meanFillsPerMarket': sum(r['fills'] for r in rows) / n if n else 0.0,
            'totalRounds': sum(r['rounds'] for r in rows),
            'meanRoundsPerMarket': sum(r['rounds'] for r in rows) / n if n else 0.0,
            'repairParentBirths': sum(r['repairParentBirths'] for r in rows),
            'repairParentCompletions': sum(r['repairParentCompletions'] for r in rows),
            'parallelRepairSubmits': sum(r['parallelRepairSubmits'] for r in rows),
            'parallelRepairActiveFillQty': sum(r['parallelRepairActiveFillQty'] for r in rows),
            'anchorlessActiveSubmits': sum(r['anchorlessActiveSubmits'] for r in rows),
            'transitionChecks': sum(r['transitionChecks'] for r in rows),
            'transitionBlocks': sum(r['transitionBlocks'] for r in rows),
            'frontierV2Binds': sum(r['frontierV2Binds'] for r in rows),
            'prospectiveTransitionChecks': sum(r['prospectiveTransitionChecks'] for r in rows),
            'prospectiveTransitionBlocks': sum(r['prospectiveTransitionBlocks'] for r in rows),
            'safetyAllZeroAndBounded': safe_all,
        }
        if not safe_all:
            decision = 'REJECT_LATEST_BATCH_SAFETY_OR_ACCOUNTING'
        elif all(target.values()):
            decision = 'LATEST_BATCH_PERFORMANCE_TARGET_PASS'
        else:
            decision = 'LATEST_BATCH_SAFE_PERFORMANCE_TARGET_NOT_MET'

        out = {
            'version': 'ETH_PARENT_OCCUPANCY_PROSPECTIVE_GUARD_LATEST_BATCH_V1',
            'date': '2026-09-05',
            'researchOnly': True,
            'runtimeAuthority': False,
            'decision': decision,
            'performanceTarget': {
                'averagePnlPerMarket': '> 2.0',
                'totalWinRateAllMarkets': '> 50%',
            },
            'targetGates': target,
            'aggregate': aggregate,
            'marketIds': [r['marketId'] for r in rows],
            'rows': rows,
            'boundary': [
                'realistic HFT execution tape only; no dream fill',
                'winner is post-hoc scoring only and is never runtime input',
                'latest parent-scoped occupancy + passive evidence + Repair-first transition/frontier + prospective ownership guard candidate',
                'Passive-only path keeps frozen sizing when no Active reservation exists',
                'Active and Passive share parent occupancy only when routes coexist',
                'AllocationLedger V2 remains Repair-first/overflow-second',
                '<=180s new-exposure fence retained',
                'no Target future/runtime oracle; no 8781',
            ],
        }
        outp.write_text(json.dumps(out, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps({'ok': True, 'decision': decision, 'targetGates': target, 'aggregate': aggregate}, ensure_ascii=False, default=str), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
