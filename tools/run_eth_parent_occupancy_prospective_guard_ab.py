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

import tools.run_eth_parent_occupancy_transition_frontier_ab as tf
from tools.eth_repair_modular.ownership_transition_guard import (
    ProspectiveOwnershipTransitionContext,
    ProspectiveOwnershipTransitionGuardV1,
)
from tools.eth_repair_modular.responsibility_transition import ResponsibilityTransitionContext

pe = tf.pe
v80 = pe.v80
EPS = 1e-9


class ProspectiveGuardParentOccupancyHFT(tf.TransitionFrontierParentOccupancyHFT):
    def __init__(self, *a, **kw):
        self.prospectiveOwnershipTransitionGuard = ProspectiveOwnershipTransitionGuardV1()
        self.prospectiveTransitionChecks = 0
        self.prospectiveTransitionBlocks = 0
        self.prospectiveTransitionEvents = []
        super().__init__(*a, **kw)

    def _ownership_if_needed(self, t, pE, qv):
        if getattr(self, 'thesis', None) is not None:
            return
        side = self._signal_side(qv)
        rec = self._v75_recoverability(t, side, qv)
        ctx = v80.OwnershipContext(
            t=int(t),
            seconds_left=(int(self.capEnd) - int(t)) / 1000.0,
            has_thesis=False,
            p_expand=float(pE),
            signal_side=side,
            recoverable=bool(rec.get('recoverable')),
        )
        dec = self.policyProfile.ownership.evaluate(ctx)
        row = {
            **rec,
            'event': 'V83_OWNERSHIP_CHECK',
            'pExpand': float(pE),
            'signalSide': side,
            'decision': dec.reason,
            'createThesis': bool(dec.create_thesis),
        }
        self.v75Checks += 1
        allow_birth = bool(dec.create_thesis)
        if dec.create_thesis:
            debt, debt_rows = self._live_repair_debt_by_side()
            td = self.transitionPolicy.evaluate(
                ResponsibilityTransitionContext(dec.side, float(debt['UP']), float(debt['DOWN']))
            )
            gd = self.prospectiveOwnershipTransitionGuard.evaluate(
                ProspectiveOwnershipTransitionContext(
                    dec.side,
                    True,
                    bool(td.allow_expand_ownership),
                    td.bind_role,
                    td.reason,
                )
            )
            self.prospectiveTransitionChecks += 1
            allow_birth = bool(gd.allow_thesis_birth)
            pev = {
                't': int(t),
                'event': 'PROSPECTIVE_OWNERSHIP_TRANSITION_CHECK',
                'candidateSide': dec.side,
                'repairDebtBySide': dict(debt),
                'debtRows': debt_rows,
                'transitionAllow': bool(td.allow_expand_ownership),
                'transitionBindRole': td.bind_role,
                'transitionReason': td.reason,
                'guardReason': gd.reason,
                'allowThesisBirth': allow_birth,
            }
            if not allow_birth:
                self.prospectiveTransitionBlocks += 1
                self.transitionBlocks += 1
                pev['event'] = 'PROSPECTIVE_THESIS_BIRTH_BLOCKED_REPAIR_FIRST'
            self.prospectiveTransitionEvents.append(dict(pev))
            self.transitionEvents.append(dict(pev))
            row.update({
                'prospectiveTransitionAllow': bool(td.allow_expand_ownership),
                'prospectiveTransitionReason': td.reason,
                'prospectiveGuardReason': gd.reason,
                'createThesis': allow_birth,
            })
        if allow_birth:
            self.thesis = {
                'id': self.nextThesisId,
                'side': dec.side,
                'bornAt': int(t),
                'materialized': False,
                'recoveries': 0,
                'opens': 0,
                'birthKind': 'V83_MODULAR_OWNERSHIP',
            }
            self.nextThesisId += 1
            self.v75Births += 1
            self.v75Recoverable += 1
            row['event'] = 'V83_THESIS_BIRTH'
            row['thesisId'] = self.thesis['id']
        self.v75Events.append(dict(row))
        self.v80OwnershipEvents.append(dict(row))

    def run_guard(self, models, winner):
        r = self.run_transition_frontier(models, winner)
        r.update({
            'prospectiveOwnershipTransitionGuard': self.prospectiveOwnershipTransitionGuard.name,
            'prospectiveTransitionChecks': self.prospectiveTransitionChecks,
            'prospectiveTransitionBlocks': self.prospectiveTransitionBlocks,
            'prospectiveTransitionEvents': self.prospectiveTransitionEvents[:400],
        })
        return r


