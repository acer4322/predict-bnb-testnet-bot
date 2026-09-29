from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import importlib
import importlib.util
print("IMPORT_STAGE joblib START", flush=True)
import joblib
print("IMPORT_STAGE joblib PASS", flush=True)

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

def _load_or_staged(fullname: str, filename: str):
    print(f'IMPORT_STAGE {fullname} START', flush=True)
    try:
        mod = importlib.import_module(fullname)
        print(f'IMPORT_STAGE {fullname} PASS project', flush=True)
        return mod
    except ImportError:
        p = Path(__file__).with_name(filename)
        spec = importlib.util.spec_from_file_location(fullname, p)
        if spec is None or spec.loader is None:
            raise ImportError(p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = mod
        spec.loader.exec_module(mod)
        print(f'IMPORT_STAGE {fullname} PASS staged', flush=True)
        return mod

# Worker-staging bootstrap: preload only modules that are known to be newer than the worker tree.
_load_or_staged('tools.eth_repair_modular.responsibility_transition', 'responsibility_transition.py')
_load_or_staged('tools.eth_repair_modular.responsibility_frontier', 'responsibility_frontier.py')
_load_or_staged('tools.eth_repair_modular.ownership_transition_guard', 'ownership_transition_guard.py')
_load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab', 'run_eth_parent_occupancy_anchorless_parallel_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab', 'run_eth_parent_occupancy_passive_evidence_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab', 'run_eth_parent_occupancy_transition_frontier_ab.py')
pg = _load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab', 'run_eth_parent_occupancy_prospective_guard_ab.py')

pe = pg.pe
EPS = 1e-9


class ProspectiveGuardResponsibilityNativeActiveHFT(pg.ProspectiveGuardParentOccupancyHFT):
    """Single seam: a live Repair responsibility can supply Active execution lineage.

    All upstream transition/ownership/economic/venue/parent-occupancy gates remain inherited.
    The only override removes the requirement that a Passive carrier must have materialized or
    churned first in the same parent epoch.
    """

    def _anchorless_execution_evidence(self, pid, st):
        return True

    def run_responsibility_native(self, models, winner):
        r = self.run_guard(models, winner)
        r['responsibilityNativeActiveExecutionV1'] = True
        return r


def slim(r):
    x = pg.slim(r)
    x.update({
        'repairParentCompletions': int(r.get('repairParentCompletions') or 0),
        'anchorlessActiveSubmits': int(r.get('anchorlessActiveSubmits') or 0),
        'anchorlessActiveAllows': int(r.get('anchorlessActiveAllows') or 0),
        'anchorlessWaitPassiveEvidence': int(r.get('anchorlessWaitPassiveEvidence') or 0),
        'passiveEvidenceEpochCount': int(r.get('passiveEvidenceEpochCount') or 0),
        'overflowAllocatedQty': float(r.get('v84OverflowAllocatedQty') or 0.0),
        'overflowPaidQty': float(r.get('v84OverflowPaidQty') or 0.0),
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
    mid = int(a.market_id)

    outp = Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json' if a.output.upper() == 'AUTO' else Path(a.output)
    outp.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f'pg_anchorless_{mid}_'))
    stop = threading.Event()

    def hb():
        while not stop.wait(15):
            print(json.dumps({'heartbeat': 'PG_RESPONSIBILITY_NATIVE_ACTIVE_AB', 'market': mid, 'ts': time.time()}), flush=True)

    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({'heartbeat': 'PG_RESPONSIBILITY_NATIVE_ACTIVE_AB_START', 'market': mid}), flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr = {int(x['marketId']): x for x in json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']}[mid]
        models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape = tmp / 'tapes' / f'{mid}.json.xz'

        control = pe.make(pg.ProspectiveGuardParentOccupancyHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            br = control.run_guard(models, cr['winner'])
            _, _, _ = pe.alloc(control, br)
        finally:
            control.close()

        candidate = pe.make(ProspectiveGuardResponsibilityNativeActiveHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            rr = candidate.run_responsibility_native(models, cr['winner'])
            cons, bound, parents = pe.alloc(candidate, rr)
        finally:
            candidate.close()

        bs = pe.safety(br)
        ss = pe.safety(rr)
        safe = all(float(v or 0.0) <= EPS for v in ss.values())
        occ = rr.get('occupancyParents') or {}
        occupancy_bound = all(float(p.get('overReservedQty') or 0.0) <= EPS for p in occ.values())
        floor_ok = float(rr.get('floor') or 0.0) >= float(br.get('floor') or 0.0) - 1e-7
        active_submit = int(rr.get('anchorlessActiveSubmits') or 0) > 0
        physical = float(rr.get('parallelRepairActiveFillQty') or 0.0) > EPS
        control_safe = all(float(v or 0.0) <= EPS for v in bs.values())
        gates = {
            'controlProspectiveGuardSafe': control_safe,
            'prospectiveGuardStillActive': rr.get('prospectiveOwnershipTransitionGuard') == 'prospective_ownership_transition_guard_v1',
            'responsibilityNativeActiveMaterialized': active_submit,
            'physicalActiveFill': physical,
            'candidateTruthMismatchZero': float(ss.get('truthMismatch') or 0.0) <= EPS,
            'candidateSafetyZero': safe,
            'allocationConservation': bool(cons),
            'allocationParentDebtBounded': bool(bound),
            'executionOccupancyBounded': occupancy_bound,
            'terminalFloorNonWorseThanControl': floor_ok,
        }
        if not safe or not cons or not bound or not occupancy_bound or not floor_ok:
            decision = 'REJECT_RESPONSIBILITY_NATIVE_ACTIVE_SAFETY_OR_FLOOR'
        elif not active_submit:
            decision = 'SAFE_BUT_RESPONSIBILITY_NATIVE_ACTIVE_NOT_MATERIALIZED'
        elif not physical:
            decision = 'RESPONSIBILITY_NATIVE_ACTIVE_SUBMIT_NO_PHYSICAL_FILL'
        else:
            decision = 'FUNCTIONAL_PASS_RESPONSIBILITY_NATIVE_ACTIVE_SECOND_MARKET'

        out = {
            'version': 'ETH_PARENT_OCCUPANCY_PROSPECTIVE_GUARD_RESPONSIBILITY_NATIVE_ACTIVE_AB_V1',
            'date': '2026-09-05',
            'researchOnly': True,
            'runtimeAuthority': False,
            'marketId': mid,
            'winnerPostHocOnly': cr['winner'],
            'decision': decision,
            'gates': gates,
            'controlProspectiveGuard': slim(br),
            'candidateResponsibilityNativeActive': slim(rr),
            'delta': {
                'pnlDiagnosticOnly': float(rr.get('pnlDiagnosticOnly') or 0.0) - float(br.get('pnlDiagnosticOnly') or 0.0),
                'floor': float(rr.get('floor') or 0.0) - float(br.get('floor') or 0.0),
                'fills': int(rr.get('actualFillEvents') or 0) - int(br.get('actualFillEvents') or 0),
                'repairParentCompletions': int(rr.get('repairParentCompletions') or 0) - int(br.get('repairParentCompletions') or 0),
            },
            'controlSafety': bs,
            'candidateSafety': ss,
            'occupancyParents': occ,
            'allocationParents': parents,
            'occupancyEvents': (rr.get('occupancyEvents') or [])[:600],
            'reservationEvents': (rr.get('reservationAwareEvents') or [])[:500],
            'prospectiveTransitionEvents': (rr.get('prospectiveTransitionEvents') or [])[:250],
            'reasonCounts': dict(collections.Counter(e.get('reason') for e in (rr.get('reservationAwareEvents') or []) if e.get('event') == 'RESERVATION_AWARE_SAME_PARENT_EVALUATION')),
            'boundary': [
                'single market 1945986 second informative replication',
                'single behavior seam relative to prospective-guard control: Repair responsibility itself supplies Active execution lineage; no Passive-first evidence requirement',
                'existing armed requirement remains frozen in this smoke',
                'ProspectiveOwnershipTransitionGuardV1 + RepairFirst transition/frontier retained',
                'parent-scoped occupancy binds Passive and Active unresolved commitments',
                'AllocationLedger V2 Repair-first/overflow-second frozen',
                'economic ceiling/legal min/payment progress/<=180 fences frozen',
                'no threshold/qty/price/timing tuning; normalized phase diagnostic only',
                'winner post-hoc only; no Target runtime input; no dream fill; no 8781',
            ],
        }
        outp.write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'decision': decision, 'gates': gates, 'control': out['controlProspectiveGuard'], 'candidate': out['candidateResponsibilityNativeActive'], 'delta': out['delta'], 'candidateSafety': ss}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
