"""B3 PLVAC post-release budget locus falsification — primary witness 1824852 only.

Behavior-inert instrumentation replay of the already-valid N/P one-shot policies.
No new treatment.  It extracts:
  rho   = confirmed selected-P slot release frontier;
  tau   = first post-rho same-H0 native legal margin frontier;
  sigma = first certified prefix crossing for the three preregistered failed metrics.

The PLVAC V2 Tracker cadence is inherited exactly: no additional Tracker.sample_state()
calls are introduced for tracing.  Read-only snapshots only inspect already-maintained
accumulators and current native state.
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
from tools import run_native_selection_margin_prevalence_stagea16_v1 as margin

MID=1824852
EPS=1e-9; TOL=1e-8; CASH_TOL=1e-7
PRIMARY=('R0LotBurdenTail','grossIntegral','grossPeak')

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x): return old.stable(x)
def exact(a,b,tol=1e-12):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)): return abs(float(a)-float(b))<=tol
    return stable(a)==stable(b)

def make_manifest_row(rawrow,seam):
    return {'rawBundleIdentity':copy.deepcopy(rawrow['rawBundleIdentity']),
            'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],
            'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in rawrow['R0']]}

def prefix_values(tr):
    # Read-only view of official accumulators; does NOT sample or advance the tracker.
    q=max(EPS,float(tr.q)); c=max(EPS,float(tr.c)); H=max(EPS,float(tr.H))
    lot=[]
    for rid,q0 in tr.r0init.items():
        den=max(EPS,float(q0)*H); lot.append(float(tr.r0Burden.get(rid,0.0))/den)
    return {
      'R0LotBurdenTail': max(lot) if lot else 0.0,
      'grossIntegral': float(tr.grossIntegral)/(q*H),
      'grossPeak': float(tr.grossPeak)/q,
    }

def non_equiv(ai,pi):
    if not ai or not pi:return False
    return not (str(ai.get('side'))==str(pi.get('side')) and str(ai.get('role'))==str(pi.get('role')) and
                float(ai.get('price')).hex()==float(pi.get('price')).hex() and float(ai.get('qty')).hex()==float(pi.get('qty')).hex())

def same_h0_authority(sim,h0):
    ql=getattr(sim,'q_ladder',None); pa=getattr(sim,'q_pending_active',None)
    if not isinstance(ql,dict) or not isinstance(pa,dict): return False
    oid=h0.get('originResponsibilityId') or h0.get('responsibilityId')
    if oid is None:return False
    try:
        if int(ql.get('originResponsibilityId'))!=int(oid) or int(pa.get('originResponsibilityId'))!=int(oid): return False
    except Exception:return False
    for k in ('targetExpandSide','side','role'):
        hv=h0.get(k)
        if hv is not None and str(ql.get(k))!=str(hv): return False
    return str(ql.get('route'))=='PENDING_ACTIVE'

def legal_margin(sim,ordinal,t,qv,end,h0):
    before=old.b2.behavior_state_digest(sim)
    a=margin.audit_current_state(sim,int(ordinal),int(t),qv,int(end))
    after=old.b2.behavior_state_digest(sim)
    ref=a['reference']; A=(ref.get('candidates') or {}).get('BOUNDED_ACTIVE') or {}; P=(ref.get('candidates') or {}).get('PASSIVE_PRIMARY') or {}
    ai=copy.deepcopy(A.get('identity')); pi=copy.deepcopy(P.get('identity'))
    checks={
      'observerReadOnly': before==after and bool(a.get('observerMutationInert')),
      'referenceClean': bool(a.get('errorsClean')),
      'sameH0Authority': same_h0_authority(sim,h0),
      'nativePriorityA': ref.get('priority')=='BOUNDED_ACTIVE',
      'ACompleteLegal': ai is not None and A.get('L')=='TRUE' and A.get('A')=='TRUE' and A.get('currentExecutable') is not False,
      'PCompleteLegal': pi is not None and P.get('L')=='TRUE' and P.get('A')=='TRUE' and P.get('currentExecutable') is not False,
      'nonEquivalent': non_equiv(ai,pi),
    }
    return all(checks.values()), {'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'stateHash':a.get('stateHash'),'checks':checks,
                                  'priority':ref.get('priority'),'AIdentity':ai,'PIdentity':pi,
                                  'qLadder':copy.deepcopy(getattr(sim,'q_ladder',None)),'pendingActive':copy.deepcopy(getattr(sim,'q_pending_active',None))}

def selected_release_state(sim,key):
    o=(sim.orders.get(key) or {}) if key else {}
    in_slots=key in {str(k) for k in sim.slot_key.values()} if key else False
    status=str(o.get('status') or '')
    return {'key':key,'status':status,'cancelRequested':bool(o.get('cancelRequested')),'cum':float(o.get('cum') or 0.0),
            'qty':float(o.get('qty') or 0.0),'inSlotReservation':bool(in_slots),'terminalByLivePredicate':not old.base.v2.base.live(status)}

def trace_branch(tape,spec,seam,manifest_row,source_branch,label,tails):
    underlying='N' if label=='N_TRACE' else 'P'
    sim=old.B3Fork(tape,spec,underlying,seam,manifest_row); tr=None; rho=None; tau=None; sigmas={k:None for k in PRIMARY}; frontiers=[]
    h0=copy.deepcopy(seam.get('pendingHandoff') or seam.get('originalHandoff') or source_branch.get('oneShotReceipt',{}).get('preQLadder'))
    try:
        updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(sim.meta['firstReceivedMs']); old.base.v2.base.ex.advance_to(sim.bt,first)
        end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        selected_was_reserved=False
        def snap(frontier,ordinal,t):
            if tr is None:return
            vals=prefix_values(tr)
            rec={'frontier':frontier,'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'prefix':vals}
            frontiers.append(rec)
            for k in PRIMARY:
                if sigmas[k] is None and float(vals[k])>float(tails[k])+CASH_TOL:
                    sigmas[k]={'metric':k,'frontier':frontier,'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'value':float(vals[k]),'tail':float(tails[k])}
        for ordinal,u in enumerate(updates):
            t=int(u[1])
            if tr is not None:
                tr.advance(t,sim); snap('TRACKER_ADVANCE_PRE_PROCESS',ordinal,t)
            sim.set_phase(ordinal,t); old.base.v2.base.ex.advance_to(sim.bt,t)
            sim.process(t); sim.cancel_expired(t)
            before_refresh=selected_release_state(sim,sim.selectedKey)
            sim._refresh_slots(t)
            after_refresh=selected_release_state(sim,sim.selectedKey)
            if sim.selectedKey and after_refresh['inSlotReservation']: selected_was_reserved=True
            if underlying=='P' and rho is None and sim.selectedKey and selected_was_reserved and before_refresh['inSlotReservation'] and not after_refresh['inSlotReservation'] and after_refresh['terminalByLivePredicate']:
                rho={'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'frontier':'AFTER_REFRESH_BEFORE_BOOK','beforeRefresh':before_refresh,'afterRefresh':after_refresh,
                     'prefix':None if tr is None else prefix_values(tr)}
            old.base.v2.base.apply(sim.book,u); qv=old.base.v2.base.quotes(sim.book)
            if qv: sim._risk_contract_if_needed(t); sim._reanchor_stale(t)
            if underlying=='P' and rho is not None and tau is None and qv is not None:
                ok,detail=legal_margin(sim,ordinal,t,qv,end,h0)
                if not detail['checks']['observerReadOnly'] or not detail['checks']['referenceClean']:
                    raise RuntimeError('MENU_OBSERVER_NOT_INERT')
                if ok:
                    detail['frontier']='POST_RELEASE_PRE_NATIVE_ACTION'; detail['prefix']=None if tr is None else prefix_values(tr); tau=detail
            if ordinal==int(seam['phaseOrdinal']):
                if t!=int(seam['eventTimestampMs']) or qv is None: raise RuntimeError('FROZEN_SEAM_LOCATOR_MISMATCH')
                tr=bl.Tracker(sim,t,end,seam['AIdentity'],seam['PIdentity'])
                sim.dispatch_target(ordinal,t,qv,end)
                tr.sample_state(sim); snap('POST_SEAM_DISPATCH_SAMPLE',ordinal,t)
                if sim.selectedKey and selected_release_state(sim,sim.selectedKey)['inSlotReservation']: selected_was_reserved=True
            elif qv:
                sim._open_one_option(t,qv,end)
            if tr is not None:
                tr.sample_state(sim); snap('POST_NATIVE_ACTION_SAMPLE',ordinal,t)
            sim._sample_occupancy(); sim._postfork_peak()
        # Same finalize ordering as formal PLVAC treatment runner.
        fin=old.clock.finalize(sim,spec,None); sim._postfork_peak()
        if tr is None or sim.forkSnapshot is None or sim.oneShotCount!=1: raise RuntimeError('TRACE_NOT_EXERCISED')
        tele=tr.finish(sim); snap('TRACKER_FINISH',len(updates),end)
        term=bl.payoff(sim); fork=sim.forkSnapshot
        suffix=sim.fill_accounting[fork['preFillAccountingLen']:]
        direct=[x for x in suffix if str(x.get('key'))==str(sim.selectedKey)]
        dq=sum(float(x.get('confirmedQty') or 0.0) for x in direct)
        activity={'suffixConfirmedFills':tele['activity']['suffixConfirmedFills'],'circulationLinks':len(tele['activity']['circulationLinks']),
                  'T':tele['activity']['T'],'B':tele['activity']['B'],'R':tele['activity']['R']}
        final_numeric={
          'R0TerminalResidual':tele['R0']['terminalResidualNorm'],'R0Burden':tele['R0']['burdenNorm'],
          'R0LotResidualTail':max(float(v) for v in tele['R0']['lotResidualNorm'].values()),
          'R0LotBurdenTail':max(float(v) for v in tele['R0']['lotBurdenNorm'].values()),
          'cashAtRiskPeak':tele['risk']['cashAtRiskPeakNorm'],'reservedQuotePeak':tele['risk']['reservedUnreturnedQuoteNotionalPeakNorm'],
          'reservedQuoteTerminal':tele['risk']['reservedUnreturnedQuoteNotionalTerminalNorm'],'grossIntegral':tele['risk']['grossIntegralNorm'],
          'absNetIntegral':tele['risk']['absNetIntegralNorm'],'burn':tele['risk']['nonReplenishingBurnNorm'],'grossPeak':tele['risk']['grossPeakNorm'],
          'absNetPeak':tele['risk']['absNetPeakNorm']}
        for s in ('UP','DOWN'):
            final_numeric[f'newPeak_{s}']=tele['newService']['peakNormByServiceSide'][s]
            final_numeric[f'newTerminal_{s}']=tele['newService']['terminalNormByServiceSide'][s]
            final_numeric[f'newBurden_{s}']=tele['newService']['burdenNormByServiceSide'][s]
        parity={
          'behavior':fin['behaviorLedgerDigest']==source_branch['behaviorLedgerDigest'],
          'terminal':stable(term)==stable(source_branch['terminalEconomics']),
          'activity':stable(activity)==stable(source_branch['activityRaw']),
          'numeric':stable(final_numeric)==stable(source_branch['numeric']),
          'selectedConfirmedQty':abs(dq-float(source_branch['selectedExecution']['confirmedQty']))<=TOL,
          'oneShotReceipt':stable(sim.oneShotReceipt)==stable(source_branch['oneShotReceipt']),
          'accounting':all(bool(v) for v in fin['accountingChecks'].values()),
        }
        return {'arm':label,'underlying':underlying,'rho':rho,'tau':tau,'sigmas':sigmas,'frontiers':frontiers,
                'finalNumeric':final_numeric,'terminalEconomics':term,'activity':activity,'selectedKey':sim.selectedKey,'selectedConfirmedQty':dq,
                'selectedTerminalState':selected_release_state(sim,sim.selectedKey),'parity':parity,'parityPass':all(parity.values())}
    finally: sim.close()

def classify(P,tails):
    if not P['parityPass']: return 'CORRECTNESS_OR_PROVENANCE_STOP',{}
    rho=P.get('rho'); tau=P.get('tau'); sig=P.get('sigmas') or {}
    if rho is None:return 'LOCUS_TEST_NOT_IDENTIFIED',{'reason':'RHO_NOT_IDENTIFIED'}
    # Pre-release commitment: sigma at or before rho event clock.
    pre=[k for k,v in sig.items() if v is not None and int(v['eventTimestampMs'])<=int(rho['eventTimestampMs'])]
    if pre:return 'POST_RELEASE_RESCUE_FALSIFIED_PRE_RELEASE_COMMITMENT',{'metrics':pre}
    if tau is None:return 'POST_RELEASE_RESCUE_FALSIFIED_NO_NATIVE_HANDOFF_LEVER',{}
    before=[]
    for k,v in sig.items():
        if v is not None and int(v['eventTimestampMs'])<=int(tau['eventTimestampMs']): before.append(k)
    if before:return 'POST_RELEASE_RESCUE_FALSIFIED_BEFORE_FIRST_LEGAL_LEVER',{'metrics':before}
    return 'POST_RELEASE_LOCUS_NOT_FALSIFIED',{'tauPrefix':tau.get('prefix'),'tails':{k:tails[k] for k in PRIMARY}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--cohort',required=True); ap.add_argument('--baseline-compact',required=True); ap.add_argument('--raw-manifest',required=True); ap.add_argument('--source-result',required=True); ap.add_argument('--prereg',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    prereg=json.loads(Path(a.prereg).read_text(encoding='utf-8')); source=json.loads(Path(a.source_result).read_text(encoding='utf-8')); compact=json.loads(Path(a.baseline_compact).read_text(encoding='utf-8')); raw=json.loads(Path(a.raw_manifest).read_text(encoding='utf-8')); co=json.loads(Path(a.cohort).read_text(encoding='utf-8'))
    expected=prereg['frozenSource']['sourceHashes'][str(MID)]
    if sha(a.source_result)!=expected: raise RuntimeError('SOURCE_RESULT_HASH_MISMATCH')
    if sha(a.baseline_compact)!=prereg['frozenSource']['baselineCompactSha256']: raise RuntimeError('BASELINE_COMPACT_HASH_MISMATCH')
    if int(source.get('marketId') or -1)!=MID or not source.get('allCorrectnessPass') or not source.get('exercisePass') or not all(source.get('parity',{}).values()): raise RuntimeError('SOURCE_RESULT_NOT_VALID')
    if source.get('verdict')!='BUDGET_EXCEEDED_FOR_THIS_CONTRACT': raise RuntimeError('SOURCE_PRIMARY_NOT_BUDGET_FAIL')
    srcpf=(source.get('preflightHashFreeze') or {}).get('sha256') or {}
    if srcpf.get('oneShotRunner') and sha(Path(old.__file__))!=srcpf.get('oneShotRunner'): raise RuntimeError('ONESHOT_RUNNER_HASH_MISMATCH')
    if srcpf.get('baselineRunnerV2') and sha(Path(bl.__file__))!=srcpf.get('baselineRunnerV2'): raise RuntimeError('BASELINE_TRACKER_HASH_MISMATCH')
    failed=[k for k,v in source['assessor']['tailChecks'].items() if not v]
    if failed!=list(PRIMARY): raise RuntimeError(f'PRIMARY_FAILED_METRIC_MISMATCH:{failed}')
    if int(raw.get('row',{}).get('marketId') or -1)!=MID or not raw.get('row',{}).get('pass'): raise RuntimeError('RAW_MANIFEST_NOT_CLEAN')
    spec=next(x for x in co['states'] if int(x['marketId'])==MID); seam=copy.deepcopy(source['frozenSeam']); manifest=make_manifest_row(raw['row'],seam)
    tails=compact['newTail']
    pre={'version':'B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_PRIMARY_HASH_FREEZE_V1','createdBeforeTraceReplay':True,'marketId':MID,
         'sha256':{'runner':sha(Path(__file__)),'prereg':sha(a.prereg),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'baselineCompact':sha(a.baseline_compact),'rawIdentityManifest':sha(a.raw_manifest),'sourceResult':sha(a.source_result),'oneShotRunner':sha(Path(old.__file__)),'baselineTrackerV2':sha(Path(bl.__file__)),'menuObserver':sha(Path(margin.__file__))}}
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); (outdir/'preflight_hash_freeze.json').write_text(json.dumps(pre,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='b3_locus_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z: z.extract(f'tapes/{MID}.json.xz',root)
        tape=root/'tapes'/f'{MID}.json.xz'
        traces={}
        for label,srcarm in (('N_TRACE','N'),('P_TRACE','P')):
            traces[label]=trace_branch(tape,spec,seam,manifest,source['branches'][srcarm],label,tails)
            print(json.dumps({'arm':label,'parityPass':traces[label]['parityPass'],'rho':traces[label].get('rho'),'tau':None if traces[label].get('tau') is None else {'phaseOrdinal':traces[label]['tau']['phaseOrdinal'],'eventTimestampMs':traces[label]['tau']['eventTimestampMs']},'sigmas':traces[label].get('sigmas')},ensure_ascii=False),flush=True)
    verdict,detail=classify(traces['P_TRACE'],tails)
    correctness=all(x['parityPass'] for x in traces.values())
    if not correctness: verdict='CORRECTNESS_OR_PROVENANCE_STOP'
    out={'version':'B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_FALSIFICATION_AUDIT4_V1_PRIMARY1824852_V1_20260908','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'primary':True,
         'preflightHashFreeze':pre,'frozenFailedMetrics':list(PRIMARY),'tails':{k:tails[k] for k in PRIMARY},'traces':traces,'correctnessPass':correctness,'verdict':verdict,'verdictDetail':detail,
         'boundaries':['N_TRACE/P_TRACE are behavior-inert replays of existing policies','no new behavior treatment','no winner used in rho/tau/sigma/verdict','official PLVAC V2 tracker cadence inherited without extra sample_state','same frozen seam and full horizon','no fresh/reserve/8781/budget retuning']}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'correctnessPass':correctness,'verdict':verdict,'detail':detail},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
