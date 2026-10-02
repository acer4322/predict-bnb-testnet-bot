"""NATIVE_SELECTION_MARGIN_PREVALENCE_STAGEA16_V1.

Behavior-inert prevalence audit of current native selection margin on consumed/development
markets.  Reuses the locally-supported B2 G/native-reference contract, but does not change
trading behavior and does not score PnL.

Question: outside the four Clock/B2 seams, how often does the current native decision state
actually contain >=2 non-equivalent, currently executable complete bundles with L=TRUE and
A=TRUE?  If such states are absent/rare, do not train a B3 action selector on a decision unit
that usually has no simultaneous choice margin.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_b2_matched_state_topology_contract_smoke4_v1 as b2

# Wider-state audit exposed a DTO-label edge: B2 g_menu marked PASSIVE A=TRUE after
# role_decision even when no complete candidate materialized. Native reference defines
# A at the complete-candidate level, so normalize only this observer label.
_B2_G_MENU_RAW = b2.g_menu
def _prevalence_g_menu(G):
    out = _B2_G_MENU_RAW(G)
    p = out.get('candidates', {}).get('PASSIVE_PRIMARY')
    if p is not None and p.get('identity') is None and p.get('reason') == 'NO_NATIVE_CANDIDATE':
        p['A'] = 'FALSE'
    return out
b2.g_menu = _prevalence_g_menu

clock = b2.clock
base = b2.base
EPS = b2.EPS
TOL = 1e-8
ACCOUNT_TOL = 1e-7
MENU_LIMIT = 16


def stable(x):
    return b2.stable(x)


def digest(x):
    return b2.digest(x)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close(a, b, tol=TOL):
    return math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=tol)


def semantic_feasible(ref):
    rows = []
    for key, v in ref.get('candidates', {}).items():
        if v.get('family') == 'WAIT_CONFIRMED_RELEASE':
            continue
        if v.get('currentExecutable') is False:
            continue
        if v.get('L') == 'TRUE' and v.get('A') == 'TRUE':
            rows.append((key, v))
    # Non-equivalent means family + complete identity, not dictionary key alone.
    seen = {}
    for key, v in rows:
        sem = digest({'family': v.get('family'), 'identity': v.get('identity')})
        seen.setdefault(sem, {'key': key, 'family': v.get('family'), 'identity': stable(v.get('identity'))})
    return list(seen.values())


def count_layer(ref, field):
    rows = []
    for key, v in ref.get('candidates', {}).items():
        if v.get('family') == 'WAIT_CONFIRMED_RELEASE' or v.get('currentExecutable') is False:
            continue
        if v.get(field) == 'TRUE':
            rows.append((key, v))
    return len({digest({'family': v.get('family'), 'identity': v.get('identity')}) for _, v in rows})


def small_state_summary(G, ref, feasible):
    phys = G['physicalNodes']
    resp = [r for r in G['responsibilities'] if r.get('completedAt') is None and float(r.get('remainingQty') or 0.0) > EPS]
    return {
        'phaseOrdinal': int(G['externalInput']['phaseOrdinal']),
        'eventTimestampMs': int(G['externalInput']['eventTimestampMs']),
        'secondsLeft': float(G['externalInput']['secondsLeft']),
        'liveSlots': len(phys),
        'freeSlots': int(G['externalInput']['maxSlots']) - len(phys),
        'cancelPending': sum(bool(n.get('cancelRequested')) for n in phys),
        'responsibilityCount': len(resp),
        'responsibilityOutstanding': {
            s: sum(float(r.get('remainingQty') or 0.0) for r in resp if str(r.get('side')) == s)
            for s in ('UP', 'DOWN')
        },
        'qLadderRoute': None if G.get('qLadder') is None else G['qLadder'].get('route'),
        'pendingActive': G.get('qPendingActive') is not None,
        'recognitionModeCounts': dict(sorted(Counter(G['decisionContract'].get('recognitionByKey', {}).values()).items())),
        'priority': ref.get('priority'),
        'feasibleBundles': feasible,
    }


def audit_current_state(sim, phase, t, qv, end):
    before = b2.behavior_state_digest(sim)
    G = b2.extract_G(sim, 'N', phase, t, qv, end, None, True)
    # Preserve raw guard-relevant qv precision. B2 extract_G stabilizes qv for DTO display/hash,
    # which can collapse 0.6599999999999999 to 0.66 and change strict native inequalities.
    # Hashing remains stable via digest(stable(...)); strategy/native guards are untouched.
    G['externalInput']['qv'] = copy.deepcopy(qv)
    pred = b2.g_menu(copy.deepcopy(G))
    seal = digest({'G': G, 'prediction': pred})
    ref = b2.reference_menu(sim, 'N', phase, t, qv, end, None, True, None)
    err = b2.menu_errors(pred, ref)
    feasible = semantic_feasible(ref)
    after = b2.behavior_state_digest(sim)
    return {
        'rawGuardQvRepr': {ss: {kk: repr(qv[ss][kk]) for kk in ('bid','ask')} for ss in ('UP','DOWN') if ss in qv},
        'stateHash': digest(G),
        'predictionSealDigest': seal,
        'predictionBeforeReference': True,
        'observerMutationInert': before == after,
        'menuScopePass': max(int(pred.get('universeSize') or 0), int(ref.get('universeSize') or 0)) <= MENU_LIMIT,
        'errorsClean': bool(err.get('clean')),
        'errors': err,
        'fullFeasibleCount': len(feasible),
        'physicalFeasibleCount': count_layer(ref, 'L'),
        'managementEligibleCount': count_layer(ref, 'A'),
        'summary': small_state_summary(G, ref, feasible),
        'G': G,
        'prediction': pred,
        'reference': ref,
    }


def run_path(tape, spec, observe=False, max_witnesses=24):
    Sim = b2.ObsInstrumented if observe else clock.InstrumentedFork
    sim = Sim(tape, spec, 'II', 'N')
    audit_counts = Counter()
    physical_counts = Counter()
    eligible_counts = Counter()
    priority_counts = Counter()
    family_combo_counts = Counter()
    witnesses = []
    clean_examples = []
    sample_negative_controls = []
    error_witnesses = []
    all_clean = True
    all_inert = True
    scope_clean = True
    seed_phase = None
    try:
        updates = sorted(sim.payload['updates'], key=lambda u: (int(u[1]), int(u[0])))
        first = int(sim.meta['firstReceivedMs'])
        base.v2.base.ex.advance_to(sim.bt, first)
        end = int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for ordinal, u in enumerate(updates):
            t = int(u[1])
            sim.set_phase(ordinal, t)
            base.v2.base.ex.advance_to(sim.bt, t)
            sim.process(t)
            sim.cancel_expired(t)
            sim._refresh_slots(t)
            base.v2.base.apply(sim.book, u)
            qv = base.v2.base.quotes(sim.book)
            if qv:
                sim._risk_contract_if_needed(t)
                sim._reanchor_stale(t)

            # Census only on current native pre-action states after seed admission and while
            # new exposure is still legally permitted by the frozen <=180s boundary.
            if observe and qv and seed_phase is not None and ordinal > seed_phase and (end - t) > int(base.v2.NO_NEW_EXPOSURE_MS):
                a = audit_current_state(sim, ordinal, t, qv, end)
                c = int(a['fullFeasibleCount'])
                audit_counts['0' if c == 0 else ('1' if c == 1 else '2plus')] += 1
                physical_counts[str(min(int(a['physicalFeasibleCount']), 2)) if int(a['physicalFeasibleCount']) < 2 else '2plus'] += 1
                eligible_counts[str(min(int(a['managementEligibleCount']), 2)) if int(a['managementEligibleCount']) < 2 else '2plus'] += 1
                priority_counts[str(a['summary']['priority'])] += 1
                fams = tuple(sorted(x['family'] for x in a['summary']['feasibleBundles']))
                family_combo_counts['+'.join(fams) if fams else 'NONE'] += 1
                all_clean = all_clean and bool(a['errorsClean']) and bool(a['predictionBeforeReference'])
                all_inert = all_inert and bool(a['observerMutationInert'])
                scope_clean = scope_clean and bool(a['menuScopePass'])
                if (not a['errorsClean']) and len(error_witnesses) < 12:
                    error_witnesses.append({'phaseOrdinal': int(ordinal), 'eventTimestampMs': int(t), 'stateHash': a['stateHash'], 'errors': a['errors'], 'summary': a['summary'], 'prediction': a['prediction'], 'reference': a['reference'], 'G': a['G'], 'rawGuardQvRepr': a.get('rawGuardQvRepr')})
                if len(clean_examples) < 3:
                    clean_examples.append({k: a[k] for k in ('stateHash', 'fullFeasibleCount', 'physicalFeasibleCount', 'managementEligibleCount', 'summary')})
                    neg = b2.negative_controls(a['G'], a['prediction'])
                    sample_negative_controls.append(neg)
                if c >= 2 and len(witnesses) < int(max_witnesses):
                    witnesses.append({k: a[k] for k in ('stateHash', 'fullFeasibleCount', 'physicalFeasibleCount', 'managementEligibleCount', 'summary')})
                    if len(sample_negative_controls) < 6:
                        sample_negative_controls.append(b2.negative_controls(a['G'], a['prediction']))

            before_seed = bool(getattr(sim, 'postSeed', False))
            if qv:
                sim._open_one_option(t, qv, end)
            if (not before_seed) and bool(getattr(sim, 'postSeed', False)):
                seed_phase = int(ordinal)
                if hasattr(sim, 'seedPhaseOrdinal'):
                    sim.seedPhaseOrdinal = int(ordinal)
            sim._sample_occupancy()

        fin = clock.finalize(sim, spec, None)
        total = sum(audit_counts.values())
        return {
            'behaviorLedgerDigest': fin['behaviorLedgerDigest'],
            'accountingChecks': fin['accountingChecks'],
            'terminalMaxSlots': int(fin['terminal']['maxSlots']),
            'seedPhysical': stable(fin.get('seedPhysical')),
            'seedAdmissionEvents': stable(fin.get('seedAdmissionEvents')),
            'seedPhaseOrdinal': seed_phase,
            'audit': {
                'statesAudited': total,
                'fullFeasibleDistribution': dict(audit_counts),
                'physicalFeasibleDistribution': dict(physical_counts),
                'managementEligibleDistribution': dict(eligible_counts),
                'priorityDistribution': dict(priority_counts),
                'feasibleFamilyCombinationDistribution': dict(family_combo_counts),
                'marginStates': int(audit_counts.get('2plus', 0)),
                'marginRate': None if total == 0 else float(audit_counts.get('2plus', 0)) / float(total),
                'witnesses': witnesses,
                'cleanExamples': clean_examples,
                'allMenuReferenceClean': bool(all_clean),
                'allObserverMutationInert': bool(all_inert),
                'allMenuScopeWithin16': bool(scope_clean),
                'sampleNegativeControls': sample_negative_controls,
                'menuErrorWitnesses': error_witnesses,
            } if observe else None,
        }
    finally:
        sim.close()


def seed_matches_cache(seed, spec):
    if not seed:
        return False
    return (
        str(seed.get('side')) == str(spec.get('seedSideFromCache'))
        and str(seed.get('role')) == 'ECONOMIC_CORE'
        and close(seed.get('price'), spec.get('seedPriceFromCache'))
        and close(seed.get('qty'), spec.get('seedQtyFromCache'))
    )


def aggregate_row(mid, spec, B, O):
    a = O['audit']
    neg_ok = all(
        bool(x.get('ID_SHAM')) and bool(x.get('NATIVE_VIEW_SHAM')) and bool(x.get('EDGE_UNKNOWN'))
        for x in a['sampleNegativeControls']
    )
    checks = {
        'baselineObserverBehaviorParity': B['behaviorLedgerDigest'] == O['behaviorLedgerDigest'],
        'baselineObserverSeedPhysicalParity': stable(B['seedPhysical']) == stable(O['seedPhysical']),
        'baselineObserverSeedAdmissionParity': stable(B['seedAdmissionEvents']) == stable(O['seedAdmissionEvents']),
        'seedTriggeredAndMatchesOutcomeBlindCache': seed_matches_cache(O['seedPhysical'], spec),
        'observerMenuReferenceCleanEveryState': bool(a['allMenuReferenceClean']),
        'observerMutationInertEveryState': bool(a['allObserverMutationInert']),
        'menuScopeWithin16EveryState': bool(a['allMenuScopeWithin16']),
        'sampleNegativeControlsPass': bool(neg_ok),
        'exactFifoAccountingClean': all(bool(v) for v in O['accountingChecks'].values()) and all(bool(v) for v in B['accountingChecks'].values()),
        'max4': int(O['terminalMaxSlots']) <= 4 and int(B['terminalMaxSlots']) <= 4,
        'statesAuditedPositive': int(a['statesAudited']) > 0,
    }
    return {
        'marketId': int(mid),
        'seedT': int(spec['t']),
        'checks': checks,
        'correctnessPass': all(checks.values()),
        'statesAudited': int(a['statesAudited']),
        'fullFeasibleDistribution': a['fullFeasibleDistribution'],
        'physicalFeasibleDistribution': a['physicalFeasibleDistribution'],
        'managementEligibleDistribution': a['managementEligibleDistribution'],
        'priorityDistribution': a['priorityDistribution'],
        'feasibleFamilyCombinationDistribution': a['feasibleFamilyCombinationDistribution'],
        'marginStates': int(a['marginStates']),
        'marginRate': a['marginRate'],
        'marginWitnesses': a['witnesses'],
        'menuErrorWitnesses': a.get('menuErrorWitnesses', []),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--cohort', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--max-markets', type=int, default=16)
    ap.add_argument('--max-witnesses-per-market', type=int, default=24)
    a = ap.parse_args()

    cohort_payload = json.loads(Path(a.cohort).read_text(encoding='utf-8'))
    states = list(cohort_payload['states'])[: int(a.max_markets)]
    outdir = Path(os.environ.get('BTC5M_LAN_RESULT_DIR', '.'))
    rows = []

    with tempfile.TemporaryDirectory(prefix='native_margin_prevalence_') as td:
        root = Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            names = set(z.namelist())
            for s in states:
                member = f"tapes/{int(s['marketId'])}.json.xz"
                if member not in names:
                    raise RuntimeError(f'MISSING_TAPE:{member}')
                z.extract(member, root)

        for i, spec in enumerate(states, 1):
            mid = int(spec['marketId'])
            tape = root / 'tapes' / f'{mid}.json.xz'
            B = run_path(tape, spec, observe=False, max_witnesses=a.max_witnesses_per_market)
            O = run_path(tape, spec, observe=True, max_witnesses=a.max_witnesses_per_market)
            row = aggregate_row(mid, spec, B, O)
            rows.append(row)
            (outdir / 'rows.jsonl').write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in rows) + '\n', encoding='utf-8')
            print(json.dumps({
                'progress': i,
                'of': len(states),
                'marketId': mid,
                'correct': row['correctnessPass'],
                'states': row['statesAudited'],
                'marginStates': row['marginStates'],
                'marginRate': row['marginRate'],
            }, ensure_ascii=False), flush=True)

    all_correct = all(r['correctnessPass'] for r in rows)
    total_states = sum(r['statesAudited'] for r in rows)
    total_margin = sum(r['marginStates'] for r in rows)
    margin_markets = [r['marketId'] for r in rows if r['marginStates'] > 0]
    dist = Counter()
    pdist = Counter()
    adist = Counter()
    combos = Counter()
    for r in rows:
        dist.update(r['fullFeasibleDistribution'])
        pdist.update(r['physicalFeasibleDistribution'])
        adist.update(r['managementEligibleDistribution'])
        combos.update(r['feasibleFamilyCombinationDistribution'])

    if not all_correct:
        verdict = 'CORRECTNESS_STOP'
    elif total_margin == 0:
        verdict = 'NATIVE_SELECTION_MARGIN_ABSENT_STAGEA16'
    elif len(margin_markets) >= 4 and total_margin >= 20:
        verdict = 'NATIVE_SELECTION_MARGIN_PRESENT_STAGEA16'
    else:
        verdict = 'NATIVE_SELECTION_MARGIN_RARE_STAGEA16'

    if verdict == 'NATIVE_SELECTION_MARGIN_PRESENT_STAGEA16':
        next_disposition = 'MARGIN_COHORT_AVAILABLE_FOR_SCOPED_B3_PREREG_ONLY'
    elif verdict in {'NATIVE_SELECTION_MARGIN_ABSENT_STAGEA16', 'NATIVE_SELECTION_MARGIN_RARE_STAGEA16'}:
        next_disposition = 'REASSESS_DECISION_UNIT_BEFORE_B3_SELECTOR'
    else:
        next_disposition = 'NO_RESEARCH_INFERENCE'

    out = {
        'version': 'NATIVE_SELECTION_MARGIN_PREVALENCE_STAGEA16_V1_20260908',
        'researchOnly': True,
        'runtimeAuthority': False,
        'consumedDevelopmentOnly': True,
        'allCorrectnessPass': all_correct,
        'summary': {
            'markets': [r['marketId'] for r in rows],
            'marketsN': len(rows),
            'statesAudited': total_states,
            'marginStates': total_margin,
            'marginMarkets': margin_markets,
            'marginMarketCount': len(margin_markets),
            'marginStateRate': None if total_states == 0 else total_margin / total_states,
            'fullFeasibleDistribution': dict(dist),
            'physicalFeasibleDistribution': dict(pdist),
            'managementEligibleDistribution': dict(adist),
            'feasibleFamilyCombinationDistribution': dict(combos),
        },
        'rows': rows,
        'verdict': verdict,
        'nextDisposition': next_disposition,
        'b5Status': 'B5_ECONOMIC_NULL_NOT_TESTED',
        'alphaStatus': 'NO_ALPHA_PROMOTION',
        'boundaries': [
            'native N path only; II immediate recognition is parity-native',
            'audit begins after outcome-blind earliest native ECONOMIC_CORE seed and stops before frozen <=180s new-exposure boundary',
            'every audited state is pre-action/current-event and strict-past',
            'B2 G prediction sealed before detached native reference',
            'no query mutation/no counterfactual suffix/no new trading policy',
            'selection margin means >=2 non-equivalent currently executable complete bundles with L=TRUE and A=TRUE in frozen native candidate universe',
            'WAIT/replacement contingent bundles are not counted as current selection margin',
            'no PnL/winner/future fill input or success gate',
            'no B3 selector/direction model',
            'no new freeSlots/rank/age/fixed-time rule',
            'no B1/H2/H4 expansion',
            'realistic HFT/no dream fill/no fresh holdout/no live8781',
        ],
        'sha256': {
            'runner': sha(Path(__file__)),
            'cohort': sha(a.cohort),
            'bundle': sha(a.bundle),
            'b2Runner': sha(Path(b2.__file__)),
        },
    }
    op = (outdir / 'result.json') if str(a.output).upper() == 'AUTO' else Path(a.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'verdict': verdict, 'nextDisposition': next_disposition, 'summary': out['summary']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
