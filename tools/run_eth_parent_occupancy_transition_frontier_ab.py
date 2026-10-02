from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import joblib

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

import tools.run_eth_parent_occupancy_passive_evidence_ab as pe
from tools.eth_repair_modular.responsibility_frontier import (
    AuthoritativeRepairResponsibilityFrontierV2,
    RepairResponsibilityFrontierInput,
)
from tools.eth_repair_modular.responsibility_transition import (
    RepairFirstResponsibilityTransitionV1,
    ResponsibilityTransitionContext,
)

EPS = 1e-9


class TransitionFrontierParentOccupancyHFT(pe.PassiveEvidenceParentOccupancyHFT):
    def __init__(self, *a, **kw):
        self.transitionPolicy = RepairFirstResponsibilityTransitionV1()
        self.repairResponsibilityFrontier = AuthoritativeRepairResponsibilityFrontierV2()
        self.transitionChecks = 0
        self.transitionBlocks = 0
        self.frontierV2Binds = 0
        self.transitionEvents = []
        super().__init__(*a, **kw)

    def _overflow_repair_debt_by_side(self):
        debt = {'UP': 0.0, 'DOWN': 0.0}
        rows = []
        for key, m in getattr(self, 'v84Composite', {}).items():
            rem = max(0.0, float(m.get('overflowDebt') or 0.0) - float(m.get('overflowPaid') or 0.0))
            if rem <= EPS or m.get('overflowBornAt') is None:
                continue
            side = 'DOWN' if str(m.get('side')).upper() == 'UP' else 'UP'
            debt[side] += rem
            rows.append({'compositeKey': str(key), 'repairSide': side, 'remainingDebt': rem, 'bornAt': m.get('overflowBornAt')})
        return debt, rows

    def _live_repair_debt_by_side(self):
        debt, rows = self._overflow_repair_debt_by_side()
        rp = getattr(self, 'repairParent', None)
        side = rp.get('side') if isinstance(rp, dict) else None
        current = 0.0
        if isinstance(rp, dict) and side in ('UP', 'DOWN'):
            try:
                pay = self._current_payoffs()
                current = max(0.0, float(pay.get('gap') or 0.0))
            except Exception:
                current = max(0.0, float(getattr(self, '_coordDebt', 0.0) or 0.0))
        dec = self.repairResponsibilityFrontier.evaluate(
            RepairResponsibilityFrontierInput(
                side,
                current,
                float(debt.get('UP') or 0.0),
                float(debt.get('DOWN') or 0.0),
            )
        )
        if dec.current_parent_bound:
            self.frontierV2Binds += 1
            rows = list(rows) + [{
                'event': 'AUTHORITATIVE_CURRENT_REPAIR_PARENT_BOUND',
                'parentId': rp.get('id') if isinstance(rp, dict) else None,
                'repairSide': side,
                'currentParentDebtEvidence': current,
                'overflowDebtBefore': dict(debt),
                'transitionDebtAfter': {'UP': dec.debt_up, 'DOWN': dec.debt_down},
            }]
        return {'UP': dec.debt_up, 'DOWN': dec.debt_down}, rows

    def _score_state(self, t, after_kind):
        self._refresh_carrier_ledger(int(t))
        debt, rows = self._live_repair_debt_by_side()
        th = getattr(self, 'thesis', None)
        side = th.get('side') if isinstance(th, dict) else None
        d = self.transitionPolicy.evaluate(
            ResponsibilityTransitionContext(side, float(debt['UP']), float(debt['DOWN']))
        )
        self.transitionChecks += 1
        ev = {
            't': int(t),
            'event': 'RESPONSIBILITY_TRANSITION_CHECK',
            'afterKind': after_kind,
            'thesisSide': side,
            'repairDebtBySide': debt,
            'debtRows': rows,
            'allowExpandOwnership': d.allow_expand_ownership,
            'bindRole': d.bind_role,
            'reason': d.reason,
            'liveRepairDebt': d.live_repair_debt,
        }
        if after_kind == 'REPAIR' and not d.allow_expand_ownership and d.bind_role == 'REPAIR':
            self.transitionBlocks += 1
            ev['event'] = 'EXPAND_OWNERSHIP_SUSPENDED_REPAIR_FIRST'
            self.transitionEvents.append(ev)
            return None
        self.transitionEvents.append(ev)
        return super()._score_state(t, after_kind)

    def run_transition_frontier(self, models, winner):
        r = self.run_passive_evidence(models, winner)
        r.update({
            'responsibilityTransitionPolicy': self.transitionPolicy.name,
            'repairResponsibilityFrontier': self.repairResponsibilityFrontier.name,
            'transitionChecks': self.transitionChecks,
            'transitionBlocks': self.transitionBlocks,
            'frontierV2Binds': self.frontierV2Binds,
            'transitionEvents': self.transitionEvents[:400],
        })
        return r


