from __future__ import annotations

import argparse
import os
import collections
import json
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
import sys

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

import importlib
import importlib.util
import joblib


def _json_default(o):
    if hasattr(o, 'tolist'):
        return o.tolist()
    if hasattr(o, 'item'):
        try:
            return o.item()
        except Exception:
            pass
    return str(o)

def _load_or_staged(fullname: str, filename: str):
    print(f'IMPORT_STAGE {fullname} START', flush=True)
    try:
        mod = importlib.import_module(fullname)
        print(f'IMPORT_STAGE {fullname} PASS project', flush=True)
        return mod
    except ImportError:
        q = Path(__file__).with_name(filename)
        spec = importlib.util.spec_from_file_location(fullname, q)
        if spec is None or spec.loader is None:
            raise ImportError(q)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = mod
        spec.loader.exec_module(mod)
        print(f'IMPORT_STAGE {fullname} PASS staged', flush=True)
        return mod

_load_or_staged('tools.eth_repair_modular.responsibility_transition', 'responsibility_transition.py')
_load_or_staged('tools.eth_repair_modular.responsibility_frontier', 'responsibility_frontier.py')
_load_or_staged('tools.eth_repair_modular.ownership_transition_guard', 'ownership_transition_guard.py')
_load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab', 'run_eth_parent_occupancy_anchorless_parallel_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab', 'run_eth_parent_occupancy_passive_evidence_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab', 'run_eth_parent_occupancy_transition_frontier_ab.py')
pg = _load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab', 'run_eth_parent_occupancy_prospective_guard_ab.py')

pe = pg.pe
EPS = 1e-9


def compact_event_inventory(sim, result):
    inv = {}
    seen = set()
    sources = [('sim', vars(sim)), ('result', result)]
    for source_name, mapping in sources:
        if not isinstance(mapping, dict):
            continue
        for name, value in mapping.items():
            if name in seen or not isinstance(value, list) or not value:
                continue
            if not all(isinstance(x, dict) for x in value[: min(len(value), 20)]):
                continue
            seen.add(name)
            ec = collections.Counter()
            rc = collections.Counter()
            dc = collections.Counter()
            submit_true = 0
            submit_false = 0
            thesis_true = 0
            thesis_false = 0
            for row in value:
                if row.get('event') is not None:
                    ec[str(row.get('event'))] += 1
                if row.get('reason') is not None:
                    rc[str(row.get('reason'))] += 1
                if row.get('decision') is not None:
                    dc[str(row.get('decision'))] += 1
                if 'submit' in row:
                    submit_true += int(bool(row.get('submit')))
                    submit_false += int(not bool(row.get('submit')))
                if 'createThesis' in row:
                    thesis_true += int(bool(row.get('createThesis')))
                    thesis_false += int(not bool(row.get('createThesis')))
            inv[name] = {
                'source': source_name,
                'rows': len(value),
                'eventCounts': dict(ec.most_common(20)),
                'reasonCounts': dict(rc.most_common(20)),
                'decisionCounts': dict(dc.most_common(20)),
                'submitTrue': submit_true,
                'submitFalse': submit_false,
                'createThesisTrue': thesis_true,
                'createThesisFalse': thesis_false,
                'sample': value[:8],
            }
    return inv


def main():
    ap = argparse.ArgumentParser()
    for n in [
        'bundle', 'lifecycle-model', 'capability-model', 'dagger-cache', 'timing-model',
        'economic-model', 'price-model', 'surplus-model', 'v44-model', 'v47-model'
    ]:
        ap.add_argument('--' + n, required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    outp = Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json' if a.output.upper() == 'AUTO' else Path(a.output)

    tmp = Path(tempfile.mkdtemp(prefix='action_liveness_funnel_'))
    stop = threading.Event()
    def hb():
        while not stop.wait(15):
            print(json.dumps({'heartbeat': 'ACTION_LIVENESS_FUNNEL', 'ts': time.time()}), flush=True)
    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({'heartbeat': 'ACTION_LIVENESS_FUNNEL_START', 'marketIds': mids}), flush=True)

    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort = json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']
        by = {int(r['marketId']): r for r in cohort}
        models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']

        rows = []
        for i, mid in enumerate(mids, 1):
            cr = by[mid]
            tape = tmp / 'tapes' / f'{mid}.json.xz'
            sim = pe.make(pg.ProspectiveGuardParentOccupancyHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
            try:
                r = sim.run_guard(models, cr['winner'])
                cons, bound, parents = pe.alloc(sim, r)
                inventory = compact_event_inventory(sim, r)
            finally:
                sim.close()
            ss = pe.safety(r)
            row = {
                'marketId': mid,
                'winnerPostHocOnly': cr['winner'],
                'pnlDiagnosticOnly': float(r.get('pnlDiagnosticOnly') or 0.0),
                'floor': float(r.get('floor') or 0.0),
                'fills': int(r.get('actualFillEvents') or 0),
                'rounds': int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),
                'repairParentBirths': int(r.get('repairParentBirths') or 0),
                'repairParentCompletions': int(r.get('repairParentCompletions') or 0),
                'parallelRepairSubmits': int(r.get('parallelRepairSubmits') or 0),
                'parallelRepairActiveFillQty': float(r.get('parallelRepairActiveFillQty') or 0.0),
                'anchorlessWaitPassiveEvidence': int(r.get('anchorlessWaitPassiveEvidence') or 0),
                'safety': ss,
                'allocationConservation': bool(cons),
                'allocationParentDebtBounded': bool(bound),
                'allocationParents': parents,
                'eventInventory': inventory,
            }
            rows.append(row)
            partial = outp.with_name(f'{outp.stem}.{mid}.partial.json')
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps(row, indent=2, ensure_ascii=False, default=_json_default), encoding='utf-8')
            print(json.dumps({'progress': f'{i}/{len(mids)}', 'marketId': mid, 'fills': row['fills'], 'rounds': row['rounds'], 'births': row['repairParentBirths'], 'completions': row['repairParentCompletions'], 'eventLists': sorted(inventory)}, ensure_ascii=False), flush=True)

        out = {
            'version': 'ETH_ACTION_LIVENESS_FUNNEL_SMOKE4_V1',
            'date': '2026-09-05',
            'researchOnly': True,
            'behaviorChange': False,
            'marketIds': mids,
            'rows': rows,
            'classification': {
                'zeroFillMarkets': [r['marketId'] for r in rows if r['fills'] == 0],
                'startedButNoRoundMarkets': [r['marketId'] for r in rows if r['fills'] > 0 and r['rounds'] == 0],
                'stalledRepairMarkets': [r['marketId'] for r in rows if r['repairParentBirths'] > 0 and r['repairParentCompletions'] == 0 and r['rounds'] == 0],
            },
            'boundary': [
                'behavior-inert event inventory only',
                'current prospective-guard parent-occupancy candidate unchanged',
                'realistic HFT tape only; no dream fill',
                'winner post-hoc only; no Target runtime input; no 8781',
                'purpose is to localize zero-entry and post-entry stalled-repair gates before any threshold or authority mutation'
            ]
        }
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=_json_default), encoding='utf-8')
        print(json.dumps({'ok': True, 'classification': out['classification']}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
