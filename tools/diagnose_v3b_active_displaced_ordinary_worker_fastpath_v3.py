from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[2]
STAGING = Path(__file__).resolve().parent
for p in (ROOT, STAGING):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from execution_tape_bundle_cache_v1 import ExecutionTapeBundleCache
import tools.run_eth_dagger60_smoke_v1 as d60
import tools.diagnose_v3b_active_displaced_ordinary_v1 as diag

_CACHE = None
_EVENT_CACHE = None


def _mid_from_tape(tape) -> int:
    return int(Path(tape).name.split('.', 1)[0])


def _fast_sim_init(self, tape, traj=None, seed='FORCE_UP'):
    global _CACHE, _EVENT_CACHE
    if _CACHE is None or _EVENT_CACHE is None:
        raise RuntimeError('fast-path cache not initialized')
    mid = _mid_from_tape(tape)
    self.payload = _CACHE.load_tape(mid)
    if _EVENT_CACHE.has_valid(mid):
        self.events, self.times, cm = _EVENT_CACHE.load(mid)
    else:
        self.events, self.times, cm = _EVENT_CACHE.build(mid)
    self.meta = dict(cm.get('rawFeedMeta') or cm)
    self.bt = d60.ex.new_bt(self.events, entry_latency_ms=250, response_latency_ms=250, queue_model='risk')
    d60.ex.initialize_bt(self.bt)
    self.traj = sorted(traj or [], key=lambda r: r['t'])
    self.ti = 0
    self.target = {'UP': 0.0, 'DOWN': 0.0}
    self.seed = seed
    self.seeded = False
    self.firstValid = None
    self.book = {'bids': {}, 'asks': {}}
    self.orders = {}
    self.n = 1
    self.inv = {'UP': 0.0, 'DOWN': 0.0}
    self.cost = 0.0
    self.sideCost = {'UP': 0.0, 'DOWN': 0.0}
    self.un = {'UP': d60.deque(), 'DOWN': d60.deque()}
    self.pairReserve = 0.0
    self.pairedQty = 0.0
    self.fillHist = d60.deque()
    self.placeHist = d60.deque()
    self.submits = 0
    self.fills = 0


def _resolve_output(v: str) -> Path:
    if v.upper() == 'AUTO':
        rd = os.environ.get('BTC5M_LAN_RESULT_DIR')
        if not rd:
            raise RuntimeError('AUTO output requires BTC5M_LAN_RESULT_DIR')
        return Path(rd) / 'result.json'
    return Path(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    if not mids:
        raise SystemExit('no market ids')

    global _CACHE, _EVENT_CACHE
    bundle = Path(a.bundle).resolve()
    _CACHE = ExecutionTapeBundleCache(bundle)
    materialize = _CACHE.ensure_tapes(mids)
    _EVENT_CACHE = _CACHE.event_cache(trade_offset='mid')
    d60.Sim.__init__ = _fast_sim_init

    rows = []
    for i, mid in enumerate(mids, 1):
        sim = diag.DiagnosticV3B(_CACHE.tape_path(mid))
        try:
            r = sim.run_qty('__UNSCORED__')
        finally:
            sim.close()
        rows.append({'marketId': mid, **r})
        print(json.dumps({'progress': i, 'marketId': mid, 'diagnosticCount': len(r.get('activeDisplacementDiagnostic') or []), 'diagnostics': r.get('activeDisplacementDiagnostic') or []}, ensure_ascii=False), flush=True)

    out = {
        'version': 'V3B_ACTIVE_DISPLACED_ORDINARY_DIAGNOSTIC_WORKER_FASTPATH_V3',
        'date': '2026-09-09',
        'researchOnly': True,
        'actionAuthority': False,
        'markets': mids,
        'rows': rows,
        'fastPath': {
            'bundle': str(bundle),
            'materialize': materialize,
            'bundleCache': _CACHE.summary(),
            'eventCacheStats': dict(_EVENT_CACHE.stats),
            'ioOnlyPatch': True,
        },
        'boundary': [
            'diagnostic only; V3B action path unchanged',
            'strict-past state immediately before protected Active submit',
            'no winner/PnL/future input',
            'persistent tape/event cache; no repeated archive-event rebuild',
            'no NEW24-B; no 8781; no dream fill',
        ],
    }
    op = _resolve_output(a.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'markets': len(mids), 'result': str(op), 'fastPath': out['fastPath']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