def slim(r):
    x = tf.slim(r)
    x.update({
        'prospectiveTransitionChecks': int(r.get('prospectiveTransitionChecks') or 0),
        'prospectiveTransitionBlocks': int(r.get('prospectiveTransitionBlocks') or 0),
    })
    return x


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

    tmp = Path(tempfile.mkdtemp(prefix='parent_occ_prospective_guard_ab_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr = {int(x['marketId']): x for x in json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']}[int(a.market_id)]
        models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape = tmp / 'tapes' / f'{a.market_id}.json.xz'

        b = pe.make(tf.TransitionFrontierParentOccupancyHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            br = b.run_transition_frontier(models, cr['winner'])
            bcons, bbound, _ = pe.alloc(b, br)
        finally:
            b.close()

        c = pe.make(ProspectiveGuardParentOccupancyHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            rr = c.run_guard(models, cr['winner'])
            cons, bound, parents = pe.alloc(c, rr)
        finally:
            c.close()

        bs = pe.safety(br)
        ss = pe.safety(rr)
        occ = rr.get('occupancyParents') or {}
        occupancy_bound = all(float(p.get('overReservedQty') or 0.0) <= EPS for p in occ.values())
        safety_zero = all(float(v or 0.0) <= EPS for v in ss.values())
        floor_nonworse = float(rr.get('floor') or 0.0) >= float(br.get('floor') or 0.0) - 1e-7
        gates = {
            'controlTruthMismatchReproduced': float(bs.get('truthMismatch') or 0.0) > EPS,
            'guardModuleActive': rr.get('prospectiveOwnershipTransitionGuard') == 'prospective_ownership_transition_guard_v1',
            'prospectiveTransitionExercised': int(rr.get('prospectiveTransitionChecks') or 0) > 0,
            'prospectiveBlockExercised': int(rr.get('prospectiveTransitionBlocks') or 0) > 0,
            'candidateTruthMismatchZero': float(ss.get('truthMismatch') or 0.0) <= EPS,
            'candidateSafetyZero': safety_zero,
            'allocationConservation': bool(cons),
            'allocationParentDebtBounded': bool(bound),
            'executionOccupancyBounded': occupancy_bound,
            'floorNonWorseThanControl': floor_nonworse,
        }
        decision = 'KEEP_PROSPECTIVE_GUARD_IN_LATEST_CANDIDATE' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_PROSPECTIVE_GUARD_INTEGRATION'
        out = {
            'version': 'ETH_PARENT_OCCUPANCY_PROSPECTIVE_OWNERSHIP_GUARD_AB_V1',
            'date': '2026-09-05',
            'marketId': int(a.market_id),
            'winnerPostHocOnly': cr['winner'],
            'decision': decision,
            'gates': gates,
            'controlTransitionFrontier': slim(br),
            'candidateProspectiveGuard': slim(rr),
            'controlSafety': bs,
            'candidateSafety': ss,
            'allocationParents': parents,
            'prospectiveTransitionEvents': rr.get('prospectiveTransitionEvents', [])[:400],
            'candidateV83Admissions': (rr.get('v83Admissions') or [])[:400],
            'boundary': [
                'single integration addition: already-validated ProspectiveOwnershipTransitionGuardV1',
                'ownership side selection unchanged',
                'RepairFirst transition + authoritative Repair frontier frozen',
                'parent occupancy / passive evidence / AllocationLedger V2 frozen',
                'no threshold/qty/price/timing tuning',
                'winner post-hoc only; no Target runtime input; no dream fill; no 8781',
            ],
        }
        Path(a.output).write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'decision': decision, 'gates': gates, 'control': out['controlTransitionFrontier'], 'candidate': out['candidateProspectiveGuard'], 'controlSafety': bs, 'candidateSafety': ss}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
