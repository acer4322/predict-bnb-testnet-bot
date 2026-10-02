from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_fast_path_v1 import ResearchFastPath
from tools.execution_tape_bundle_cache_v1 import ExecutionTapeBundleCache
import tools.run_eth_dagger60_smoke_v1 as d60
import tools.diagnose_v3b_active_displaced_ordinary_v1 as diag
import tools.run_pair_core_decontaminated_d7_spare_slot_cached_v1 as cached

_CACHE: ExecutionTapeBundleCache | None = None
_EVENT_CACHE = None


def _fast_init(self, tape, traj=None, seed='FORCE_UP'):
    global _CACHE, _EVENT_CACHE
    cached._CACHE = _CACHE
    cached._EVENT_CACHE = _EVENT_CACHE
    return cached._fast_sim_init(self, tape, traj=traj, seed=seed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    if not mids:
        raise SystemExit('no market ids')

    global _CACHE, _EVENT_CACHE
    fp = ResearchFastPath()
    try:
        bundles = [fp.preferred_bundle(mid) for mid in mids]
        first = bundles[0]
        if any(p.resolve() != first.resolve() for p in bundles[1:]):
            raise RuntimeError('structural cohort spans multiple preferred bundles')
        _CACHE = ExecutionTapeBundleCache(first)
        mat = _CACHE.ensure_tapes(mids)
        _EVENT_CACHE = _CACHE.event_cache(trade_offset='mid')
        d60.Sim.__init__ = _fast_init

        rows = []
        for i, mid in enumerate(mids, 1):
            tape = _CACHE.tape_path(mid)
            sim = diag.DiagnosticV3B(tape)
            try:
                r = sim.run_qty('__UNSCORED__')
            finally:
                sim.close()
            rows.append({'marketId': mid, **r})
            print(json.dumps({'progress': i, 'marketId': mid, 'diagnosticCount': len(r.get('activeDisplacementDiagnostic') or []), 'diagnostics': r.get('activeDisplacementDiagnostic') or []}, ensure_ascii=False), flush=True)

        out = {
            'version': 'V3B_ACTIVE_DISPLACED_ORDINARY_DIAGNOSTIC_FASTPATH_V1',
            'date': '2026-09-09',
            'researchOnly': True,
            'actionAuthority': False,
            'markets': mids,
            'rows': rows,
            'fastPath': {
                'bundle': str(first),
                'materialize': mat,
                'bundleCache': _CACHE.summary(),
                'eventCacheStats': dict(_EVENT_CACHE.stats),
                'ioOnlyPatch': True,
            },
            'boundary': [
                'diagnostic only; V3B action path unchanged',
                'strict-past state immediately before protected Active submit',
                'no winner/PnL/future input',
                'ResearchFastPath + Bundle Registry + persistent tape/event cache',
                'no NEW24-B; no 8781; no dream fill',
            ],
        }
        op = Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'markets': len(mids), 'fastPath': out['fastPath']}, ensure_ascii=False), flush=True)
    finally:
        fp.close()


if __name__ == '__main__':
    main()
