"""Read-only source/result review; creates only the GPT6 audit artifacts."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
P0 = ROOT / 'data/research/r4_v0/p0_provenance_v1'
EPS = 1e-7


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def existing_audit():
    plan = (P0 / 'GPT6_BTC5M_SYSTEM_GAP_CHALLENGE_PLAN_V1_20260905.md').read_text(encoding='utf-8')
    names = re.findall(r'`([^`\n]+\.(?:json|md|py))`', plan)
    manifest = []
    for name in dict.fromkeys(names):
        if name.startswith('GPT6_') and 'SYSTEM_GAP_CHALLENGE_PLAN' not in name:
            continue
        f = ROOT / name if '/' in name else P0 / name
        if not f.is_file():
            manifest.append(dict(path=name, exists=False))
            continue
        data = f.read_bytes()
        manifest.append(dict(path=str(f.relative_to(ROOT)), exists=True, bytes=len(data),
                             sha256=hashlib.sha256(data).hexdigest()))
    lease = read(P0 / 'ETH_MULTISLOT_ECONOMIC_HANDOFF_LEASE_LOCK_1946475_RESULT_20260905.json')
    hybrid = read(P0 / 'ETH_MULTISLOT_HYBRID_PRICE_FANOUT_1946475_RESULT_20260905.json')
    h100 = read(P0 / 'ETH_V53_OUR_TARGET_SAME_MARKET_MULTICYCLE_COMPARISON_20260902.json')
    checks = []
    for name, result in [('ECONOMIC_LEASE', lease), ('SYMMETRIC_HYBRID', hybrid)]:
        parents = result['allocationParents']
        checks.append(dict(control=name, fills=result['candidate']['fills'],
            semanticRounds=result['candidate']['rounds'],
            legacyEconomicCompletions=result['candidate']['repairParentCompletions'],
            paidDebtCompletions=sum(p['initialDebt'] > 0 and p['remainingDebt'] <= EPS for p in parents.values()),
            repairPaid=sum(p['repairPaid'] for p in parents.values()),
            remainingDebt=sum(p['remainingDebt'] for p in parents.values()),
            pnl=result['candidate']['pnlDiagnosticOnly']))
    v41 = read(P0 / 'TARGET_BTC_ETH_REEXPAND_GLOBAL_RESERVE_V41.json')['groups']['ETH']
    v41b = read(P0 / 'TARGET_BTC_ETH_REEXPAND_RECOVERABILITY_V41B.json')['assets']['ETH']
    funnel = read(P0 / 'ETH_TRANSITION_CORRECT_EXPAND_ELIGIBILITY_FUNNEL_SHADOW_V1_20260904.json')
    out = dict(version='GPT6_EVIDENCE_AUDIT_V1', researchOnly=True, sourceManifest=manifest,
        current1946475CompletionSemantics=checks, h100RawAggregate=h100['aggregate'],
        fresh4Funnel=funnel,
        denominatorAudit=dict(V41FirstEpisodeProgressMedian=v41['FIRST_REEXPAND_PER_EPISODE']['repairProgressFrac']['median'],
            V41BAllReexpandProgressMedian=v41b['ALL']['repairProgressFrac']['median'],
            V41BFormula='episode_repaired / episode_initial; later expansions increase debt but not episode_initial',
            interpretation='Unbounded cumulative turnover ratio; P75_100 also contains ratios above 1. Do not treat as a bounded current-debt payment fraction.'),
        conclusions=[
            '1946475 legacy completion 0 is economic semantics, not proof of unpaid share debt.',
            'Role-separation can change execution cost without improving terminal paid debt or RER rounds.',
            'H100 is historical ETH; not a fresh BTC5M candidate performance cohort.',
            'Missing Priority-0 price inference blocks new Target price architecture claims.'
        ])
    save(P0 / 'GPT6_EVIDENCE_AUDIT_V1_20260905.json', out)
    return dict(evidenceFiles=sum(x['exists'] for x in manifest), completionAudit=checks,
                h100Rounds={k: h100['aggregate'].get(k) for k in ['ourRepairExpandRepairRounds', 'targetRepairExpandRepairRounds']})


def evaluate(result_dir):
    rd = Path(result_dir)
    data = {c: read(rd / f'cell_{c}.json') for c in 'ABCD'}
    a, b, c, d = [data[k]['metrics'] for k in 'ABCD']
    legacy = dict(A=read(P0 / 'ETH_MULTISLOT_ECONOMIC_HANDOFF_LEASE_LOCK_1946475_RESULT_20260905.json')['candidate'],
                  C=read(P0 / 'ETH_MULTISLOT_HYBRID_PRICE_FANOUT_1946475_RESULT_20260905.json')['candidate'])
    mappings = {'fills':'actualFillEvents', 'rounds':'semanticRounds', 'floor':'floor',
                'pnlDiagnosticOnly':'pnlDiagnosticOnly', 'repairParentBirths':'repairParentBirths',
                'repairParentCompletions':'repairParentCompletions'}
    reproduce = {cell: {k: data[cell]['metrics'][mappings[k]] is not None and
                              abs(float(v)-float(data[cell]['metrics'][mappings[k]])) <= EPS
                       for k,v in baseline.items() if k in mappings}
                 for cell, baseline in legacy.items()}
    roles = data['D']['roles']
    cores = {k for k,v in roles.items() if v['role']=='ECONOMIC_CORE'}
    satellites = {k for k,v in roles.items() if v['role']=='PRIORITY_SATELLITE'}
    cancel_events = data['D']['raw'].get('repeatedRollingEvents', [])
    core_chases = [r for r in cancel_events if r.get('event')=='REPEATED_ROLLING_CANCEL_REQUEST'
                   and r.get('key') in cores and r.get('reason')=='FRONTIER_REANCHOR']
    core_bindings = [r for r in data['D']['roleEvents'] if r.get('event')=='ROLE_REPLACEMENT_BOUND' and r.get('role')=='ECONOMIC_CORE']
    economic_fields = ['pnlDiagnosticOnly','floor','worstObservedFloor','terminalBest']
    nonworse = {field: d[field] is not None and a[field] is not None and d[field]>=a[field]-EPS
                for field in economic_fields}
    lifecycle = dict(
        roundsIncrease=d['semanticRounds'] is not None and a['semanticRounds'] is not None and d['semanticRounds']>a['semanticRounds'],
        paymentAreaImproves=d['sampledDebtTrackingAreaShareSeconds'] is not None and a['sampledDebtTrackingAreaShareSeconds'] is not None
            and d['sampledDebtTrackingAreaShareSeconds']<a['sampledDebtTrackingAreaShareSeconds']-EPS,
        paymentCompletionEarlier=d['sampledAllDebtPaidAt'] is not None and a['sampledAllDebtPaidAt'] is not None
            and d['sampledAllDebtPaidAt']<a['sampledAllDebtPaidAt'])
    gates = dict(controlsReproduced=all(all(v.values()) for v in reproduce.values()),
        allCellsSafetyAccounting=all(all(x['checks'].values()) for x in data.values()),
        coreMaterialized=bool(core_bindings), coreNeverOrdinaryReanchored=bool(cores) and not core_chases,
        satelliteSubmitted=bool(satellites), satelliteConfirmedFill=d['satelliteFillQty']>1e-9,
        physicalLifecycleEffect=any(lifecycle.values()), strongEconomicGate=all(nonworse.values()),
        betterThanFullPriority=d['pnlDiagnosticOnly']>-0.9282608695652179+1e-9 and d['floor']>-0.9282608695652179+1e-9)
    if not gates['controlsReproduced']:
        failure='PROVENANCE_CONTROL_MISMATCH'
    elif not gates['allCellsSafetyAccounting']:
        failure='SAFETY_INVARIANT_FAIL'
    elif not gates['coreMaterialized'] or not gates['coreNeverOrdinaryReanchored']:
        failure='IMPLEMENTATION_WRONG'
    elif not gates['satelliteConfirmedFill']:
        failure='PHYSICAL_EXECUTION_FAIL'
    elif not gates['strongEconomicGate']:
        failure='ECONOMIC_PARAMETERIZATION_FAIL'
    elif not gates['physicalLifecycleEffect']:
        failure='DIAGNOSIS_WRONG_NO_LIFECYCLE_EFFECT'
    else:
        failure=None
    decision='NEEDS_REPLICATION' if all(gates.values()) else 'REJECT'
    deltas={field: {cell: data[cell]['metrics'][field]-a[field] for cell in 'BCD'}
            for field in ['pnlDiagnosticOnly','floor','terminalBest','sampledDebtTrackingAreaShareSeconds','actualFillEvents','ledgerRepairPaid']
            if a[field] is not None and all(data[cell]['metrics'][field] is not None for cell in 'BCD')}
    interactions={field: d[field]-b[field]-c[field]+a[field] for field in ['pnlDiagnosticOnly','floor','sampledDebtTrackingAreaShareSeconds']
                  if all(x[field] is not None for x in [a,b,c,d])}
    out=dict(version='GPT6_REPAIR_CANDIDATE_V1_RESULT', marketId=1946475, consumed=True, researchOnly=True,
        decision=decision, failureClassification=failure, gates=gates, controlReproduction=reproduce,
        economicNonworseVsA=nonworse, lifecycleGates=lifecycle, metrics={k:v['metrics'] for k,v in data.items()},
        deltaVsA=deltas, factorialInteractionDminusBminusCplusA=interactions,
        coreReanchorViolations=core_chases, sourceDirectory=str(rd),
        promotion=False, nextStageAuthorized=decision=='NEEDS_REPLICATION',
        competingHypothesis='The cheaper baseline already pays all debt; priority changes timing and displacement cost, while role identity alone cannot create admission or favorable lifecycle value.',
        interpretation='Sampled debt tracking area uses observed post-process states. Confirmed fills are replay outcomes under the inherited simulator. No fresh/BTC5M/positive-PnL claim.')
    save(P0 / 'GPT6_REPAIR_CANDIDATE_V1_RESULT_1946475_20260905.json', out)
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--result-dir')
    args=ap.parse_args()
    print(json.dumps(existing_audit()))
    if args.result_dir:
        out=evaluate(args.result_dir)
        print(json.dumps({k:out[k] for k in ['decision','failureClassification','gates','metrics','factorialInteractionDminusBminusCplusA']}))


if __name__=='__main__':main()
