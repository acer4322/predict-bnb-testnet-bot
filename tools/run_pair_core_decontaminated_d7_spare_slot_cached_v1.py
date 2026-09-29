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
import tools.run_eth_quantity_responsibility_ladder_v3k_decontaminated_d7_spare_slot_continuation as orig

_CACHE: ExecutionTapeBundleCache | None = None
_EVENT_CACHE = None


def _mid_from_tape(tape) -> int:
    name = Path(tape).name
    return int(name.split('.', 1)[0])


def _fast_sim_init(self, tape, traj=None, seed='FORCE_UP'):
    global _CACHE, _EVENT_CACHE
    if _CACHE is None or _EVENT_CACHE is None:
        raise RuntimeError('fast-path cache not initialized')
    mid = _mid_from_tape(tape)
    self.payload = _CACHE.load_tape(mid)
    if _EVENT_CACHE.has_valid(mid):
        self.events, self.times, cache_meta = _EVENT_CACHE.load(mid)
        self.meta = dict(cache_meta.get('rawFeedMeta') or cache_meta)
    else:
        self.events, self.times, cache_meta = _EVENT_CACHE.build(mid)
        self.meta = dict(cache_meta.get('rawFeedMeta') or cache_meta)
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


def _run_one(mid: int, tape: Path):
    rows = []
    for cell, cls in [
        ('A_V3B_FIFO_AGGREGATE', orig.v3b.FifoAggregateResponsibilityLadderV3B),
        ('B_DECONTAM_D7_SAME_RECEIPT_SPARE', orig.DecontaminatedD7SpareSlotContinuation),
    ]:
        sim = cls(tape)
        try:
            r = sim.run_qty('__UNSCORED__')
        finally:
            sim.close()
        rows.append({'marketId': mid, 'cell': cell, **r})
    return rows


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
            raise RuntimeError('cached wrapper currently requires one canonical bundle for the fixed cohort')
        _CACHE = ExecutionTapeBundleCache(first)
        materialize = _CACHE.ensure_tapes(mids)
        _EVENT_CACHE = _CACHE.event_cache(trade_offset='mid')
        d60.Sim.__init__ = _fast_sim_init
        d60.feed.ARCHIVE_DIR = _CACHE.tape_dir

        rows = []
        for i, mid in enumerate(mids, 1):
            tape = _CACHE.tape_path(mid)
            mr = _run_one(mid, tape)
            rows.extend(mr)
            A, B = mr
            comp = {
                'progress': i,
                'marketId': mid,
                'floorDelta': float(B.get('floor') or 0) - float(A.get('floor') or 0),
                'bestDelta': float(B.get('best') or 0) - float(A.get('best') or 0),
                'fillDelta': int(B.get('fillEvents') or 0) - int(A.get('fillEvents') or 0),
                'submitDelta': int(B.get('submits') or 0) - int(A.get('submits') or 0),
                'alternationDelta': int(B.get('fillSideAlternations') or 0) - int(A.get('fillSideAlternations') or 0),
                'maxSlotsCandidate': int(B.get('maxSimultaneousSlots') or 0),
                'decontam': B.get('decontamD7'),
                'ledgerViolations': ((B.get('quantityLedgerSummary') or {}).get('invariantViolations')),
            }
            print(json.dumps(comp, ensure_ascii=False), flush=True)

        amap = {r['marketId']: r for r in rows if r['cell'] == 'A_V3B_FIFO_AGGREGATE'}
        bmap = {r['marketId']: r for r in rows if r['cell'] == 'B_DECONTAM_D7_SAME_RECEIPT_SPARE'}
        comparison = []
        for mid in mids:
            A, B = amap[mid], bmap[mid]
            comparison.append({
                'marketId': mid,
                'floorDelta': float(B.get('floor') or 0) - float(A.get('floor') or 0),
                'bestDelta': float(B.get('best') or 0) - float(A.get('best') or 0),
                'fillDelta': int(B.get('fillEvents') or 0) - int(A.get('fillEvents') or 0),
                'submitDelta': int(B.get('submits') or 0) - int(A.get('submits') or 0),
                'alternationDelta': int(B.get('fillSideAlternations') or 0) - int(A.get('fillSideAlternations') or 0),
                'maxSlotsCandidate': int(B.get('maxSimultaneousSlots') or 0),
                'checks': int(((B.get('decontamD7') or {}).get('checks') or 0)),
                'spareChecks': int(((B.get('decontamD7') or {}).get('spareChecks') or 0)),
                'ordinarySubmits': int(((B.get('decontamD7') or {}).get('ordinarySubmits') or 0)),
                'ledgerViolations': ((B.get('quantityLedgerSummary') or {}).get('invariantViolations')),
            })
        out = {
            'version': 'PAIR_CORE_DECONTAMINATED_D7_SAME_RECEIPT_SPARE_SLOT_CACHED_V1',
            'researchOnly': True,
            'runtimeAuthority': False,
            'markets': mids,
            'rows': rows,
            'comparison': comparison,
            'fastPath': {
                'bundle': str(first),
                'materialize': materialize,
                'bundleCache': _CACHE.summary(),
                'eventCacheStats': dict(_EVENT_CACHE.stats),
                'ioOnlyPatch': True,
            },
            'boundary': [
                'strategy/controller semantics frozen',
                'only base Sim I/O constructor replaced with persistent BundleCache + EventCache',
                'no winner/PnL input used',
                'realistic HFT/no dream fill/no 8781/no NEW24-B',
            ],
        }
        op = Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'comparison': comparison, 'fastPath': out['fastPath']}, ensure_ascii=False), flush=True)
    finally:
        fp.close()


if __name__ == '__main__':
    main()
