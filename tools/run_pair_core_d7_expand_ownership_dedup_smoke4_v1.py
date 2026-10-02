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
import tools.run_pair_core_decontaminated_d7_spare_slot_cached_v1 as cached

_CACHE = None
_EVENT_CACHE = None
EPS = orig.EPS


class D7ExpandOwnershipDedup(orig.DecontaminatedD7SpareSlotContinuation):
    def __init__(self, tape):
        super().__init__(tape)
        self.dedup_blocks = 0
        self.dedup_allows = 0
        self.dedup_events = []

    def _live_same_side_expand(self, side: str):
        out = []
        for sid, key in sorted(self.slot_key.items()):
            o = self.orders.get(key)
            if not o:
                continue
            if str(self.key_role.get(key)) != 'SATELLITE_EXPAND':
                continue
            if str(o.get('side')) != str(side):
                continue
            if bool(o.get('cancelRequested')):
                continue
            out.append({
                'slotId': int(sid), 'key': key, 'side': o.get('side'),
                'role': self.key_role.get(key), 'price': o.get('price'),
                'qty': o.get('qty'), 'cum': o.get('cum'),
                'cancelRequested': o.get('cancelRequested'),
            })
        return out

    def _open_one_option(self, t, qv, end):
        if self.q_pending_active is None:
            return super()._open_one_option(t, qv, end)

        L = self.q_ladder
        ctx = self._qualifying_context(L)
        if int(end) - int(t) <= orig.base.v2.NO_NEW_EXPOSURE_MS:
            self._complete_carrier(t, 'ACTIVE_BLOCKED_LATE_180S')
            self.q_pending_active = None
        elif self._submit_protected_active_qty(t, qv):
            self.q_pending_active = None
            if ctx is not None:
                self.dk_checks += 1
                slots_after = len(self.slot_key)
                spare = max(0, self.max_slots - slots_after)
                ev = {
                    'event': 'D7_DEDUP_POST_ACTIVE_CAPACITY', 't': int(t), **ctx,
                    'slotsAfterActive': slots_after, 'spareAfterActive': spare,
                    'maxSlots': self.max_slots,
                    'activeKey': (self.q_ladder or {}).get('activeKey'),
                }
                if spare > 0:
                    self.dk_spare += 1
                    side, role, require_pair, require_budget = orig.base.MinimalPairRoleSim._role_decision(self, qv)
                    cand = orig.base.MinimalPairRoleSim._candidate_from_levels(self, side, require_pair, require_budget)
                    live_same = self._live_same_side_expand(side) if str(role) == 'SATELLITE_EXPAND' else []
                    ev['ordinaryDecision'] = {
                        'side': side, 'role': role,
                        'candidate': ({'price': float(cand[0]), 'qty': float(cand[1])} if cand else None),
                        'sameSideLiveNonCancelExpand': live_same,
                    }
                    should_block = bool(cand is not None and str(role) == 'SATELLITE_EXPAND' and live_same)
                    if should_block:
                        self.dedup_blocks += 1
                        ev['dedupBlocked'] = True
                        ev['ordinarySubmitDelta'] = 0
                    else:
                        self.dedup_allows += 1
                        before_sub = int(self.submits)
                        before_hist = len(self.slot_history)
                        orig.base.MinimalPairRoleSim._open_one_option(self, t, qv, end)
                        delta = int(self.submits) - before_sub
                        new_submit = next((x for x in self.slot_history[before_hist:] if x.get('event') == 'ROLE_SLOT_SUBMIT'), None)
                        if delta > 0:
                            self.dk_ordinary_submits += delta
                        ev['dedupBlocked'] = False
                        ev['ordinarySubmitDelta'] = delta
                        if new_submit is not None:
                            ev['ordinary'] = {k: new_submit.get(k) for k in ['key','role','side','price','qty','slotId','source']}
                    ev['slotsAfterOrdinary'] = len(self.slot_key)
                self.dk_events.append(ev)
                self.dedup_events.append(ev)
            return
        return super()._open_one_option(t, qv, end)

    def run_qty(self, winner='__UNSCORED__'):
        r = super().run_qty(winner)
        r['expandOwnershipDedup'] = {
            'blocks': int(self.dedup_blocks),
            'allows': int(self.dedup_allows),
            'events': self.dedup_events,
        }
        return r


