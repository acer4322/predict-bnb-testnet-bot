from __future__ import annotations
import argparse, json, math, os, shutil, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264

EPS = 1e-9
v2 = r264.v2


class R264ExecutionHazardDumpSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    """Behavior-inert instrumentation for role-conditioned execution survival.

    Records submit-time strict-past features for every passive Maker role and
    labels later physical outcomes. No action/admission/price/qty changes.
    """

    def __init__(self, tape, fanout_limit=1, max_slots=4):
        super().__init__(tape, fanout_limit, max_slots)
        self.hazard_rows: list[dict] = []
        self.hazard_by_key: dict[str, dict] = {}

    def _rank(self, side: str, price: float):
        try:
            levels = [float(v2.kprice(x)) for x in self._live_price_levels(side)]
            return levels.index(float(v2.kprice(price))) + 1
        except Exception:
            return None

    def _depth(self, side: str, price: float):
        try:
            return float(self._level_depth(side, float(price)))
        except Exception:
            return None

    def _submit_role_v8(self, t, side, role, p, q, proj, split=None):
        before_n = self.n
        levels = [float(v2.kprice(x)) for x in self._live_price_levels(side)]
        best = levels[0] if levels else None
        rank = self._rank(side, p)
        depth = self._depth(side, p)
        best_depth = self._depth(side, best) if best is not None else None
        opposite = 'DOWN' if side == 'UP' else 'UP'
        opp_avg = self.unmatched_avg(opposite)
        floor = float(self._physical_floor())
        best_payoff = float(max(float(self.inv['UP']), float(self.inv['DOWN'])) - float(self.cost))
        row = {
            'submitT': int(t),
            'key': f'{side}_{before_n}',
            'side': str(side),
            'role': str(role),
            'price': float(p),
            'qty': float(q),
            'rank': rank,
            'depth': depth,
            'bestPrice': best,
            'bestDepth': best_depth,
            'distanceTicks': ((float(best) - float(p)) / 0.01) if best is not None else None,
            'pairCompatible': bool(self._pair_ok(side, p)),
            'oppositeUnmatchedAvg': float(opp_avg) if opp_avg is not None else None,
            'pairSum': (float(opp_avg) + float(p)) if opp_avg is not None else None,
            'floor': floor,
            'best': best_payoff,
            'gap': best_payoff - floor,
            'upQty': float(self.inv['UP']),
            'downQty': float(self.inv['DOWN']),
            'cost': float(self.cost),
            'scopeSide': self.scopeSide,
            'scopeGeneration': int(self.scopeGeneration),
            'scopeDebtQty': float(self._scope_debt_qty()),
            'reservedRepairQuota': float(self._reserved_repair_quota()),
            'availableExpandRiskCredit': float(self._available_expand_risk_credit()),
            'scopeRepairProgressClocks': int(self.scopeRepairProgressClocks),
            'livePassiveSlots': int(len(self.slot_key)),
            'liveActiveSlots': int(len(getattr(self, 'activeKeys', set()))),
            'repairQtyAuthorized': float(split.get('repairQty', 0.0)) if isinstance(split, dict) else 0.0,
            'overflowQtyAuthorized': float(split.get('overflowQty', 0.0)) if isinstance(split, dict) else (float(q) if role == 'SATELLITE_EXPAND' else 0.0),
            'firstRankWorseT': None,
            'firstFrontierLossT': None,
        }
        ok = super()._submit_role_v8(t, side, role, p, q, proj, split)
        if ok:
            self.hazard_rows.append(row)
            self.hazard_by_key[row['key']] = row
        return ok

    def _scan_live_queue_state(self, t: int):
        for key, row in list(self.hazard_by_key.items()):
            o = self.orders.get(key)
            if not o:
                continue
            try:
                st = str(self.snap(o).get('status') or '').upper()
            except Exception:
                st = ''
            if st in v2.TERMINAL_STATUSES:
                continue
            side = str(row['side'])
            p = float(row['price'])
            rank_now = self._rank(side, p)
            if row['firstFrontierLossT'] is None and rank_now is None:
                row['firstFrontierLossT'] = int(t)
            if row['firstRankWorseT'] is None and row.get('rank') is not None and rank_now is not None and int(rank_now) > int(row['rank']):
                row['firstRankWorseT'] = int(t)

    def _open_one_option(self, t, qv, end):
        # Called after the current strict-past book update in the frozen run loop.
        self._scan_live_queue_state(int(t))
        return super()._open_one_option(t, qv, end)

    def run_hazard_dump(self, winner):
        r = super().run_r264(winner)
        fills = {}
        releases = {}
        for ev in r.get('slotHistory', []):
            typ = ev.get('event')
            key = ev.get('key')
            if not key:
                continue
            t = int(ev.get('t') or 0)
            if typ == 'ROLE_FILL_SPLIT':
                inc = float(ev.get('fillInc') or 0.0)
                if inc > EPS:
                    z = fills.setdefault(key, {'firstFillT': t, 'lastFillT': t, 'fillQty': 0.0, 'fillEvents': 0})
                    z['firstFillT'] = min(z['firstFillT'], t)
                    z['lastFillT'] = max(z['lastFillT'], t)
                    z['fillQty'] += inc
                    z['fillEvents'] += 1
            elif typ == 'SLOT_RELEASE':
                releases[key] = {
                    'terminalT': t,
                    'terminalStatus': str(ev.get('status') or ''),
                    'terminalCum': float(ev.get('cum') or 0.0),
                    'cancelRequested': bool(ev.get('cancelRequested')),
                }

        outrows = []
        for row0 in self.hazard_rows:
            row = dict(row0)
            key = row['key']
            f = fills.get(key)
            rel = releases.get(key)
            ff = int(f['firstFillT']) if f else None
            if ff is not None:
                cause = 'FILL'
                event_t = ff
            elif rel is not None:
                cause = 'OWN_CANCEL_ZERO' if bool(rel['cancelRequested']) else 'GENUINE_ZERO'
                event_t = int(rel['terminalT'])
            else:
                cause = 'CENSORED'
                event_t = None
            row.update({
                'cause': cause,
                'eventT': event_t,
                'eventDelayMs': (event_t - int(row['submitT'])) if event_t is not None else None,
                'filledAny': ff is not None,
                'firstFillT': ff,
                'firstFillDelayMs': (ff - int(row['submitT'])) if ff is not None else None,
                'fillQty': float(f.get('fillQty', 0.0)) if f else 0.0,
                'fillEvents': int(f.get('fillEvents', 0)) if f else 0,
                'terminalT': rel.get('terminalT') if rel else None,
                'terminalStatus': rel.get('terminalStatus') if rel else None,
                'terminalCum': rel.get('terminalCum') if rel else None,
                'cancelRequested': rel.get('cancelRequested') if rel else None,
                'lifetimeMs': (int(rel['terminalT']) - int(row['submitT'])) if rel else None,
                'firstFrontierLossDelayMs': (int(row['firstFrontierLossT']) - int(row['submitT'])) if row.get('firstFrontierLossT') is not None else None,
                'firstRankWorseDelayMs': (int(row['firstRankWorseT']) - int(row['submitT'])) if row.get('firstRankWorseT') is not None else None,
            })
            outrows.append(row)
        r['laneGHazardRows'] = outrows
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp = Path(tempfile.mkdtemp(prefix='lane_g_r264_hazard_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co = {int(x['marketId']): x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:
                (tmp / f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows = []
        for m in mids:
            sim = R264ExecutionHazardDumpSim(tmp / f'{m}.json.xz', 1, 4)
            try:
                r = sim.run_hazard_dump(co[m]['winner'])
            finally:
                sim.close()
            hz = r.get('laneGHazardRows', [])
            rows.append({'marketId': m, 'winnerPostHocOnly': co[m]['winner'], 'hazardRows': hz, 'correct': bool(r.get('r264CorrectnessPass'))})
            print(json.dumps({'marketId': m, 'orders': len(hz), 'fills': sum(x['cause'] == 'FILL' for x in hz), 'ownCancelZero': sum(x['cause'] == 'OWN_CANCEL_ZERO' for x in hz), 'genuineZero': sum(x['cause'] == 'GENUINE_ZERO' for x in hz), 'correct': bool(r.get('r264CorrectnessPass'))}, ensure_ascii=False), flush=True)
        allx = [x for r in rows for x in r['hazardRows']]
        out = {
            'version': 'LANE_G_R264_EXECUTION_HAZARD_DUMP_V1',
            'researchOnly': True,
            'behaviorChange': False,
            'markets': mids,
            'rows': rows,
            'aggregate': {'orders': len(allx), 'fills': sum(x['cause'] == 'FILL' for x in allx), 'ownCancelZero': sum(x['cause'] == 'OWN_CANCEL_ZERO' for x in allx), 'genuineZero': sum(x['cause'] == 'GENUINE_ZERO' for x in allx), 'censored': sum(x['cause'] == 'CENSORED' for x in allx)},
            'gates': {'correctnessPass': all(r['correct'] for r in rows)},
            'boundary': ['exact R2.64 behavior; instrumentation only', 'strict-past submit features only', 'outcome labels from later realistic-HFT execution', 'winner posthoc only and absent from features', 'no Target runtime input', 'no dream fill', 'no 8781'],
        }
        op = (Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json') if str(a.output).upper() == 'AUTO' else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'gates': out['gates'], 'aggregate': out['aggregate']}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
