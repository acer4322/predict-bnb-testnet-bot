"""Operator-only D/E experiment. Importing this file never starts research work."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'tools', ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools import run_gpt6_repair_candidate_v1 as v1
from tools.eth_repair_modular.readiness_aware_role_adapter import make_readiness_adapter

INPUTS = ('bundle', 'lifecycle_model', 'capability_model', 'dagger_cache', 'timing_model',
          'economic_model', 'price_model', 'surplus_model', 'v44_model', 'v47_model')
EPS = 1e-7
PINNED_SOURCES = {
    'tools/run_gpt6_repair_candidate_v1.py': 'e3824962352bd33c7c8a55b616a4b76b172eae2a3f4254e124b59d9eca89066d',
    'tools/eth_repair_modular/persistent_execution_roles.py': 'cbf7177ec654fa78fdcaf12a05c941c244006aeec8ec83559a18aa240e342f30',
}
PINNED_INPUTS = {
    'bundle': 'ea5fcb5a525416e1cbce34a0f78a0c8de017be962d51e06b7e07775e6ab8301f',
    'lifecycle_model': '93473679cfe806a8ca47be23cb7f3f0bddf1ae4d4e6700274923c6b4195a2318',
    'capability_model': '4fae8d7171ec4a79799251b02a9d4c49517982045071c375c048d04ab36121e9',
}


def evaluate_cells(cells):
    """Only the operator-run main calls this after collecting actual cells."""
    if set(cells) != {'D', 'E'}:
        return dict(decision='INCOMPLETE', reason='BOTH_CONTEMPORANEOUS_D_AND_E_REQUIRED')
    d, e = cells['D']['metrics'], cells['E']['metrics']
    required = ('actualFillEvents', 'pnlDiagnosticOnly', 'floor', 'semanticRounds',
                'ledgerRepairPaid', 'ledgerRemainingDebt', 'sampledDebtTrackingAreaShareSeconds',
                'sampledAllDebtPaidAt', 'worstObservedFloor', 'terminalBest', 'satelliteFillQty')
    missing = [f'{c}.{k}' for c in ('D', 'E') for k in required if cells[c]['metrics'].get(k) is None]
    if missing:
        return dict(decision='INCOMPLETE', reason='MISSING_REQUIRED_METRICS', fields=missing)
    observed = cells['E']['readinessEvents']
    core_keys = {k for k, r in cells['E']['roles'].items() if r['role'] == 'ECONOMIC_CORE'}
    core_chases = [r for r in cells['E']['raw'].get('repeatedRollingEvents', [])
                   if r.get('event') == 'REPEATED_ROLLING_CANCEL_REQUEST' and
                   r.get('key') in core_keys and r.get('reason') == 'FRONTIER_REANCHOR']
    accepted = [r for r in observed if r['event'] == 'READY_CANCEL_DISPATCH' and r['accepted']]
    replacements = [r for r in observed if r['event'] == 'TERMINAL_SATELLITE_REVALIDATION' and r.get('submitted')]
    economics = {k: e[k] >= d[k] - EPS for k in ('pnlDiagnosticOnly', 'floor', 'worstObservedFloor', 'terminalBest')}
    lifecycle = dict(paymentAreaImproves=e['sampledDebtTrackingAreaShareSeconds'] < d['sampledDebtTrackingAreaShareSeconds'] - EPS,
                     paymentCompletesEarlier=e['sampledAllDebtPaidAt'] < d['sampledAllDebtPaidAt'],
                     roundsIncrease=e['semanticRounds'] > d['semanticRounds'])
    frozen_d = dict(actualFillEvents=3, pnlDiagnosticOnly=-0.2631147540983605,
                    floor=-0.2631147540983605, semanticRounds=0, ledgerRepairPaid=5.0,
                    ledgerRemainingDebt=0.0, sampledDebtTrackingAreaShareSeconds=491.2039344262295)
    gates = dict(
        controlReproduced=all(abs(d[k] - value) <= EPS for k, value in frozen_d.items()),
        originalSafetyAccounting=all(all(cells[c]['checks'].values()) for c in ('D', 'E')),
        readinessWaitExercised=any(r['event'] == 'ORDER_READINESS_OBSERVED' and r['state'] == 'WAIT_READINESS' for r in observed),
        readyCancelAccepted=bool(accepted) and all(r['ready'] for r in accepted),
        noDuplicateAcceptedCancel=len({r['key'] for r in accepted}) == len(accepted),
        terminalReplacement=bool(replacements) and all(r['oldTerminalConfirmed'] for r in replacements),
        upwardRetargetAuthorized=all(r.get('upwardV16', {}).get('allow', False)
            for r in replacements if r['decision'] == 'FRESH_SATELLITE_PROPOSAL' and r['target'] > r['approvedPrice'] + 1e-9),
        corePreserved=bool(core_keys) and not core_chases,
        satelliteConfirmedFill=e['satelliteFillQty'] > 1e-9,
        lifecycleImproves=any(lifecycle.values()), economicNonworse=all(economics.values()),
        beatsHistoricalFullPriority=all(e[k] > -0.9282608695652179 + 1e-9 for k in ('pnlDiagnosticOnly', 'floor')))
    failed = [k for k, ok in gates.items() if not ok]
    return dict(decision='NEEDS_OPERATOR_REVIEW_FOR_REPLICATION' if not failed else 'REJECT',
                promotion=False, gates=gates, failedGates=failed, economics=economics,
                lifecycle=lifecycle, coreReanchorViolations=core_chases,
                coalescedSubmissions=sum(r['decision'] == 'FRESH_SATELLITE_PROPOSAL' for r in replacements),
                unknownCancelOutcomes=[r for r in observed if r['event'] in ('READY_CANCEL_DISPATCH', 'CANCEL_OUTCOME_UNKNOWN')
                                       and (r.get('outcome') == 'CANCEL_OUTCOME_UNKNOWN' or r['event'] == 'CANCEL_OUTCOME_UNKNOWN')],
                boundary='Consumed 1946475; no automatic dispatch, replication or fresh/BTC promotion.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in INPUTS:
        parser.add_argument('--' + name.replace('_', '-'))
    parser.add_argument('--input-spec', help='JSON path mapping for the existing frozen inputs; explicit flags take precedence')
    parser.add_argument('--hft-path')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    if args.input_spec:
        spec = json.loads(Path(args.input_spec).read_text(encoding='utf-8-sig'))
        for key in (*INPUTS, 'hft_path'):
            if getattr(args, key) is None and key in spec:
                setattr(args, key, spec[key])
    missing = [key for key in INPUTS if not getattr(args, key)]
    if missing:
        parser.error('missing input paths: ' + ', '.join(missing))
    for name, expected in PINNED_SOURCES.items():
        if v1.sha(ROOT / name) != expected:
            raise ValueError('supplied baseline source identity mismatch: ' + name)
    files = {key: dict(path=getattr(args, key), sha256=v1.sha(getattr(args, key))) for key in INPUTS}
    for name, expected in PINNED_INPUTS.items():
        if files[name]['sha256'] != expected:
            raise ValueError('supplied frozen input identity mismatch: ' + name)
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)  # Never overwrite an earlier result.
    started = time.time()
    v1.write_json(out / 'partial.json', dict(state='IMPORTING', operatorExecution=True))
    if args.hft_path:
        sys.path.insert(0, str(Path(args.hft_path).resolve(strict=True)))
    import run_eth_dagger60_smoke_v1  # Fail at the leaf before legacy fallback loaders.
    import joblib
    import hftbacktest
    import tools.run_eth_multislot_hybrid_price_fanout_1946475 as hybrid
    sources = {}
    for module in list(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        if filename:
            path = Path(filename).resolve()
            if path.is_relative_to(ROOT) and path.suffix == '.py' and path.is_file():
                sources[str(path.relative_to(ROOT))] = v1.sha(path)
    hft_path = Path(hftbacktest.__file__).resolve()
    if (getattr(hftbacktest, '__version__', None) != '2.4.4' or
            v1.sha(hft_path) != '6982e734eec8a5490e2713cd399edbb5ece7d6d98d883deb012fe1b3b4a6a275'):
        raise ValueError('supplied HftBacktest identity mismatch')
    v1.write_json(out / 'inputs.json', dict(files=files, loadedSources=sources,
        hftDependency=dict(path=str(hft_path), sha256=v1.sha(hft_path), version=getattr(hftbacktest, '__version__', None))))
    pe = hybrid.pe
    models, life, cap, tim, econ, price, sur = pe.v38.v36.v34.v30.load_runtime(args)
    t44 = joblib.load(args.v44_model)['models']['EVENT_VALUE_NORM']
    t47 = joblib.load(args.v47_model)['models']['GENERATION_AWARE_NORM']
    payloads = {}
    with tempfile.TemporaryDirectory(prefix='gpt6_repair_v2_external_') as temp:
        tape = Path(temp) / '1946475.json.xz'
        with zipfile.ZipFile(args.bundle) as archive:
            cohort = json.loads(archive.read('cohort.json'))
            tape.write_bytes(archive.read('tapes/1946475.json.xz'))
        market = next(r for r in cohort['rows'] if int(r['marketId']) == 1946475)
        for cell in ('D', 'E'):
            v1.write_json(out / 'partial.json', dict(state='RUNNING', cell=cell))
            cls = v1.make_adapter(hybrid, True, True) if cell == 'D' else make_readiness_adapter(hybrid)
            sim = pe.make(cls, tape, models, life, cap, tim, econ, price, sur, t44, t47)
            try:
                # Same inherited terminal scorer. No winner is passed to the readiness policy.
                raw = sim.run_hybrid(models, market['winner'])
                sim._capture_observed_state(int(sim.capEnd))
                conservation, bounded, parents = pe.alloc(sim, raw)
                metrics, safety = v1.summarize(sim, raw, parents), pe.safety(raw)
                occupancy = raw.get('occupancyParents')
                checks = dict(allocationConservation=bool(conservation), allocationDebtBounded=bool(bounded),
                    safetyZero=bool(safety) and all(float(v) <= 1e-9 for v in safety.values()),
                    occupancyEvidencePresent=bool(occupancy), observedOccupancyBounded=metrics['maxObservedOverReserved'] <= EPS,
                    metricsPresent=not metrics['missingRequiredFields'])
                payload = dict(metrics=metrics, checks=checks, safety=safety, allocationParents=parents,
                    occupancyParents=occupancy, roles={k: asdict(v) for k, v in sim.executionRoles.entries.items()},
                    roleEvents=sim.roleEvents, readinessEvents=getattr(sim, 'readinessEvents', []),
                    readinessIntents={k: asdict(v) for k, v in getattr(getattr(sim, 'readinessIntents', None), 'entries', {}).items()},
                    carriers=sim.carrierLedger,
                    orders={k: {f: o.get(f) for f in ('n', 'side', 'price', 'qty', 'placed', 'cum', 'status')} for k, o in sim.orders.items()},
                    observedStateTrace=sim.gptTrace, raw=raw)
                payloads[cell] = payload
                v1.write_json(out / f'cell_{cell}.json', payload)
            finally:
                sim.close()
    result = dict(version='GPT6_REPAIR_CANDIDATE_V2', researchOnly=True, consumed=True, marketId=1946475,
        preregistered='GPT6_REPAIR_CANDIDATE_V2_PREREGISTERED_20260905.md',
        cells={c: {k: p[k] for k in ('metrics', 'checks', 'safety')} for c, p in payloads.items()},
        evaluation=evaluate_cells(payloads), elapsedSeconds=time.time() - started)
    v1.write_json(out / 'result.json', result)
    v1.write_json(out / 'partial.json', dict(state='DONE', decision=result['evaluation']['decision']))
    print(json.dumps(dict(output=str(out), evaluation=result['evaluation'])), flush=True)


if __name__ == '__main__':
    main()
