from __future__ import annotations
import json, hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P0=ROOT/'data/research/r4_v0/p0_provenance_v1'
RET=ROOT/'data/research/lan_worker_returns'

PRIMARY=P0/'B3_RETAIN_PENDING_ONCE_PRIMARY1824852_FIRST_RESULT_V1_20260908.json'
REPEAT=RET/'b3-retain-pending-once-primary1824852-repeat-controls-20260908-v1/result.json'
REM=RET/'b3-retain-pending-once-remaining3-anchors-20260908-v1/result.json'
BASE=P0/'B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_BASELINE_COMPLETE_COMPACT_20260908.json'

POOL_NAMES=['R0Burden','R0TerminalResidual','cashAtRiskPeak','grossIntegral','absNetIntegral','burn']
ORDER=[1824852,1824758,1825994,1825962]

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    pri=json.load(open(PRIMARY,encoding='utf-8'))
    rep=json.load(open(REPEAT,encoding='utf-8'))
    rem=json.load(open(REM,encoding='utf-8'))
    base=json.load(open(BASE,encoding='utf-8'))
    rr={int(x['marketId']):x for x in rem['rows']}

    if rep.get('verdict')!='PRIMARY_REPEAT_CANONICAL_PARITY_PASS' or not all(rep.get('comparisons',{}).values()):
        raise RuntimeError('PRIMARY_CANONICAL_REPEAT_STOP')

    rows=[]
    # Primary 1824852: use valid first-run data plus canonical repeat amendment.
    p=pri
    rows.append({
      'marketId':1824852,'role':'PRIMARY','actionReachable':True,'actionExercised':True,
      'effectiveRSource':'PRIMARY_FIRST_RESULT_R_WITH_CANONICAL_REPEAT_AMENDMENT',
      'tStar':{'phaseOrdinal':p['R']['retain']['phaseOrdinal'],'eventTimestampMs':p['R']['retain']['eventTimestampMs']},
      'reentry':p['R']['nativeReentry'],
      'reentryDelayMs':int(p['R']['nativeReentry']['eventTimestampMs'])-int(p['R']['retain']['eventTimestampMs']),
      'physicalFirstStage':p['physicalFirstStage'],'KDeltaRminusC0':p['KDeltaRminusC0'],
      'individualTailChecksR':p['individualTailChecksR'],'individualTailPassR':p['individualTailPassR'],
      'economic':p['economic'],'activity':p['R']['activity'],'sameH0Service':p['R']['sameH0Service'],
      'numericR':p['R']['numeric'],'numericC0':p['C0']['numeric'],
      'correctnessPass':bool(p['R']['correctnessPass'] and all(p['NParity'].values()) and all(p['C0DisabledParity'].values()) and all(rep['comparisons'].values())),
      'verdict':'PRIMARY_MECHANISM_EXERCISED_BUDGET_FAIL' if not p['individualTailPassR'] else 'PRIMARY_MECHANISM_EXERCISED_BOUNDED'
    })

    # 1824758: prereg semantics say action is unreachable when P0 is no longer physically reserved.
    r=rr[1824758]
    retain=(r['R'].get('retain') or {}).get('result') or {}
    checks=retain.get('checks') or {}
    unreachable=(r['actionReachable'] and retain.get('rejected') is True and checks.get('p0PositivePhysicalReservation') is False and checks.get('h0PendingIdentityExact') is True)
    if not unreachable or not all(r['C0DisabledParity'].values()):
        raise RuntimeError('1824758_UNREACHABLE_CLASSIFICATION_STOP')
    # The attempted R branch incorrectly omitted native after rejection; it is invalid and excluded.
    rows.append({
      'marketId':1824758,'role':'CONTEXT_FEASIBLE','actionReachable':False,'actionExercised':False,
      'effectiveRSource':'C0_BY_PREREG_NO_ACTION_WHEN_TRIGGER_UNREACHABLE',
      'invalidAttemptedRExcluded':True,'invalidAttemptReason':'RETAIN_REJECTED_P0_NOT_RESERVED_THEN_RUNNER_FAILED_TO_NATIVE_DELEGATE',
      'detectedNativeCommit':{'phaseOrdinal':r['detectedTStar']['phaseOrdinal'],'eventTimestampMs':r['detectedTStar']['eventTimestampMs']},
      'P0AtCommit':r['detectedTStar']['pre']['p0'],'KDeltaRminusC0':{k:0.0 for k in p['KDeltaRminusC0']},
      'individualTailChecksR':{k:(float(r['C0']['numeric'][k])<=float(v)+1e-7) for k,v in base['newTail'].items() if k in r['C0']['numeric'] and v is not None},
      'individualTailPassR':True,
      'economic':{'RminusC0':{'deltaU':0.0,'deltaD':0.0},'RminusN':{'deltaU':float(r['C0']['terminal']['U'])-float(json.load(open(P0/'B3_PLVAC_FORMAL_SOURCE_1824758_20260908.json',encoding='utf-8'))['branches']['N']['terminalEconomics']['U']),'deltaD':float(r['C0']['terminal']['D'])-float(json.load(open(P0/'B3_PLVAC_FORMAL_SOURCE_1824758_20260908.json',encoding='utf-8'))['branches']['N']['terminalEconomics']['D'])}},
      'activity':r['C0']['activity'],'sameH0Service':r['C0']['sameH0Service'],
      'numericR':r['C0']['numeric'],'numericC0':r['C0']['numeric'],'correctnessPass':True,'verdict':'ACTION_NOT_REACHABLE'
    })

    for mid,role in [(1825994,'SECOND_BUDGET_FAIL_CONTEXT'),(1825962,'BUDGET_FEASIBLE_CONTEXT')]:
        r=rr[mid]
        rows.append({
          'marketId':mid,'role':role,'actionReachable':bool(r['actionReachable']),'actionExercised':bool(r['R'].get('retain') is not None and r['R']['checks'].get('retainAccepted')),
          'effectiveRSource':'REMAINING3_RAW_R','tStar':{'phaseOrdinal':r['R']['retain']['phaseOrdinal'],'eventTimestampMs':r['R']['retain']['eventTimestampMs']},
          'reentry':r['R']['nativeReentry'],'reentryDelayMs':int(r['R']['nativeReentry']['eventTimestampMs'])-int(r['R']['retain']['eventTimestampMs']),
          'physicalFirstStage':r['physicalFirstStage'],'KDeltaRminusC0':r['KDeltaRminusC0'],
          'individualTailChecksR':r['individualTailChecksR'],'individualTailPassR':r['individualTailPassR'],
          'economic':r['economic'],'activity':r['R']['activity'],'sameH0Service':r['R']['sameH0Service'],
          'numericR':r['R']['numeric'],'numericC0':r['C0']['numeric'],
          'correctnessPass':bool(all(r['C0DisabledParity'].values()) and r['R']['correctnessPass']),
          'verdict':'ANCHOR_MECHANISM_EXERCISED_BUDGET_FAIL' if not r['individualTailPassR'] else 'ANCHOR_MECHANISM_EXERCISED_BOUNDED'
        })

    by={x['marketId']:x for x in rows}; rows=[by[m] for m in ORDER]
    pool={}
    for k in POOL_NAMES:
        value=sum(float(x['numericR'][k]) for x in rows)
        cap=float(base['newPool'][k]); pool[k]={'value':value,'cap':cap,'deltaToCap':value-cap,'pass':value<=cap+1e-7}

    coverage={
      'T':sum(bool(x['activity']['trade']) for x in rows),
      'B':sum(bool(x['activity']['twoSided']) for x in rows),
      'R':sum(bool(x['activity']['repeated']) for x in rows)
    }
    reach=sum(bool(x['actionReachable']) for x in rows); exercise=sum(bool(x['actionExercised']) for x in rows)
    correctness=all(x['correctnessPass'] for x in rows)
    indiv=all(x['individualTailPassR'] for x in rows)
    poolpass=all(x['pass'] for x in pool.values())
    service=all((not x['actionExercised']) or bool(x['sameH0Service']['h0ServiceReplacedOrCompleted']) for x in rows)
    anti=coverage=={'T':4,'B':4,'R':4} and service
    cost_effect=all(any(abs(float(v))>1e-7 for v in x['KDeltaRminusC0'].values()) for x in rows if x['actionExercised'])

    if not correctness: verdict='CORRECTNESS_OR_PROVENANCE_STOP'
    elif not anti: verdict='MECHANISM_CONFIRMED_INACTIVITY_OR_OPTION_DESTRUCTION'
    elif not cost_effect: verdict='ACTION_EXERCISED_NO_BUDGET_PATH_EFFECT'
    elif not indiv or not poolpass: verdict='BUDGET_PATH_EFFECT_WITHOUT_FEASIBLE_SEPARATION'
    else: verdict='STAGE1_GO_TO_FIXED_BREADTH_ONLY'

    out={
      'version':'B3_RETAIN_PENDING_ONCE_STAGE1_FIXED4_POSTPROCESS_V1_20260908',
      'researchOnly':True,'runtimeAuthority':False,'markets':ORDER,'rows':rows,
      'summary':{
        'actionReachability':f'{reach}/4','actionExercise':f'{exercise}/4','coverage':coverage,
        'correctnessPass':correctness,'antiCollapsePass':anti,'allIndividualTailPass':indiv,'oldFourPoolPass':poolpass,
        'oldFourPool':pool,'stage2Authorized':verdict=='STAGE1_GO_TO_FIXED_BREADTH_ONLY',
        'branchAccounting':{'primaryInitial':6,'primaryCanonicalRepeatControls':3,'remaining3C0plusR':6,'total':15,'ceiling':15},
        'mainVerdict':verdict,
        'tags':['VALUE_ORDER_REMAINS_NON_TOTAL','MECHANISM_HETEROGENEOUS_NON_TOTAL','NO_ALPHA_PROMOTION','REAL_NET_COST_UNRESOLVED','B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE']
      },
      'provenance':{'primary':sha(PRIMARY),'repeatControls':sha(REPEAT),'remaining3Raw':sha(REM),'baselineCompact':sha(BASE)},
      'boundary':['1824758 invalid attempted R excluded; prereg no-action semantics use C0 exactly when trigger unreachable','no replay in postprocess','no fresh/reserve/8781','no Stage2 because frozen budget gate fails','no belief/selector training']
    }
    compact=P0/'B3_RETAIN_PENDING_ONCE_STAGE1_FIXED4_COMPACT_V1_20260908.json'
    compact.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')

    lines=[]
    lines += ['# B3 RETAIN_PENDING_ONCE — Stage 1 Fixed-Four Report','',f"Date: 2026-09-08  ",f"Verdict: **`{verdict}`**  ",f"Stage 2 authorized: **{out['summary']['stage2Authorized']}**",'']
    lines += ['## Executive result','',f"A1 was reachable in **{reach}/4** anchors and physically exercised in **{exercise}/4**. All four effective paths retained T/B/R; exercised paths had confirmed native re-entry and same-H0 service completion. The action is therefore a real, bounded authority primitive rather than a synthetic HOLD.",'']
    lines += ['However, it does **not** create feasible PLVAC separation. 1824852 and 1825994 still fail individual frozen Tail constraints, and the old-four `cashAtRiskPeak` Pool fails. Therefore the preregistered 10-market breadth stage is not authorized.','']
    lines += ['## Per-market','', '| market | reach/exercise | re-entry | Tail | R−C0 (U,D) | main interpretation |','|---|---|---|---|---|---|']
    for x in rows:
        if x['actionExercised']: re=f"+{x['reentryDelayMs']} ms"
        else: re='N/A'
        e=x['economic']['RminusC0']; td='PASS' if x['individualTailPassR'] else 'FAIL'
        lines.append(f"| {x['marketId']} | {x['actionReachable']}/{x['actionExercised']} | {re} | {td} | ({e['deltaU']:+.6f}, {e['deltaD']:+.6f}) | `{x['verdict']}` |")
    lines += ['','### 1824758 correction','', 'At its first native H0 Active commit, P0 was already `CANCELED` and no longer in slot reservation. The A1 trigger is therefore false. The raw attempted R branch correctly rejected RETAIN but the experimental runner then failed to delegate native for that rejected decision; that branch is invalid and excluded. Per preregistration, an unreachable context is `ACTION_NOT_REACHABLE` and the effective R path is exactly C0. No replacement phase and no replay were used.','']
    lines += ['## Cost-path pattern','', 'Across all three exercised anchors, `R0Burden` increased while `grossIntegral` decreased. The intervention therefore changes the service/exposure timing path, but mostly redistributes cost rather than resolving it. Terminal economics were identical to C0 in 1824852 and 1825994; 1825962 was lower by about 0.01449 on both U and D.','']
    lines += ['## Frozen old-four Pool','', '| metric | R effective | cap | pass |','|---|---:|---:|---|']
    for k in POOL_NAMES:
        z=pool[k]; lines.append(f"| {k} | {z['value']:.12f} | {z['cap']:.12f} | {'PASS' if z['pass'] else 'FAIL'} |")
    lines += ['','The decisive Pool failure is `cashAtRiskPeak`: **18.600643064707 > 18.209772835543**. This is independent of the individual Tail failures.','']
    lines += ['## Disposition','', '- Stage 0 architecture result remains valid: first-class one-shot retain/no-emission is well formed.', '- Stage 1 demonstrates a real causal first stage and non-collapse in 3/4 anchors.', '- The minimal one-receipt action does not achieve frozen budget feasibility.', '- **Do not run Stage 2 / 10 markets.**', '- Do not add a second retain, duration rule, timer, cancel combination, A2/A3, or selector automatically.', '- `VALUE_ORDER_REMAINS_NON_TOTAL`; `B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE`.', '- `NO_ALPHA_PROMOTION`; `REAL_NET_COST_UNRESOLVED`; belief training remains unauthorized.', '', '**REPORT COMPLETE — STOP.**']
    report=P0/'B3_RETAIN_PENDING_ONCE_STAGE1_FIXED4_REPORT_V1_20260908.md'
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'reach':reach,'exercise':exercise,'individualTailPass':indiv,'poolPass':poolpass,'pool':pool,'compact':str(compact.relative_to(ROOT)),'report':str(report.relative_to(ROOT))},ensure_ascii=False))

if __name__=='__main__': main()
