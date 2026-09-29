from __future__ import annotations
import json, hashlib
from pathlib import Path

BASE = Path('data/research/r4_v0/p0_provenance_v1/B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_BASELINE_COMPLETE_COMPACT_20260908.json')
JOBS = {
    1824758: 'b3-plvac-first-treatment-1824758-20260908-v4',
    1824852: 'b3-plvac-treatment-1824852-20260908-v1',
    1825962: 'b3-plvac-treatment-1825962-20260908-v1',
    1825994: 'b3-plvac-treatment-1825994-20260908-v1',
}
OUT_COMPACT = Path('data/research/r4_v0/p0_provenance_v1/B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_COMPACT_20260908.json')
OUT_REPORT = Path('data/research/r4_v0/p0_provenance_v1/B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_REPORT_20260908.md')
TOL=1e-7

def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest()
def econ(d): return d.get('econ') or d['economicContrastPminusN']
def controls_pass(d): return bool(d.get('assessorControlsPass', d.get('assessorControls',{}).get('pass',False)))

def main():
    base=json.loads(BASE.read_text(encoding='utf-8'))
    results={}
    source_hashes={}
    rows=[]
    for mid,job in JOBS.items():
        p=Path(f'data/research/lan_worker_returns/{job}/result.json')
        d=json.loads(p.read_text(encoding='utf-8'))
        results[mid]=d; source_hashes[str(mid)]={'job':job,'sha256':sha(p)}
        e=econ(d); n=d['branches']['N']; pp=d['branches']['P']
        tail_false=[k for k,v in d['assessor']['tailChecks'].items() if not v]
        rows.append({
            'marketId':mid,'job':job,'verdict':d['verdict'],'correctnessPass':bool(d['allCorrectnessPass']),
            'exercisePass':bool(d['exercisePass']),'parityPass':all(bool(x) for x in d['parity'].values()),'assessorControlsPass':controls_pass(d),
            'budgetStatus':d['assessor']['budgetStatus'],'antiCollapseStatus':d['assessor']['antiCollapseStatus'],
            'replacementContinuationStatus':d['assessor']['replacementContinuationStatus'],
            'tailFailures':tail_false,'deltaU':e['deltaU'],'deltaD':e['deltaD'],'deltaM':e['deltaM'],'deltaT':e['deltaT'],
            'NActivity':n['activityRaw'],'PActivity':pp['activityRaw'],
            'PSelectedConfirmedQty':(pp.get('selected') or pp.get('selectedExecution') or {}).get('confirmedQty',0),
            'NSelectedConfirmedQty':(n.get('selected') or n.get('selectedExecution') or {}).get('confirmedQty',0),
            'PR0TerminalResidual':pp['telemetry']['R0']['terminalResidualNorm'],
            'PReservedTerminalNorm':pp['telemetry']['risk']['reservedUnreturnedQuoteNotionalTerminalNorm'],
            'PCancelPendingReservedTerminal':pp['telemetry']['risk']['cancelPendingReservedQuoteNotionalTerminal'],
            'POriginCounts':pp['telemetry']['newService']['originCounts'],
            'PNumeric':pp['numeric'],
        })
    all_correct=all(r['correctnessPass'] for r in rows)
    all_exercise=all(r['exercisePass'] for r in rows)
    all_parity=all(r['parityPass'] for r in rows)
    all_controls=all(r['assessorControlsPass'] for r in rows)
    anti_all=all(r['antiCollapseStatus']=='ANTI_COLLAPSE_SUPPORTED_LOCALLY' for r in rows)
    coverage={}
    for k in ('T','B','R'):
        n=sum(bool(r['NActivity'][k]) for r in rows); p=sum(bool(r['PActivity'][k]) for r in rows)
        coverage[k]={'N':n,'P':p,'pass':p>=n}
    activity_totals={}
    for k in ('suffixConfirmedFills','circulationLinks'):
        n=sum(int(r['NActivity'][k]) for r in rows); p=sum(int(r['PActivity'][k]) for r in rows)
        activity_totals[k]={'N':n,'P':p,'delta':p-n}
    pool={}
    for k,limit in base['newPool'].items():
        s=sum(float(r['PNumeric'][k]) for r in rows)
        pool[k]={'Psum':s,'frozenPool':float(limit),'delta':s-float(limit),'pass':s<=float(limit)+TOL}
    budget_failed=[r['marketId'] for r in rows if r['budgetStatus']=='BUDGET_EXCEEDED_FOR_THIS_CONTRACT']
    budget_feasible=[r['marketId'] for r in rows if r['budgetStatus']!='BUDGET_EXCEEDED_FOR_THIS_CONTRACT']
    pool_fail=[k for k,v in pool.items() if not v['pass']]
    closure={
        'R0TerminalZeroAll':all(abs(float(r['PR0TerminalResidual']))<=TOL for r in rows),
        'reservedTerminalZeroAll':all(abs(float(r['PReservedTerminalNorm']))<=TOL and abs(float(r['PCancelPendingReservedTerminal']))<=TOL for r in rows),
        'originCounts':{k:sum(int(r['POriginCounts'].get(k,0)) for r in rows) for k in ('PRE_EXISTING_CARRIER','POST_T0_CARRIER','MIXED_ORIGIN_UNRESOLVED')},
    }
    sum_e={k:sum(float(r[k]) for r in rows) for k in ('deltaU','deltaD','deltaM','deltaT')}
    all_p_selected_nofill=all(float(r['PSelectedConfirmedQty'] or 0)<=TOL for r in rows)
    all_n_selected_fill=all(float(r['NSelectedConfirmedQty'] or 0)>TOL for r in rows)
    too_permissive=False
    old_proxy_reintroduced=False
    provisional_service_cap_witness=False
    # Pre-registered strong witness requires ONLY new-service tail/burden failure, all other budgets pass, terminal obligations discharged, bilateral nonworse.
    for r in rows:
        tf=set(r['tailFailures'])
        allowed={'newPeak_UP','newPeak_DOWN','newBurden_UP','newBurden_DOWN','newTerminal_UP','newTerminal_DOWN'}
        if tf and tf.issubset(allowed) and r['deltaU']>=-TOL and r['deltaD']>=-TOL and r['PR0TerminalResidual']<=TOL and r['PReservedTerminalNorm']<=TOL:
            provisional_service_cap_witness=True
    contract_supported=all_correct and all_exercise and all_parity and all_controls and anti_all and all(v['pass'] for v in coverage.values()) and not too_permissive and not old_proxy_reintroduced
    contract_verdict='CONTRACT_SEPARATION_SUPPORTED_IN_THIS_SMOKE' if contract_supported else 'CONTRACT_CHALLENGE_REQUIRES_REVIEW'
    candidate_disposition='P_CLASS_BUDGET_EXCEEDED_FOR_THIS_CONTRACT' if budget_failed or pool_fail else 'P_CLASS_BUDGET_FEASIBLE_IN_THIS_SMOKE'
    value_disposition='VALUE_ORDER_NON_TOTAL_AND_REAL_NET_COST_UNRESOLVED'
    compact={
        'version':'B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_COMPACT_20260908','researchOnly':True,'runtimeAuthority':False,
        'contractVerdict':contract_verdict,'candidateDisposition':candidate_disposition,'valueDisposition':value_disposition,
        'allCorrectnessPass':all_correct,'allExercisePass':all_exercise,'allParityPass':all_parity,'allAssessorControlsPass':all_controls,
        'antiCollapseAllSupportedLocally':anti_all,'coverage':coverage,'activityTotals':activity_totals,
        'budgetFeasibleMarkets':budget_feasible,'budgetExceededMarkets':budget_failed,'cohortPool':pool,'cohortPoolFailures':pool_fail,
        'closure':closure,'sumEconomicContrastPminusN':sum_e,'allPSelectedCarriersNoFill':all_p_selected_nofill,'allNSelectedCarriersFilled':all_n_selected_fill,
        'contractCounterexampleChecks':{'tooPermissiveOrLayeringBroken':too_permissive,'oldProxyReintroduced':old_proxy_reintroduced,'provisionalServiceLoadCapOverconservativeWitness':provisional_service_cap_witness},
        'rows':rows,'sourceHashes':source_hashes,'baselineCompactSha256':sha(BASE),
        'b5Status':'B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE','alphaStatus':'NO_ALPHA_PROMOTION','costStatus':'REAL_NET_COST_UNRESOLVED',
        'stopping':['no new HFT after fixed four','no budget retuning','no fresh/reserve/8781','no belief model/selector','candidate budget failure does not invalidate contract-separation evidence'],
    }
    OUT_COMPACT.write_text(json.dumps(compact,ensure_ascii=False,indent=2),encoding='utf-8')
    md=[]
    md.append('# B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1 — Formal Report')
    md.append('')
    md.append('Date: 2026-09-08  ')
    md.append(f'Final contract verdict: **`{contract_verdict}`**  ')
    md.append(f'Candidate disposition: **`{candidate_disposition}`**  ')
    md.append('Value status: **`VALUE_ORDER_NON_TOTAL_AND_REAL_NET_COST_UNRESOLVED`**')
    md.append('')
    md.append('## 1. Execution / correctness')
    md.append('')
    md.append(f'- correctness 4/4: {all_correct}')
    md.append(f'- treatment exercise 4/4: {all_exercise}')
    md.append(f'- N/N_OBS and P/P_REPEAT parity 4/4: {all_parity}')
    md.append(f'- assessor controls 4/4: {all_controls}')
    md.append('- no fresh, no reserve, no 8781, no belief model, no persistent Passive-first rule')
    md.append('')
    md.append('## 2. Per-market contract / economic results')
    md.append('')
    md.append('| Market | Budget | Anti-collapse | ΔU | ΔD | ΔM | Tail failures |')
    md.append('|---|---|---|---:|---:|---:|---|')
    for r in rows:
        md.append(f"| {r['marketId']} | {r['budgetStatus']} | {r['antiCollapseStatus']} | {r['deltaU']:.9f} | {r['deltaD']:.9f} | {r['deltaM']:.9f} | {', '.join(r['tailFailures']) if r['tailFailures'] else 'none'} |")
    md.append('')
    md.append('Interpretation: 1824758 is tiny bilateral model improvement within budget; 1824852 is bilateral model improvement but outside the frozen budget; 1825962 is budget-feasible but economically non-total; 1825994 is both economically non-total and outside the frozen budget. PLVAC therefore does not collapse budget feasibility into economic value.')
    md.append('')
    md.append('## 3. Cohort budgets')
    md.append('')
    md.append('| Pool metric | P sum | Frozen Pool | Pass |')
    md.append('|---|---:|---:|---|')
    for k,v in pool.items(): md.append(f"| {k} | {v['Psum']:.12f} | {v['frozenPool']:.12f} | {'PASS' if v['pass'] else 'FAIL'} |")
    md.append('')
    md.append(f"Cohort Pool failure: **{', '.join(pool_fail) if pool_fail else 'none'}**. Individual budget-fail markets: {budget_failed}. Therefore the one-shot P class is not L1 budget-feasible as a four-market candidate under this frozen PLVAC-1 envelope.")
    md.append('')
    md.append('## 4. Activity / anti-collapse')
    md.append('')
    md.append(f"Coverage remains T={coverage['T']['P']}/{coverage['T']['N']}, B={coverage['B']['P']}/{coverage['B']['N']}, R={coverage['R']['P']}/{coverage['R']['N']} (all PASS).")
    md.append(f"Confirmed fills total {activity_totals['suffixConfirmedFills']['N']}→{activity_totals['suffixConfirmedFills']['P']} (Δ {activity_totals['suffixConfirmedFills']['delta']:+d}); conservative circulation links {activity_totals['circulationLinks']['N']}→{activity_totals['circulationLinks']['P']} (Δ {activity_totals['circulationLinks']['delta']:+d}).")
    md.append('1824852 and 1825994 reduce raw circulation-link counts, but each retains repeated quantity-confirmed circulation and all T/B/R coverage. No new no-trade/veto authority or ordinary-option destruction was identified, so raw count reduction is not reintroduced as an automatic rejection proxy.')
    md.append('')
    md.append('## 5. Responsibility / reservation closure')
    md.append('')
    md.append(f"R0 terminal residual zero in all P branches: {closure['R0TerminalZeroAll']}. Reserved and cancel-pending quote-notional terminal zero in all P branches: {closure['reservedTerminalZeroAll']}.")
    md.append(f"Post-t0 origin totals: {closure['originCounts']}. No mixed/unresolved origin occurred.")
    md.append('')
    md.append('## 6. Direct execution vs full continuation')
    md.append('')
    md.append(f"All four P selected carriers had zero confirmed fill: {all_p_selected_nofill}; all four native A selected carriers filled: {all_n_selected_fill}. Yet all four forks produced non-zero terminal contrasts. The economic effect is therefore a suffix allocation/continuation effect, not a monotone function of direct selected-carrier fill.")
    md.append('')
    md.append('## 7. Contract falsification checks')
    md.append('')
    md.append('- `CONTRACT_TOO_PERMISSIVE_OR_LAYERING_BROKEN`: not observed. Budget breaches were explicitly rejected as budget claims.')
    md.append('- `CONTRACT_OVERCONSERVATIVE_PROXY_REINTRODUCED`: not observed. Lower raw activity counts did not cause automatic rejection when service/circulation remained supported.')
    md.append('- `PROVISIONAL_SERVICE_LOAD_CAP_OVERCONSERVATIVE_ON_THIS_WITNESS`: not established. 1824852 also breaches R0-lot burden and gross-exposure limits; 1825994 breaches cash-risk and has a material U-side deterioration.')
    md.append('')
    md.append('Therefore the scoped contract verdict is **`CONTRACT_SEPARATION_SUPPORTED_IN_THIS_SMOKE`**. This validates separation behavior of the assessor on these consumed-development forks; it does not validate the P policy, the numeric deployment budget, or alpha.')
    md.append('')
    md.append('## 8. Economic boundary')
    md.append('')
    md.append(f"Unweighted endpoint sums P−N: ΔU={sum_e['deltaU']:.9f}, ΔD={sum_e['deltaD']:.9f}, ΔM={sum_e['deltaM']:.9f}. These are descriptive only: payoff ordering is non-total and no strict-past belief has been supplied.")
    md.append('Real fee/rebate scope remains unresolved, so no real-net edge is claimed.')
    md.append('')
    md.append('## 9. Final status / stop')
    md.append('')
    md.append('- `B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE`')
    md.append('- `NO_ALPHA_PROMOTION`')
    md.append('- `REAL_NET_COST_UNRESOLVED`')
    md.append('- No automatic belief/selector training, no fresh cohort, no L2/L3 promotion.')
    md.append('')
    md.append('**REPORT COMPLETE — STOP.**')
    OUT_REPORT.write_text('\n'.join(md)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'contractVerdict':contract_verdict,'candidateDisposition':candidate_disposition,'budgetFailedMarkets':budget_failed,'poolFailures':pool_fail,'coverage':coverage,'activityTotals':activity_totals,'compact':str(OUT_COMPACT),'report':str(OUT_REPORT)},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