def slim(r):
    return {
        'pnlDiagnosticOnly': float(r.get('pnlDiagnosticOnly') or 0.0),
        'floor': float(r.get('floor') or 0.0),
        'fills': int(r.get('actualFillEvents') or 0),
        'rounds': int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),
        'repairParentBirths': int(r.get('repairParentBirths') or 0),
        'repairParentCompletions': int(r.get('repairParentCompletions') or 0),
        'parallelRepairSubmits': int(r.get('parallelRepairSubmits') or 0),
        'parallelRepairActiveFillQty': float(r.get('parallelRepairActiveFillQty') or 0.0),
        'transitionChecks': int(r.get('transitionChecks') or 0),
        'transitionBlocks': int(r.get('transitionBlocks') or 0),
        'frontierV2Binds': int(r.get('frontierV2Binds') or 0),
    }


def main():
    ap = argparse.ArgumentParser()
    for n in [
        'bundle', 'lifecycle-model', 'capability-model', 'dagger-cache', 'timing-model',
        'economic-model', 'price-model', 'surplus-model', 'v44-model', 'v47-model'
    ]:
        ap.add_argument('--' + n, required=True)
    ap.add_argument('--market-id', type=int, required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='parent_occ_transition_frontier_ab_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr = {int(x['marketId']): x for x in json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']}[int(a.market_id)]
        models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape = tmp / 'tapes' / f'{a.market_id}.json.xz'

        b = pe.make(pe.PassiveEvidenceParentOccupancyHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            br = b.run_passive_evidence(models, cr['winner'])
            bcons, bbound, _ = pe.alloc(b, br)
        finally:
            b.close()

        c = pe.make(TransitionFrontierParentOccupancyHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            rr = c.run_transition_frontier(models, cr['winner'])
            cons, bound, parents = pe.alloc(c, rr)
        finally:
            c.close()

        bs = pe.safety(br)
        ss = pe.safety(rr)
        occ = rr.get('occupancyParents') or {}
        occupancy_bound = all(float(p.get('overReservedQty') or 0.0) <= EPS for p in occ.values())
        safety_zero = all(float(v or 0.0) <= EPS for v in ss.values())
        gates = {
            'controlTruthMismatchReproduced': float(bs.get('truthMismatch') or 0.0) > EPS,
            'transitionPolicyActive': rr.get('responsibilityTransitionPolicy') == 'repair_first_existing_thesis_transition_v1',
            'authoritativeFrontierActive': rr.get('repairResponsibilityFrontier') == 'authoritative_current_parent_plus_overflow_repair_frontier_v2',
            'frontierCurrentParentBindExercised': int(rr.get('frontierV2Binds') or 0) > 0,
            'transitionBlockExercised': int(rr.get('transitionBlocks') or 0) > 0,
            'candidateTruthMismatchZero': float(ss.get('truthMismatch') or 0.0) <= EPS,
            'candidateSafetyZero': safety_zero,
            'allocationConservation': bool(cons),
            'allocationParentDebtBounded': bool(bound),
            'executionOccupancyBounded': occupancy_bound,
            'floorNonWorseThanControl': float(rr.get('floor') or 0.0) >= float(br.get('floor') or 0.0) - 1e-7,
        }
        decision = 'KEEP_TRANSITION_FRONTIER_IN_LATEST_CANDIDATE' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_TRANSITION_FRONTIER_INTEGRATION'
        out = {
            'version': 'ETH_PARENT_OCCUPANCY_TRANSITION_FRONTIER_AB_V1',
            'date': '2026-09-05',
            'marketId': int(a.market_id),
            'winnerPostHocOnly': cr['winner'],
            'decision': decision,
            'gates': gates,
            'control': slim(br),
            'candidate': slim(rr),
            'controlSafety': bs,
            'candidateSafety': ss,
            'allocationParents': parents,
            'transitionEvents': rr.get('transitionEvents', [])[:400],
            'boundary': [
                'adds only already-validated RepairFirstResponsibilityTransitionV1 + AuthoritativeRepairResponsibilityFrontierV2',
                'parent occupancy / passive evidence / AllocationLedger V2 frozen',
                'no threshold/qty/price/timing tuning',
                'winner post-hoc only; no Target runtime input; no dream fill; no 8781',
            ],
        }
        Path(a.output).write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'decision': decision, 'gates': gates, 'control': out['control'], 'candidate': out['candidate'], 'controlSafety': bs, 'candidateSafety': ss}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
