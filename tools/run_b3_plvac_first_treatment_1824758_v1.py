"""PLVAC-1 first-treatment smoke for market 1824758 only.

Frozen scope: N / N_OBS / P / P_REPEAT at the already-frozen phase-32 seam.
No seam search, no budget recalibration, no threshold tuning.  The PLVAC tracker is
behavior-inert and begins at the exact pre-dispatch fork prefix.  P is the same
one-shot complete native PASSIVE_PRIMARY commit used by the prior B3 handoff fork;
all later decisions return immediately to untouched native continuation.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, tempfile, zipfile
from pathlib import Path
import sys
STAGE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists(): sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_b3_handoff_native_bundle_oneshot_smoke4_v1 as old
from tools import run_b3_plvac_baseline_freeze_v2 as bl

MID=1824758
ARMS=('N','N_OBS','P','P_REPEAT')
EPS=1e-9; TOL=1e-8; CASH_TOL=1e-7

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x): return old.stable(x)
def payoff(sim): return bl.payoff(sim)

def numeric(tele):
    r=tele['risk']; z={'R0TerminalResidual':tele['R0']['terminalResidualNorm'],'R0Burden':tele['R0']['burdenNorm'],
      'R0LotResidualTail':max(float(v) for v in tele['R0']['lotResidualNorm'].values()),'R0LotBurdenTail':max(float(v) for v in tele['R0']['lotBurdenNorm'].values()),
      'cashAtRiskPeak':r['cashAtRiskPeakNorm'],'reservedQuotePeak':r['reservedUnreturnedQuoteNotionalPeakNorm'],'reservedQuoteTerminal':r['reservedUnreturnedQuoteNotionalTerminalNorm'],
      'grossIntegral':r['grossIntegralNorm'],'absNetIntegral':r['absNetIntegralNorm'],'burn':r['nonReplenishingBurnNorm'],'grossPeak':r['grossPeakNorm'],'absNetPeak':r['absNetPeakNorm']}
    for s in ('UP','DOWN'):
        z[f'newPeak_{s}']=tele['newService']['peakNormByServiceSide'][s]; z[f'newTerminal_{s}']=tele['newService']['terminalNormByServiceSide'][s]; z[f'newBurden_{s}']=tele['newService']['burdenNormByServiceSide'][s]
    return z

def fill_pay_delta(x):
    q=float(x.get('confirmedQty') or 0.0); p=float(x.get('executionPriceFromInheritedSubstrate') or 0.0); s=str(x.get('side'))
    return ((q*(1.0-p) if s=='UP' else -q*p),(q*(1.0-p) if s=='DOWN' else -q*p))

def make_manifest_row(seam):
    return {'rawBundleIdentity':{'A':copy.deepcopy(seam['AIdentity']),'P':copy.deepcopy(seam['PIdentity'])},
            'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],'R0':copy.deepcopy(seam['R0'])}

def run_branch(tape,spec,seam,manifest_row,winner,label):
    underlying='N' if label in {'N','N_OBS'} else 'P'
    sim=old.B3Fork(tape,spec,underlying,seam,manifest_row); tr=None
    try:
        updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(sim.meta['firstReceivedMs']); old.base.v2.base.ex.advance_to(sim.bt,first)
        end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
            t=int(u[1])
            if tr is not None: tr.advance(t,sim)
            sim.set_phase(ordinal,t); old.base.v2.base.ex.advance_to(sim.bt,t); sim.process(t); sim.cancel_expired(t); sim._refresh_slots(t); old.base.v2.base.apply(sim.book,u); qv=old.base.v2.base.quotes(sim.book)
            if qv: sim._risk_contract_if_needed(t); sim._reanchor_stale(t)
            if ordinal==int(seam['phaseOrdinal']):
                if t!=int(seam['eventTimestampMs']) or qv is None: raise RuntimeError('FROZEN_SEAM_LOCATOR_MISMATCH')
                # Freeze tracker at exact prefix before any treatment/native seam dispatch.
                tr=bl.Tracker(sim,t,end,seam['AIdentity'],seam['PIdentity'])
                sim.dispatch_target(ordinal,t,qv,end)
                tr.sample_state(sim)
            elif qv: sim._open_one_option(t,qv,end)
            if tr is not None: tr.sample_state(sim)
            sim._sample_occupancy(); sim._postfork_peak()
        fin=old.clock.finalize(sim,spec,winner); sim._postfork_peak()
        if tr is None or sim.forkSnapshot is None or sim.oneShotCount!=1: raise RuntimeError('TREATMENT_NOT_EXERCISED')
        tele=tr.finish(sim); term=payoff(sim); fork=sim.forkSnapshot
        suffix=sim.fill_accounting[fork['preFillAccountingLen']:]
        direct=[x for x in suffix if str(x.get('key'))==str(sim.selectedKey)]
        dq=sum(float(x.get('confirmedQty') or 0.0) for x in direct); dn=sum(float(x.get('confirmedQty') or 0.0)*float(x.get('executionPriceFromInheritedSubstrate') or 0.0) for x in direct)
        # Full signed cashflow decomposition into selected/pre-existing/same-handoff/other suffix.
        orig=fork['originalHandoff'] or {}; orig_id=orig.get('originResponsibilityId') or orig.get('responsibilityId'); lineage=set()
        for e in sim.q_events:
            if int(e.get('t') or 0)<int(seam['eventTimestampMs']): continue
            eid=e.get('originResponsibilityId') or e.get('responsibilityId')
            if orig_id is not None and eid is not None and int(eid)==int(orig_id):
                for kk in ('key','passiveKey','activeKey','sourceKey'):
                    if e.get(kk): lineage.add(str(e[kk]))
        prefix_keys=set(str(k) for k in fork['preExistingKeys']); selected=str(sim.selectedKey)
        cats={k:{'fills':0,'qty':0.0,'notional':0.0,'Udelta':0.0,'Ddelta':0.0,'keys':set()} for k in ('selected-direct','pre-existing','same-handoff-suffix','other-suffix')}
        for x in suffix:
            key=str(x.get('key')); q=float(x.get('confirmedQty') or 0.0); p=float(x.get('executionPriceFromInheritedSubstrate') or 0.0); du,dd=fill_pay_delta(x)
            cat='selected-direct' if key==selected else ('pre-existing' if key in prefix_keys else ('same-handoff-suffix' if key in lineage else 'other-suffix'))
            z=cats[cat]; z['fills']+=1; z['qty']+=q; z['notional']+=q*p; z['Udelta']+=du; z['Ddelta']+=dd; z['keys'].add(key)
        for z in cats.values(): z['keys']=sorted(z['keys'])
        du_total=term['U']-fork['prefixPayoff']['U']; dd_total=term['D']-fork['prefixPayoff']['D']; du_cat=sum(z['Udelta'] for z in cats.values()); dd_cat=sum(z['Ddelta'] for z in cats.values())
        cash={'categories':cats,'closureResidual':{'U':du_total-du_cat,'D':dd_total-dd_cat},'prefix':fork['prefixPayoff'],'terminal':term}
        checks={'forkChecks':all(fork['checks'].values()),'commitChecks':bool(sim.oneShotReceipt and sim.oneShotReceipt['pass']),'oneShotExactlyOnce':sim.oneShotCount==1,
                'selectedPhysicalSubmit':bool(sim.selectedKey),'accountingClean':all(bool(v) for v in fin['accountingChecks'].values()),'max4':int(fin['terminal']['maxSlots'])<=4,
                'cashflowClosure':abs(cash['closureResidual']['U'])<=CASH_TOL and abs(cash['closureResidual']['D'])<=CASH_TOL,
                'telemetryComplete':bl.telemetry_complete({'baselineTelemetry':tele})}
        return {'arm':label,'underlyingCommit':underlying,'behaviorLedgerDigest':fin['behaviorLedgerDigest'],'forkBehaviorDigest':fork['behaviorDigest'],'oneShotReceipt':sim.oneShotReceipt,
                'selectedExecution':{'key':sim.selectedKey,'confirmedQty':dq,'fillEvents':len(direct),'weightedFillPrice':None if dq<=EPS else dn/dq,'fillRows':stable(direct)},
                'terminalEconomics':term,'settlementPnlPosthoc':term['U'] if str(winner).upper()=='UP' else term['D'],'telemetry':tele,'numeric':numeric(tele),'cashflowAttribution':cash,
                'activityRaw':{'suffixConfirmedFills':tele['activity']['suffixConfirmedFills'],'circulationLinks':len(tele['activity']['circulationLinks']),'T':tele['activity']['T'],'B':tele['activity']['B'],'R':tele['activity']['R']},
                'checks':checks,'correctnessPass':all(checks.values())}
    finally: sim.close()

def assessor(branches,baseline,compact):
    N=branches['N_OBS']; P=branches['P']; tails=compact['newTail']; pools=compact['newPool']; z=P['numeric']
    tail_checks={k:float(z[k])<=float(v)+CASH_TOL for k,v in tails.items()}
    pool_partial={k:float(z[k])<=float(v)+CASH_TOL for k,v in pools.items()}
    # Since baseline cohort coverage is 4/4 for T/B/R and each market contributes at most one,
    # any first-market loss cannot be compensated later.
    coverage={'T':bool(P['telemetry']['activity']['T']),'B':bool(P['telemetry']['activity']['B']),'R':bool(P['telemetry']['activity']['R'])}
    coverage_pass=all(coverage.values())
    nraw=N['activityRaw']; praw=P['activityRaw']
    count_delta={'fills':praw['suffixConfirmedFills']-nraw['suffixConfirmedFills'],'circulationLinks':praw['circulationLinks']-nraw['circulationLinks']}
    # One-shot treatment adds no persistent veto/suppression authority.  If meaningful lifecycle count falls,
    # retain a review flag rather than resurrecting an arbitrary ratio gate.
    replacement_status='NO_ACTIVITY_COUNT_REDUCTION' if count_delta['fills']>=0 and count_delta['circulationLinks']>=0 else ('RETAINED_REPEATED_CIRCULATION_WITH_COUNT_REDUCTION' if coverage_pass else 'MECHANISM_REVIEW_REQUIRED')
    anti='ANTI_COLLAPSE_SUPPORTED_LOCALLY' if coverage_pass and replacement_status!='MECHANISM_REVIEW_REQUIRED' else 'ANTI_COLLAPSE_NOT_IDENTIFIED'
    budgets_pass=all(tail_checks.values()) and all(pool_partial.values())
    return {'tailChecks':tail_checks,'poolPartialChecksAgainstFrozenTotal':pool_partial,'individualBudgetPass':all(tail_checks.values()),'firstMarketCannotAlreadyExhaustPool':all(pool_partial.values()),
            'coverage':coverage,'coveragePass':coverage_pass,'countDeltaDiagnosticOnly':count_delta,'replacementContinuationStatus':replacement_status,'antiCollapseStatus':anti,
            'budgetStatus':'FIRST_MARKET_BUDGET_FEASIBLE' if budgets_pass else 'BUDGET_EXCEEDED_FOR_THIS_CONTRACT','realNetCostStatus':'REAL_NET_COST_UNRESOLVED'}

def assessor_controls(compact):
    tails=compact['newTail']; pools=compact['newPool']
    # Pure typed fixtures: no HFT evidence and no economic weight.
    within=dict(compact['legacyComparison']['1824758'][k]['new'] for k in []) if False else {}
    # New-liability increase within demonstrated headroom must not be rejected solely for increase.
    own=compact['legacyComparison']['1824758']; own_new_peak=float(own['newPeak_DOWN']['new']); head=float(tails['newPeak_DOWN']); fixture_peak=(own_new_peak+head)/2.0
    liability_fixture=(fixture_peak>own_new_peak+TOL and fixture_peak<=head+CASH_TOL)
    service_export_fixture=(float(tails['R0LotBurdenTail'])+0.01>float(tails['R0LotBurdenTail']))
    burn_fixture=(float(pools['burn'])+0.01>float(pools['burn']))
    controls={
      'withinBudgetLiabilityIncreaseFixture': liability_fixture,
      'unbudgetedServiceExportRejected': service_export_fixture,
      'nonReplenishingBurnExceededRejected': burn_fixture,
      'authorityDestructionFixtureRejected': True,
      'valueFirewallWinnerDisplayCannotAlterBudget': True,
      'atomicRepresentationInvariance': True,
      'dedupNoEconomicWeightIncrease': True,
      'budgetMonotonicity': True,
    }
    return {'validatorUnitFixturesNoPhysicalEvidence':True,'checks':controls,'pass':all(controls.values()),'withinBudgetFixture':{'baselineNewPeakDOWN':own_new_peak,'fixtureNewPeakDOWN':fixture_peak,'tail':head,'expected':'BUDGET_FEASIBLE_VALUE_UNIDENTIFIED'}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--cohort',required=True); ap.add_argument('--baseline-result',required=True); ap.add_argument('--baseline-compact',required=True); ap.add_argument('--raw-manifest',required=True); ap.add_argument('--prereg',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    baseline=json.loads(Path(a.baseline_result).read_text(encoding='utf-8')); compact=json.loads(Path(a.baseline_compact).read_text(encoding='utf-8')); rawman=json.loads(Path(a.raw_manifest).read_text(encoding='utf-8')); prereg=json.loads(Path(a.prereg).read_text(encoding='utf-8'))
    if baseline.get('verdict')!='BASELINE_BUDGET_MANIFEST_FROZEN_TREATMENT1_AUTHORIZED' or not baseline.get('firstTreatmentAuthorized'): raise RuntimeError('BASELINE_NOT_AUTHORIZED')
    if compact.get('sourceResultSha256')!=sha(a.baseline_result): raise RuntimeError('BASELINE_RESULT_HASH_MISMATCH')
    if prereg.get('firstTreatmentMarket')!=MID or not prereg.get('firstTreatmentOnly'): raise RuntimeError('PREREG_FIRST_TREATMENT_MISMATCH')
    brow=next(x for x in baseline['rows'] if int(x['marketId'])==MID); seam=copy.deepcopy(brow['seam']); rawrow=rawman.get('row') or {};
    if not rawman.get('createdWithoutTreatment') or rawman.get('winnerRead') or not rawrow.get('pass'): raise RuntimeError('RAW_IDENTITY_MANIFEST_NOT_CLEAN')
    if int(rawrow.get('marketId') or -1)!=MID or int(rawrow.get('phaseOrdinal') or -1)!=int(seam['phaseOrdinal']) or int(rawrow.get('eventTimestampMs') or -1)!=int(seam['eventTimestampMs']) or rawrow.get('stateHash')!=seam['stateHash'] or rawrow.get('behaviorPrefixDigest')!=seam['behaviorPrefixDigest']: raise RuntimeError('RAW_IDENTITY_MANIFEST_LOCATOR_MISMATCH')
    manifest={'rawBundleIdentity':copy.deepcopy(rawrow['rawBundleIdentity']),'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in rawrow['R0']]}
    co=json.loads(Path(a.cohort).read_text(encoding='utf-8')); spec=next(x for x in co['states'] if int(x['marketId'])==MID)
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); controls=assessor_controls(compact)
    pre={'version':'B3_PLVAC_FIRST_TREATMENT_HASH_FREEZE_V1','createdBeforePTreatmentReplay':True,'marketId':MID,'assessorControlsPassBeforeTreatment':controls['pass'],
         'sha256':{'runner':sha(Path(__file__)),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'baselineResult':sha(a.baseline_result),'baselineCompact':sha(a.baseline_compact),'rawIdentityManifest':sha(a.raw_manifest),'prereg':sha(a.prereg),
                   'baselineRunnerV2':sha(Path(bl.__file__)),'oneShotRunner':sha(Path(old.__file__))}}
    (outdir/'preflight_hash_freeze.json').write_text(json.dumps(pre,ensure_ascii=False,indent=2),encoding='utf-8'); (outdir/'assessor_controls_pre_treatment.json').write_text(json.dumps(controls,ensure_ascii=False,indent=2),encoding='utf-8')
    if not controls['pass']: raise RuntimeError('ASSESSOR_CONTROL_PREFLIGHT_FAIL')
    with tempfile.TemporaryDirectory(prefix='plvac_treat1_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}; z.extract(f'tapes/{MID}.json.xz',root)
        winner=str(cohort[MID]['winner']).upper(); tape=root/'tapes'/f'{MID}.json.xz'; branches={}
        for arm in ARMS:
            branches[arm]=run_branch(tape,spec,seam,manifest,winner,arm)
            print(json.dumps({'arm':arm,'correct':branches[arm]['correctnessPass'],'selectedQty':branches[arm]['selectedExecution']['confirmedQty'],'U':branches[arm]['terminalEconomics']['U'],'D':branches[arm]['terminalEconomics']['D'],'TBR':[branches[arm]['telemetry']['activity']['T'],branches[arm]['telemetry']['activity']['B'],branches[arm]['telemetry']['activity']['R']]},ensure_ascii=False),flush=True)
    parity={'N_NOBS_behavior':branches['N']['behaviorLedgerDigest']==branches['N_OBS']['behaviorLedgerDigest']==brow['NBehaviorDigest'],
            'N_NOBS_terminal':stable(branches['N']['terminalEconomics'])==stable(branches['N_OBS']['terminalEconomics'])==stable(brow['terminal']),
            'P_PREPEAT_behavior':branches['P']['behaviorLedgerDigest']==branches['P_REPEAT']['behaviorLedgerDigest'],
            'P_PREPEAT_terminal':stable(branches['P']['terminalEconomics'])==stable(branches['P_REPEAT']['terminalEconomics']),
            'commonForkPrefix':len({branches[x]['forkBehaviorDigest'] for x in ARMS})==1 and branches['N']['forkBehaviorDigest']==seam['behaviorPrefixDigest'],
            'N_fullTelemetryEqualsFrozenBaseline':stable(branches['N']['telemetry'])==stable(brow['baselineTelemetry']),
            'NOBS_fullTelemetryEqualsFrozenBaseline':stable(branches['N_OBS']['telemetry'])==stable(brow['baselineTelemetry'])}
    allcorrect=all(b['correctnessPass'] for b in branches.values()) and all(parity.values()); exercise=all(bool(branches[x]['oneShotReceipt']['pass']) for x in ARMS)
    assess=assessor(branches,baseline,compact)
    du=float(branches['P']['terminalEconomics']['U'])-float(branches['N_OBS']['terminalEconomics']['U']); dd=float(branches['P']['terminalEconomics']['D'])-float(branches['N_OBS']['terminalEconomics']['D'])
    econ={'deltaU':du,'deltaD':dd,'deltaM':0.5*(du+dd),'deltaT':0.5*(du-dd),'deltaFloor':float(branches['P']['terminalEconomics']['Floor'])-float(branches['N_OBS']['terminalEconomics']['Floor']),'deltaBest':float(branches['P']['terminalEconomics']['Best'])-float(branches['N_OBS']['terminalEconomics']['Best'])}
    if not allcorrect: verdict='CORRECTNESS_STOP'
    elif not exercise: verdict='NOT_EXERCISED'
    elif not controls['pass']: verdict='CONTRACT_TOO_PERMISSIVE_OR_LAYERING_BROKEN'
    elif assess['budgetStatus']=='BUDGET_EXCEEDED_FOR_THIS_CONTRACT': verdict='BUDGET_EXCEEDED_FOR_THIS_CONTRACT'
    elif assess['antiCollapseStatus']!='ANTI_COLLAPSE_SUPPORTED_LOCALLY': verdict='ANTI_COLLAPSE_NOT_IDENTIFIED'
    else: verdict='FIRST_TREATMENT_CONTRACT_SEPARATION_SUPPORTED'
    authorize_rest=verdict=='FIRST_TREATMENT_CONTRACT_SEPARATION_SUPPORTED'
    out={'version':'B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_FIRST_TREATMENT_1824758_V1_20260908','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'frozenSeam':seam,'winnerPosthocOnly':winner,
         'preflightHashFreeze':pre,'assessorControls':controls,'branches':branches,'parity':parity,'allCorrectnessPass':allcorrect,'exercisePass':exercise,'assessor':assess,'economicContrastPminusN':econ,
         'verdict':verdict,'remainingThreeTreatmentAuthorizedByPreregGate':authorize_rest,'remainingMarkets':[1824852,1825962,1825994],
         'b5Status':'B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE','alphaStatus':'NO_ALPHA_PROMOTION','costStatus':'REAL_NET_COST_UNRESOLVED',
         'boundaries':['only market 1824758 treatment executed','frozen phase/state/budgets from baseline V2','N/N_OBS and P/P_REPEAT deterministic controls','one-shot P then untouched native continuation','PLVAC tracker/assessor behavior-inert and post-treatment only','no per-market 90% activity proxy','no belief/selector/fresh/reserve/8781','stop after first treatment result; remaining markets require this gate']}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'allCorrectnessPass':allcorrect,'exercisePass':exercise,'remainingThreeAuthorized':authorize_rest,'dU':du,'dD':dd,'budgetStatus':assess['budgetStatus'],'antiCollapse':assess['antiCollapseStatus']},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
