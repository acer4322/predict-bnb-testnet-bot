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


class RoleTruthDiag(pe.PassiveEvidenceParentOccupancyHFT):
    def __init__(self, *a, **kw):
        self.roleTruthTrace = []
        super().__init__(*a, **kw)

    def submit(self, t, side, p, q):
        before = int(getattr(self, 'authorizedSubmitWithTruthRoleMismatch', 0) or 0)
        n_before = int(self.n)
        pending_role = getattr(self, '_pendingAuthorizedRole', None)
        pending_oid = getattr(self, '_pendingAuthorizedObjectiveId', None)
        pending_parent = getattr(self, '_pendingParentId', None)
        pending_lane = getattr(self, '_pendingLane', None)
        truth_inv = dict(getattr(self, 'truthInv', {}))
        mgr_inv = dict(getattr(self, 'inv', {}))
        rp = dict(self.repairParent) if isinstance(getattr(self, 'repairParent', None), dict) else None
        ok = super().submit(t, side, p, q)
        if not ok:
            return ok
        key = f'{side}_{n_before}'
        after = int(getattr(self, 'authorizedSubmitWithTruthRoleMismatch', 0) or 0)
        row = {
            't': int(t),
            'key': key,
            'side': side,
            'price': float(p),
            'qty': float(q),
            'authorizedRole': getattr(self, 'submitRoleAuthorized', {}).get(key, pending_role),
            'observedRole': getattr(self, 'submitRoleObserved', {}).get(key),
            'truthRole': getattr(self, 'submitRoleTruth', {}).get(key),
            'pendingAuthorizedRole': pending_role,
            'pendingObjectiveId': pending_oid,
            'pendingParentId': pending_parent,
            'pendingLane': pending_lane,
            'repairParentId': int(rp.get('id')) if rp and rp.get('id') is not None else None,
            'repairParentSide': rp.get('side') if rp else None,
            'truthInvBefore': truth_inv,
            'managerInvBefore': mgr_inv,
            'mismatchCounterBefore': before,
            'mismatchCounterAfter': after,
            'mismatchIncrement': max(0, after - before),
        }
        self.roleTruthTrace.append(row)
        return ok

    def run_diag(self, models, winner):
        r = self.run_passive_evidence(models, winner)
        r['roleTruthSubmitTrace'] = self.roleTruthTrace
        return r


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

    tmp = Path(tempfile.mkdtemp(prefix='parent_occ_role_truth_diag_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        by = {int(x['marketId']): x for x in json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']}
        cr = by[int(a.market_id)]
        models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape = tmp / 'tapes' / f'{a.market_id}.json.xz'
        sim = pe.make(RoleTruthDiag, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            r = sim.run_diag(models, cr['winner'])
            cons, bound, parents = pe.alloc(sim, r)
        finally:
            sim.close()
        trace = r.get('roleTruthSubmitTrace') or []
        mismatch = [x for x in trace if int(x.get('mismatchIncrement') or 0) > 0]
        out = {
            'version': 'ETH_PARENT_OCCUPANCY_ROLE_TRUTH_DIAG_V1',
            'marketId': int(a.market_id),
            'winnerPostHocOnly': cr['winner'],
            'summary': {
                'pnlDiagnosticOnly': float(r.get('pnlDiagnosticOnly') or 0),
                'floor': float(r.get('floor') or 0),
                'fills': int(r.get('actualFillEvents') or 0),
                'submits': int(r.get('submits') or 0),
                'truthMismatch': int(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),
                'tracedMismatchIncrements': len(mismatch),
                'repairParentBirths': int(r.get('repairParentBirths') or 0),
                'parallelRepairSubmits': int(r.get('parallelRepairSubmits') or 0),
            },
            'safety': pe.safety(r),
            'allocationConservation': bool(cons),
            'allocationParentDebtBounded': bool(bound),
            'mismatchEvents': mismatch,
            'submitTrace': trace,
            'carrierLedger': getattr(sim, 'carrierLedger', {}),
            'allocationParents': parents,
            'passiveEvidenceEvents': r.get('passiveEvidenceEvents', []),
            'occupancyEvents': r.get('occupancyEvents', []),
            'reservationEvents': r.get('reservationAwareEvents', []),
            'boundary': [
                'instrumentation only; candidate behavior unchanged',
                'winner post-hoc only',
                'no Target runtime input; no dream fill; no 8781',
            ],
        }
        Path(a.output).write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'summary': out['summary'], 'mismatchEvents': mismatch}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
