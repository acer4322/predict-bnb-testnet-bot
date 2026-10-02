from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import joblib

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sibling(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


base = sibling(
    'base_repair_slack_for_risk_capacity_v1',
    Path(__file__).resolve().with_name('run_eth_base_repair_price_envelope_slack_tuning_v1.py'),
)

EPS = 1e-9
v38 = base.v38
v80 = base.v80


class RecoverabilityBackedRiskCapacityShadow(base.BaseRepairPriceSlackCandidate):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.riskCapacityChecks = 0
        self.riskCapacityRecoverableChecks = 0
        self.riskCapacityPositiveSpareChecks = 0
        self.riskCapacityEvents = []
        self._risk_seen = set()

    def _capacity_for_total_expand_qty(self, t, side, qv, total_expand_qty):
        px = float(qv[side]['bid'])
        q = float(total_expand_qty)
        if not math.isfinite(q) or q <= EPS or q > 12.0 + EPS or px <= EPS:
            return {'feasible': False, 'reason': 'EXPAND_QTY_OR_PRICE_INVALID', 'totalExpandQty': q, 'expandPrice': px}

        floor, u, d, cost = self._raw_floor()
        hu = float(u) + (q if side == 'UP' else 0.0)
        hd = float(d) + (q if side == 'DOWN' else 0.0)
        hc = float(cost) + q * px
        hfloor = min(hu, hd) - hc

        repair = 'DOWN' if side == 'UP' else 'UP'
        weak_qty = hd if repair == 'DOWN' else hu
        strong_qty = hu if repair == 'DOWN' else hd
        hgap = max(0.0, strong_qty - weak_qty)

        owned = 0.0
        reserved_gain = 0.0
        owned_rows = []
        for key, entry, rem in self.lane_unresolved('REPAIR'):
            if entry.get('side') != repair:
                continue
            rq = float(rem)
            op = float(self.orders.get(key, {}).get('price') or entry.get('price') or 0.0)
            owned += rq
            gain = rq * (1.0 - op) if EPS < op < 1.0 - EPS else 0.0
            reserved_gain += gain
            owned_rows.append({'key': key, 'remaining': rq, 'price': op, 'floorGainIfFilled': gain})

        projected = hfloor + reserved_gain
        room = max(0.0, hgap - owned)
        ceiling = (strong_qty - hc) / hgap if hgap > EPS else None
        rbid_raw = qv.get(repair, {}).get('bid')
        rbid = float(rbid_raw) if rbid_raw is not None else None
        admissible = min(rbid, float(ceiling)) if rbid is not None and ceiling is not None else None

        need = None
        legal = None
        req = None
        if projected >= -EPS:
            feasible = True
            reason = 'OWNED_REPAIR_COVERS_PROJECTED_FLOOR'
        elif admissible is None or not (EPS < admissible < 1.0 - EPS):
            feasible = False
            reason = 'NO_ADMISSIBLE_FUTURE_REPAIR_PRICE'
        else:
            need = max(0.0, -projected) / (1.0 - admissible)
            legal = 1.0 / admissible
            req = max(need, legal)
            feasible = req <= room + EPS
            reason = 'PASS' if feasible else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM'

        return {
            't': int(t),
            'side': side,
            'expandPrice': px,
            'totalExpandQty': q,
            'floorBefore': float(floor),
            'hypFloorAfterExpand': float(hfloor),
            'repairSide': repair,
            'ownedRepairQty': float(owned),
            'ownedRepairGain': float(reserved_gain),
            'ownedRepairRows': owned_rows,
            'projectedFloorAfterOwnedRepair': float(projected),
            'repairGapAfterExpand': float(hgap),
            'repairRoomAfterOwned': float(room),
            'economicRepairCeiling': None if ceiling is None else float(ceiling),
            'repairBid': rbid,
            'admissibleFutureRepairPrice': None if admissible is None else float(admissible),
            'futureNeedQty': None if need is None else float(need),
            'futureLegalQty': None if legal is None else float(legal),
            'futureRequiredQty': None if req is None else float(req),
            'repairCapacityMarginQty': None if req is None else float(room - req),
            'feasible': bool(feasible),
            'reason': reason,
        }

    def _v75_recoverability(self, t, side, qv):
        original = super()._v75_recoverability(t, side, qv)
        key = (int(t), str(side), round(float(qv[side]['bid']), 8))
        if key in self._risk_seen:
            return original
        self._risk_seen.add(key)
        self.riskCapacityChecks += 1

        px = float(qv[side]['bid'])
        q0 = 1.0 / px if px > EPS else math.inf
        event = {
            't': int(t),
            'side': side,
            'baseExpandPrice': px,
            'baseExpandQty': q0,
            'baseRecoverability': dict(original),
            'originalRecoverableDecision': bool(original.get('recoverable')),
            'maxTotalExpandQty': q0,
            'spareRecoverableQty': 0.0,
            'riskSpendCapacity': 0.0,
            'q10': q0,
            'q25': q0,
            'extraQty10': 0.0,
            'extraQty25': 0.0,
            'extraRiskSpend10': 0.0,
            'extraRiskSpend25': 0.0,
        }

        if bool(original.get('recoverable')) and math.isfinite(q0) and q0 <= 12.0 + EPS:
            self.riskCapacityRecoverableChecks += 1
            base_eval = self._capacity_for_total_expand_qty(t, side, qv, q0)
            hi = 12.0
            hi_eval = self._capacity_for_total_expand_qty(t, side, qv, hi)
            if bool(hi_eval.get('feasible')):
                max_q = hi
                max_eval = hi_eval
            else:
                lo = q0
                if not bool(base_eval.get('feasible')):
                    max_q = q0
                    max_eval = base_eval
                else:
                    for _ in range(42):
                        mid = (lo + hi) / 2.0
                        cur = self._capacity_for_total_expand_qty(t, side, qv, mid)
                        if bool(cur.get('feasible')):
                            lo = mid
                        else:
                            hi = mid
                    max_q = lo
                    max_eval = self._capacity_for_total_expand_qty(t, side, qv, max_q)
            spare = max(0.0, max_q - q0)
            q10 = q0 + 0.10 * spare
            q25 = q0 + 0.25 * spare
            event.update({
                'baseCapacityEval': base_eval,
                'maxCapacityEval': max_eval,
                'maxTotalExpandQty': float(max_q),
                'spareRecoverableQty': float(spare),
                'riskSpendCapacity': float(spare * px),
                'q10': float(q10),
                'q25': float(q25),
                'extraQty10': float(q10 - q0),
                'extraQty25': float(q25 - q0),
                'extraRiskSpend10': float((q10 - q0) * px),
                'extraRiskSpend25': float((q25 - q0) * px),
                'q10Eval': self._capacity_for_total_expand_qty(t, side, qv, q10),
                'q25Eval': self._capacity_for_total_expand_qty(t, side, qv, q25),
            })
            if spare > EPS:
                self.riskCapacityPositiveSpareChecks += 1

        self.riskCapacityEvents.append(event)
        return original

    def run_shadow(self, models, winner):
        result = self.run_slack(models, winner)
        result.update({
            'riskCapacityChecks': self.riskCapacityChecks,
            'riskCapacityRecoverableChecks': self.riskCapacityRecoverableChecks,
            'riskCapacityPositiveSpareChecks': self.riskCapacityPositiveSpareChecks,
            'riskCapacityEvents': self.riskCapacityEvents[:500],
        })
        return result


def main():
    ap = argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--' + n, required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    mids = [int(x) for x in args.market_ids.split(',') if x.strip()]

    tmp = Path(tempfile.mkdtemp(prefix='eth_risk_capacity_shadow_v1_'))
    stop = threading.Event()

    def hb():
        while not stop.wait(15):
            print(json.dumps({'heartbeat':'ETH_RISK_CAPACITY_SHADOW_V1','markets':mids,'ts':time.time()}), flush=True)

    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({'heartbeat':'ETH_RISK_CAPACITY_SHADOW_V1_START','markets':mids}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(tmp)
        by = {int(r['marketId']): r for r in json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']}
        missing = [m for m in mids if m not in by]
        if missing:
            raise ValueError(f'missing markets {missing}')
        models, life, cap, tim, econ, price, sur = v38.v36.v34.v30.load_runtime(args)
        t44 = joblib.load(args.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(args.v47_model)['models']['GENERATION_AWARE_NORM']
        rows = []
        for i, mid in enumerate(mids, 1):
            cr = by[mid]
            sim = RecoverabilityBackedRiskCapacityShadow(
                tmp / 'tapes' / f'{mid}.json.xz',
                'BOOK_IMBALANCE',
                models, life, 0, 0,
                capability=cap, timing=tim, economic=econ, price_envelope=price,
                surplus_value=sur, teacher=t44, genTeacher=t47,
                policy_profile=v80.economic_v1_profile(),
                base_pair_slack=0.05,
                parent_no_chase_insurance=True,
            )
            try:
                r = sim.run_shadow(models, cr['winner'])
                saf = base.front.safety(r)
            finally:
                sim.close()
            ev = list(r.get('riskCapacityEvents') or [])
            pos = [x for x in ev if float(x.get('spareRecoverableQty') or 0.0) > EPS]
            row = {
                'marketId': mid,
                'winnerPostHocOnly': cr['winner'],
                'pnlDiagnosticOnly': float(r.get('pnlDiagnosticOnly') or 0.0),
                'floor': float(r.get('floor') or 0.0),
                'fills': int(r.get('actualFillEvents') or 0),
                'riskCapacityChecks': int(r.get('riskCapacityChecks') or 0),
                'recoverableChecks': int(r.get('riskCapacityRecoverableChecks') or 0),
                'positiveSpareChecks': int(r.get('riskCapacityPositiveSpareChecks') or 0),
                'maxSpareRecoverableQty': max([float(x.get('spareRecoverableQty') or 0.0) for x in ev] or [0.0]),
                'maxRiskSpendCapacity': max([float(x.get('riskSpendCapacity') or 0.0) for x in ev] or [0.0]),
                'events': ev[:300],
                'safety': saf,
            }
            rows.append(row)
            print(json.dumps({'idx':i,'of':len(mids),**{k:row[k] for k in ['marketId','pnlDiagnosticOnly','floor','fills','riskCapacityChecks','recoverableChecks','positiveSpareChecks','maxSpareRecoverableQty','maxRiskSpendCapacity']}}, ensure_ascii=False), flush=True)

        all_safety_zero = all(all(abs(float(v)) <= EPS for v in row['safety'].values()) for row in rows)
        out = {
            'version': 'ETH_RECOVERABILITY_BACKED_RISK_CAPACITY_SHADOW_V1',
            'date': '2026-09-04',
            'researchOnly': True,
            'runtimeAuthority': False,
            'marketIds': mids,
            'aggregate': {
                'markets': len(rows),
                'checks': sum(r['riskCapacityChecks'] for r in rows),
                'recoverableChecks': sum(r['recoverableChecks'] for r in rows),
                'positiveSpareChecks': sum(r['positiveSpareChecks'] for r in rows),
                'marketsWithPositiveSpare': sum(r['positiveSpareChecks'] > 0 for r in rows),
                'maxSpareRecoverableQty': max([r['maxSpareRecoverableQty'] for r in rows] or [0.0]),
                'maxRiskSpendCapacity': max([r['maxRiskSpendCapacity'] for r in rows] or [0.0]),
                'allSafetyZero': all_safety_zero,
            },
            'rows': rows,
            'boundary': [
                'shadow only; _v75_recoverability return decision unchanged',
                'parent-local no-chase Repair insurance behavior retained',
                'actual V83 Expand qty remains venue-min',
                'no future Target action/winner/PnL input',
                '<=180s boundary unchanged',
                'realistic HFT only',
                'no 8781',
            ],
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':out['aggregate']}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
