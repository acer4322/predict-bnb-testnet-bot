from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get('BTC5M_WORKER_ROOT', '')).resolve() if os.environ.get('BTC5M_WORKER_ROOT') else Path(__file__).resolve().parents[1]
if not (ROOT / 'tools').is_dir():
    ROOT = Path.cwd().resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--market-id', type=int, required=True)
    ap.add_argument('--strategy-db', required=True)
    ap.add_argument('--tape-dir', required=True)
    ap.add_argument('--book-db')
    ap.add_argument('--runtime-site')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    if a.runtime_site:
        site = Path(a.runtime_site).resolve()
        if site.is_dir():
            sys.path.insert(0, str(site))

    from tools import hftbacktest_r2_execution_school_v0 as base
    from tools import hftbacktest_r3_context_control_v0 as r3ctl

    base.STRATEGY_DB = Path(a.strategy_db).resolve()
    base.tape_v1.ARCHIVE_DIR = Path(a.tape_dir).resolve()
    if a.book_db:
        base.ex.BOOK_DB = Path(a.book_db).resolve()

    mid = int(a.market_id)
    snaps = base.load_public_snapshots(mid)
    tape_events, update_times, tape_meta = base.tape_v1.build_archive_events(mid, trade_offset='mid')
    rep = r3ctl.run_market(mid, True)
    roll = rep.get('studentRollout') or {}
    decisions = rep.get('decisionRows') or []

    def compact_decision(d: dict) -> dict:
        p = d.get('portfolio') or {}
        m = d.get('models') or {}
        return {
            'decisionMs': d.get('decisionMs'),
            'phase': d.get('phase'),
            'desiredPortfolioAction': d.get('desiredPortfolioAction'),
            'executionChoice': d.get('executionChoice'),
            'activeMakerOrders': d.get('activeMakerOrders'),
            'bookAgeMs': d.get('bookAgeMs'),
            'makerNet': p.get('maker_net'),
            'makerAbsNet': p.get('maker_abs_net'),
            'makerCoverage': p.get('maker_paired_coverage'),
            'floor': p.get('worst_case_floor'),
            'pMakerUpBase': m.get('pMakerUpBase'),
            'pMakerDownBase': m.get('pMakerDownBase'),
            'pMakerUp': m.get('pMakerUp'),
            'pMakerDown': m.get('pMakerDown'),
            'pTaker1s': m.get('pTaker1s'),
            'pTaker3s': m.get('pTaker3s'),
            'pResidualWake': m.get('pResidualWake'),
            'pPassiveRepair': m.get('pPassiveRepair'),
        }

    mods = {}
    for name in ['numpy', 'pandas', 'sklearn', 'joblib', 'numba', 'scipy']:
        try:
            mod = importlib.import_module(name)
            mods[name] = {'version': getattr(mod, '__version__', None), 'file': getattr(mod, '__file__', None)}
        except Exception as exc:
            mods[name] = {'error': f'{type(exc).__name__}:{exc}'}

    out = {
        'version': 'R4_REPLAY_EQUIVALENCE_TRACE_V1',
        'marketId': mid,
        'python': sys.version,
        'modules': mods,
        'strategyDb': str(base.STRATEGY_DB),
        'tapeDir': str(base.tape_v1.ARCHIVE_DIR),
        'bookDb': str(base.ex.BOOK_DB),
        'snapshotCount': len(snaps),
        'firstSnapshotMs': int(snaps[0].get('sampledAtMs') or 0) if snaps else None,
        'lastSnapshotMs': int(snaps[-1].get('sampledAtMs') or 0) if snaps else None,
        'firstSnapshotKeys': sorted(snaps[0].keys()) if snaps else [],
        'tapeEventCount': len(tape_events),
        'tapeUpdateCount': len(update_times),
        'tapeMeta': tape_meta,
        'rollout': {
            'decisions': roll.get('decisions'),
            'makerPlacements': roll.get('makerPlacements'),
            'makerFillEvents': roll.get('makerFillEvents'),
            'makerFilledShares': roll.get('makerFilledShares'),
            'takerFills': roll.get('takerFills'),
            'takerFilledShares': roll.get('takerFilledShares'),
            'submitRejectCount': len(roll.get('submitRejects') or []),
            'submitRejectsHead': (roll.get('submitRejects') or [])[:5],
            'finalPortfolio': roll.get('finalPortfolio'),
        },
        'decisionRowCount': len(decisions),
        'decisionHead': [compact_decision(d) for d in decisions[:8]],
        'decisionFirstNonzeroInventory': next((compact_decision(d) for d in decisions if abs(float((d.get('portfolio') or {}).get('maker_abs_net') or 0)) > 1e-9), None),
        'r3Control': {
            'eventCount': len((rep.get('r3Control') or {}).get('events') or []),
            'vetoStrong': (rep.get('r3Control') or {}).get('vetoStrong'),
            'weakSubstitutions': (rep.get('r3Control') or {}).get('weakSubstitutions'),
        },
    }
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, default=str), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