def geom(r):
    return {
        'floor': float(r['floor']), 'best': float(r['best']),
        'fills': int(r['fillEvents']), 'submits': int(r['submits']),
        'alternations': int(r['fillSideAlternations']),
        'upQty': float(r['upQty']), 'downQty': float(r['downQty']),
        'buyNotional': float(r['buyNotional']),
        'maxSlots': int(r['maxSimultaneousSlots']),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    if not mids:
        raise SystemExit('no market ids')

    fp = ResearchFastPath()
    try:
        bundles = [fp.preferred_bundle(mid) for mid in mids]
        bundle = bundles[0]
        if any(x.resolve() != bundle.resolve() for x in bundles[1:]):
            raise RuntimeError('cohort spans multiple preferred bundles')
        global _CACHE, _EVENT_CACHE
        _CACHE = ExecutionTapeBundleCache(bundle)
        mat = _CACHE.ensure_tapes(mids)
        _EVENT_CACHE = _CACHE.event_cache(trade_offset='mid')
        cached._CACHE = _CACHE
        cached._EVENT_CACHE = _EVENT_CACHE
        d60.Sim.__init__ = cached._fast_sim_init

        rows = []
        for i, mid in enumerate(mids, 1):
            market_rows = []
            for cell, cls in [
                ('A_V3B_FIFO_AGGREGATE', orig.v3b.FifoAggregateResponsibilityLadderV3B),
                ('B_UNCONDITIONAL_D7_SPARE_CONTINUATION', orig.DecontaminatedD7SpareSlotContinuation),
                ('C_D7_EXPAND_OWNERSHIP_DEDUP', D7ExpandOwnershipDedup),
            ]:
                sim = cls(_CACHE.tape_path(mid))
                try:
                    r = sim.run_qty('__UNSCORED__')
                finally:
                    sim.close()
                row = {'marketId': mid, 'cell': cell, **r}
                rows.append(row); market_rows.append(row)
            A, B, C = market_rows
            print(json.dumps({
                'progress': i, 'marketId': mid,
                'A': geom(A), 'B': geom(B), 'C': geom(C),
                'Bdecontam': B.get('decontamD7'),
                'Cdedup': C.get('expandOwnershipDedup'),
                'ledgerC': (C.get('quantityLedgerSummary') or {}).get('invariantViolations', {}),
            }, ensure_ascii=False), flush=True)

        by = {}
        for r in rows: by.setdefault(int(r['marketId']), {})[r['cell']] = r
        comparison = []
        for mid in mids:
            A = by[mid]['A_V3B_FIFO_AGGREGATE']; B = by[mid]['B_UNCONDITIONAL_D7_SPARE_CONTINUATION']; C = by[mid]['C_D7_EXPAND_OWNERSHIP_DEDUP']
            comparison.append({
                'marketId': mid,
                'BvsA': {k: geom(B)[k]-geom(A)[k] for k in ['floor','best','fills','submits','alternations']},
                'CvsA': {k: geom(C)[k]-geom(A)[k] for k in ['floor','best','fills','submits','alternations']},
                'CvsB': {k: geom(C)[k]-geom(B)[k] for k in ['floor','best','fills','submits','alternations']},
                'dedupBlocks': int((C.get('expandOwnershipDedup') or {}).get('blocks') or 0),
                'dedupAllows': int((C.get('expandOwnershipDedup') or {}).get('allows') or 0),
                'maxSlotsC': int(C['maxSimultaneousSlots']),
                'ledgerViolationsC': (C.get('quantityLedgerSummary') or {}).get('invariantViolations', {}),
            })
        out = {
            'version': 'PAIR_CORE_D7_SAME_RECEIPT_EXPAND_OWNERSHIP_DEDUP_SMOKE4_V1',
            'date': '2026-09-09', 'researchOnly': True, 'runtimeAuthority': False,
            'markets': mids, 'rows': rows, 'comparison': comparison,
            'fastPath': {'bundle': str(bundle), 'materialize': mat, 'bundleCache': _CACHE.summary(), 'eventCacheStats': dict(_EVENT_CACHE.stats)},
            'boundary': ['realistic HFT/no dream fill','consumed development only','strict-past ownership dedup only','no timer/threshold/sweep','no winner/PnL runtime input','no locked 1946xxx','no NEW24-B','no 8781'],
        }
        op = Path(a.output); op.parent.mkdir(parents=True, exist_ok=True); op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'comparison': comparison, 'fastPath': out['fastPath']}, ensure_ascii=False), flush=True)
    finally:
        fp.close()


if __name__ == '__main__':
    main()
