from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
import zipfile
from collections import deque
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264

v2 = r264.v2
EPS = 1e-9
WINDOWS = (500, 1000, 2000)


class ReanchorDecisionTrajectoryDumpSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    """Behavior-inert decision-time / strict-past trajectory instrumentation.

    Captures the state exactly when frozen R2.64 requests SATELLITE_REPAIR
    SATELLITE_FRONTIER_REANCHOR. No cancellation/admission/price/qty behavior changes.
    """

    def __init__(self, tape, fanout_limit=1, max_slots=4):
        super().__init__(tape, fanout_limit, max_slots)
        self.reanchorDecisionRows = []
        self._recent = {'UP': deque(), 'DOWN': deque()}
        self._lastSampleT = None

    def _safe_depth(self, side, price):
        if price is None:
            return None
        try:
            return float(self._level_depth(side, float(price)))
        except Exception:
            return None

    def _sample_side(self, t, side, imb):
        levels = [float(v2.kprice(x)) for x in self._live_price_levels(side)]
        best = levels[0] if levels else None
        second = levels[1] if len(levels) > 1 else None
        row = {
            't': int(t),
            'bestPrice': best,
            'secondPrice': second,
            'bestDepth': self._safe_depth(side, best),
            'secondDepth': self._safe_depth(side, second),
            'imb': float(imb) if imb is not None and math.isfinite(float(imb)) else None,
            'levelCount': len(levels),
        }
        q = self._recent[side]
        q.append(row)
        cutoff = int(t) - max(WINDOWS) - 1500
        while q and int(q[0]['t']) < cutoff:
            q.popleft()

    def _sample_book(self, t):
        if self._lastSampleT == int(t):
            return
        self._lastSampleT = int(t)
        try:
            qv = v2.base.quotes(self.book)
            imb = qv.get('imb') if qv else None
        except Exception:
            imb = None
        for side in ('UP', 'DOWN'):
            self._sample_side(int(t), side, imb)

    def _trajectory(self, t, side, own_price):
        q = list(self._recent[side])
        out = {}
        for w in WINDOWS:
            z = [x for x in q if int(t) - int(x['t']) <= w]
            if not z:
                z = q[-1:] if q else []
            if not z:
                for name in ('bestDelta','bestRange','depthDelta','depthRange','bestMoveCount','gapNowTicks','gapStartTicks','gapDeltaTicks','imbDelta','levelCountDelta'):
                    out[f'w{w}_{name}'] = None
                continue
            first = z[0]
            last = z[-1]
            bvals = [float(x['bestPrice']) for x in z if x.get('bestPrice') is not None]
            dvals = [float(x['bestDepth']) for x in z if x.get('bestDepth') is not None]
            ivals = [float(x['imb']) for x in z if x.get('imb') is not None]
            moves = 0
            prev = None
            for x in z:
                b = x.get('bestPrice')
                if b is None:
                    continue
                if prev is not None and abs(float(b) - float(prev)) > EPS:
                    moves += 1
                prev = b
            b0 = first.get('bestPrice')
            b1 = last.get('bestPrice')
            d0 = first.get('bestDepth')
            d1 = last.get('bestDepth')
            i0 = first.get('imb')
            i1 = last.get('imb')
            out[f'w{w}_bestDelta'] = None if b0 is None or b1 is None else float(b1) - float(b0)
            out[f'w{w}_bestRange'] = (max(bvals) - min(bvals)) if bvals else None
            out[f'w{w}_depthDelta'] = None if d0 is None or d1 is None else float(d1) - float(d0)
            out[f'w{w}_depthRange'] = (max(dvals) - min(dvals)) if dvals else None
            out[f'w{w}_bestMoveCount'] = int(moves)
            out[f'w{w}_gapNowTicks'] = None if b1 is None else (float(b1) - float(own_price)) / 0.01
            out[f'w{w}_gapStartTicks'] = None if b0 is None else (float(b0) - float(own_price)) / 0.01
            out[f'w{w}_gapDeltaTicks'] = None if b0 is None or b1 is None else (float(b1) - float(b0)) / 0.01
            out[f'w{w}_imbDelta'] = None if i0 is None or i1 is None else float(i1) - float(i0)
            out[f'w{w}_levelCountDelta'] = int(last.get('levelCount') or 0) - int(first.get('levelCount') or 0)
        return out

    def _decision_row(self, t, sid, key, o):
        side = str(o['side'])
        own = float(o['price'])
        levels = [float(v2.kprice(x)) for x in self._live_price_levels(side)]
        current_best = levels[0] if levels else None
        current_second = levels[1] if len(levels) > 1 else None
        used = set(self._used_prices(side))
        replacement = next((p for p in levels if p not in used), None)
        replacement_qty = (1.0 / replacement) if replacement is not None and replacement > EPS else None
        floor = float(self._physical_floor())
        repl_floor = None
        if replacement is not None and replacement_qty is not None:
            repl_floor = float(self._candidate_alone_floor(side, replacement, replacement_qty))
        try:
            s = self.snap(o)
            cum = float(s.get('cumExecQty') or o.get('cum') or 0.0)
            leaves = s.get('leavesQty')
            remaining = float(leaves) if leaves is not None else max(0.0, float(o.get('qty') or 0.0) - cum)
        except Exception:
            cum = float(o.get('cum') or 0.0)
            remaining = max(0.0, float(o.get('qty') or 0.0) - cum)
        own_quota = float(self.keyRepairQuotaRemaining.get(key, 0.0))
        debt = float(self._scope_debt_qty())
        reserved = float(self._reserved_repair_quota(side))
        reserved_without_own = max(0.0, reserved - own_quota)
        opp = 'DOWN' if side == 'UP' else 'UP'
        opp_avg = self.unmatched_avg(opp)
        row = {
            't': int(t),
            'key': str(key),
            'slotId': int(sid),
            'side': side,
            'role': str(self.key_role.get(key, '')),
            'ownPrice': own,
            'orderQty': float(o.get('qty') or 0.0),
            'cumQtyAtDecision': cum,
            'remainingQtyAtDecision': remaining,
            'ageAtDecisionMs': int(t) - int(o.get('placed') or t),
            'currentBestPrice': current_best,
            'currentSecondPrice': current_second,
            'currentBestDepth': self._safe_depth(side, current_best),
            'currentSecondDepth': self._safe_depth(side, current_second),
            'currentGapTicks': None if current_best is None else (float(current_best) - own) / 0.01,
            'replacementPrice': replacement,
            'replacementQty': replacement_qty,
            'replacementVsOwnTicks': None if replacement is None else (float(replacement) - own) / 0.01,
            'replacementPairSum': None if replacement is None or opp_avg is None else float(replacement) + float(opp_avg),
            'replacementFloorDeltaIfFilled': None if repl_floor is None else repl_floor - floor,
            'physicalFloorAtDecision': floor,
            'physicalBestAtDecision': float(max(self.inv.values()) - self.cost),
            'physicalGapAtDecision': float(max(self.inv.values()) - self.cost) - floor,
            'scopeSideAtDecision': self.scopeSide,
            'scopeGenerationAtDecision': int(self.scopeGeneration),
            'scopeDebtQtyAtDecision': debt,
            'reservedRepairQuotaAtDecision': reserved,
            'ownRepairQuotaRemaining': own_quota,
            'reservedRepairQuotaWithoutOwn': reserved_without_own,
            'unreservedDebtIfOwnReleased': max(0.0, debt - reserved_without_own),
            'availableExpandRiskCreditAtDecision': float(self._available_expand_risk_credit()),
            'scopeRepairProgressClocksAtDecision': int(self.scopeRepairProgressClocks),
            'livePassiveSlotsAtDecision': int(len(self.slot_key)),
            'liveActiveSlotsAtDecision': int(len(getattr(self, 'activeKeys', set()))),
            'sameSideLiveCountAtDecision': int(len(self._live_role_rows(side=side))),
            'oppositeUnmatchedAvgAtDecision': float(opp_avg) if opp_avg is not None else None,
        }
        row.update(self._trajectory(int(t), side, own))
        return row

    def _request_cancel(self, t, sid, reason):
        key = self.slot_key.get(int(sid))
        o = self.orders.get(key) if key else None
        role = self.key_role.get(key) if key else None
        if reason == 'SATELLITE_FRONTIER_REANCHOR' and key and o and role == 'SATELLITE_REPAIR':
            self._sample_book(int(t))
            self.reanchorDecisionRows.append(self._decision_row(int(t), int(sid), str(key), o))
        return super()._request_cancel(t, sid, reason)

    def _reanchor_stale(self, t):
        self._sample_book(int(t))
        return super()._reanchor_stale(t)

    def run_dump(self, winner):
        r = super().run_r264(winner)
        return {
            'correct': bool(r.get('r264CorrectnessPass')),
            'rows': self.reanchorDecisionRows,
            'submits': int(r.get('submits') or 0),
            'fills': int(r.get('fillEvents') or 0),
            'pnlPostHocOnly': float(r.get('pnlDiagnosticOnly') or 0.0),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp = Path(tempfile.mkdtemp(prefix='lane_g_reanchor_decision_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co = {int(x['marketId']): x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:
                (tmp / f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows = []
        markets = []
        for m in mids:
            sim = ReanchorDecisionTrajectoryDumpSim(tmp / f'{m}.json.xz', 1, 4)
            try:
                r = sim.run_dump(co[m]['winner'])
            finally:
                sim.close()
            for x in r['rows']:
                rows.append({'marketId': m, **x})
            markets.append({'marketId': m, 'decisionCount': len(r['rows']), 'correct': r['correct'], 'submits': r['submits'], 'fills': r['fills']})
            print(json.dumps(markets[-1], ensure_ascii=False), flush=True)
        out = {
            'version': 'LANE_G_R264_REANCHOR_DECISION_TIME_TRAJECTORY_DUMP_V1_20260907',
            'researchOnly': True,
            'behaviorChange': False,
            'markets': markets,
            'rows': rows,
            'gates': {
                'correctnessPass': all(x['correct'] for x in markets),
                'decisionCount': len(rows),
            },
            'boundary': [
                'exact frozen R2.64 behavior; instrumentation only',
                'capture occurs before native SATELLITE_FRONTIER_REANCHOR cancel request',
                'current and trailing 0.5/1/2s features use only observable strict-past/current book state',
                'no future fill/cancel/priority-loss outcome in features',
                'winner/PnL posthoc only and absent from decision rows',
                'no Target runtime input, no fresh, no dream fill, no 8781',
            ],
        }
        op = (Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json') if str(a.output).upper() == 'AUTO' else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'gates': out['gates']}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
