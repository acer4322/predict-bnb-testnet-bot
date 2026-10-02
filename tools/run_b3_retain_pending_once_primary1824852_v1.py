from __future__ import annotations
import argparse, copy, hashlib, json, math, os, tempfile, zipfile, sys
from pathlib import Path
STAGE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists(): sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_b3_handoff_native_bundle_oneshot_smoke4_v1 as old
from tools import run_b3_plvac_baseline_freeze_v2 as bl
import importlib.util
_STAGED_A1=Path.cwd()/'.lan_worker_v1'/'staging'/'b3_retain_pending_once_action_set_v1.py'
if _STAGED_A1.exists():
    _sp=importlib.util.spec_from_file_location('b3_retain_pending_once_action_set_v1_staged',_STAGED_A1);a1=importlib.util.module_from_spec(_sp);sys.modules[_sp.name]=a1;_sp.loader.exec_module(a1)
else:
    from tools import b3_retain_pending_once_action_set_v1 as a1

MID=1824852; EPS=1e-9; TOL=1e-8; CASH_TOL=1e-7
K=('R0Burden','R0LotBurdenTail','grossIntegral','grossPeak','cashAtRiskPeak','burn')

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x): return old.stable(x)
def close(a,b,tol=TOL): return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)
def payoff(sim): return bl.payoff(sim)

def numeric(tele):
    r=tele['risk']; z={'R0TerminalResidual':tele['R0']['terminalResidualNorm'],'R0Burden':tele['R0']['burdenNorm'],
      'R0LotResidualTail':max(float(v) for v in tele['R0']['lotResidualNorm'].values()),'R0LotBurdenTail':max(float(v) for v in tele['R0']['lotBurdenNorm'].values()),
      'cashAtRiskPeak':r['cashAtRiskPeakNorm'],'reservedQuotePeak':r['reservedUnreturnedQuoteNotionalPeakNorm'],'reservedQuoteTerminal':r['reservedUnreturnedQuoteNotionalTerminalNorm'],
      'grossIntegral':r['grossIntegralNorm'],'absNetIntegral':r['absNetIntegralNorm'],'burn':r['nonReplenishingBurnNorm'],'grossPeak':r['grossPeakNorm'],'absNetPeak':r['absNetPeakNorm']}
    for s in ('UP','DOWN'):
        z[f'newPeak_{s}']=tele['newService']['peakNormByServiceSide'][s]; z[f'newTerminal_{s}']=tele['newService']['terminalNormByServiceSide'][s]; z[f'newBurden_{s}']=tele['newService']['burdenNormByServiceSide'][s]
    return z

def p0_snap(sim,key):
    if key is None:return None
    o=sim.orders.get(str(key)) or {}
    try:s=sim.snap(o) if o else {}
    except Exception:s={}
    st=str(s.get('status') or o.get('status') or '').upper();cum=float(s.get('cumExecQty') if s.get('cumExecQty') is not None else o.get('cum') or 0.0);qty=float(o.get('qty') or 0.0);rem=max(0.0,qty-cum)
    sid=next((int(sid) for sid,k in sim.slot_key.items() if str(k)==str(key)),None)
    return {'key':str(key),'status':st,'cum':cum,'qty':qty,'remaining':rem,'cancelRequested':bool(o.get('cancelRequested')),'inSlotReservation':sid is not None,'slotId':sid,'price':float(o.get('price') or 0.0)}

def physical_snapshot(sim,p0_key):
    rows=[]
    for sid,key in sorted(sim.slot_key.items()):
        o=sim.orders.get(key) or {}; ps=p0_snap(sim,key)
        rows.append({'slotId':int(sid),'key':str(key),'side':str(o.get('side')),'role':str(sim.key_role.get(key,'UNASSIGNED')),'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),'status':ps['status'],'cum':ps['cum'],'remaining':ps['remaining'],'cancelRequested':ps['cancelRequested']})
    return {'submits':int(sim.submits),'slots':rows,'p0':p0_snap(sim,p0_key),'qLadder':stable(copy.deepcopy(sim.q_ladder)),'qPendingActive':stable(copy.deepcopy(sim.q_pending_active))}

def active_event(events,start,rid):
    for e in events[start:]:
        et=str(e.get('event') or e.get('type') or '')
        rr=e.get('originResponsibilityId') if e.get('originResponsibilityId') is not None else e.get('responsibilityId')
        if 'ACTIVE_SUBMIT' in et and rr is not None and int(rr)==int(rid): return copy.deepcopy(e)
    return None

def exact_tstar_match(detected, frozen):
    return detected is not None and int(detected['phaseOrdinal'])==int(frozen['tStar']['phaseOrdinal']) and int(detected['eventTimestampMs'])==int(frozen['tStar']['eventTimestampMs']) and str(detected['preBehaviorDigest'])==str(frozen['tStar']['preBehaviorDigest'])

def run_branch(tape,spec,seam,manifest,mode,frozen_tstar=None,detected_tstar=None):
    arm='N' if mode.startswith('N') else 'P'
    sim=old.B3Fork(tape,spec,arm,seam,manifest); tr=None;p0=None;h0_ref=None;action_state=None;detected=None;retain=None;reentry=None;tstar_pre=None;tstar_post=None
    try:
        updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);old.base.v2.base.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
            t=int(u[1]);
            if tr is not None: tr.advance(t,sim)
            sim.set_phase(ordinal,t);old.base.v2.base.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);sim._refresh_slots(t);old.base.v2.base.apply(sim.book,u);qv=old.base.v2.base.quotes(sim.book)
            if qv: sim._risk_contract_if_needed(t);sim._reanchor_stale(t)
            if ordinal==int(seam['phaseOrdinal']):
                tr=bl.Tracker(sim,t,end,seam['AIdentity'],seam['PIdentity']);sim.dispatch_target(ordinal,t,qv,end);tr.sample_state(sim)
                if arm=='P':
                    p0=str(sim.selectedKey);L=copy.deepcopy(sim.q_ladder);P=copy.deepcopy(sim.q_pending_active)
                    if L is None or P is None or L.get('route')!='PENDING_ACTIVE':raise RuntimeError('POST_T0_PENDING_H0_MISSING')
                    h0_ref={'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'sourceKey':str(P['sourceKey'])}
                    action_state=a1.RetainPendingOnceState(MID,h0_ref['originResponsibilityId'],h0_ref['targetExpandSide'],h0_ref['sourceKey'])
            elif qv:
                if tr is None:
                    sim._open_one_option(t,qv,end)
                elif arm=='N':
                    a1.dispatch_open_decision(sim,state=a1.RetainPendingOnceState(MID,-1,'NONE','NONE'),action='NATIVE',p0_key='',decision_ref=f'{MID}:{ordinal}',t=t,qv=qv,end=end)
                else:
                    pre_digest=old.b2.behavior_state_digest(sim);qstart=len(getattr(sim,'q_events',[]));pre=physical_snapshot(sim,p0)
                    do_retain=(mode.startswith('R') and detected_tstar is not None and int(ordinal)==int(detected_tstar['phaseOrdinal']))
                    if do_retain:
                        if int(t)!=int(detected_tstar['eventTimestampMs']) or pre_digest!=str(detected_tstar['preBehaviorDigest']):raise RuntimeError('R_COMMON_PREFIX_TSTAR_MISMATCH')
                        tstar_pre=pre
                        rr=a1.dispatch_open_decision(sim,state=action_state,action='RETAIN_PENDING_ONCE',p0_key=p0,decision_ref=f'{MID}:{ordinal}',t=t,qv=qv,end=end)
                        tstar_post=physical_snapshot(sim,p0)
                        retain={'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'preBehaviorDigest':pre_digest,'result':stable(rr),'pre':pre,'post':tstar_post}
                    else:
                        rr=a1.dispatch_open_decision(sim,state=action_state,action='NATIVE',p0_key=p0,decision_ref=f'{MID}:{ordinal}',t=t,qv=qv,end=end)
                        ev=active_event(getattr(sim,'q_events',[]),qstart,h0_ref['originResponsibilityId'])
                        if detected is None and ev is not None:
                            detected={'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'preBehaviorDigest':pre_digest,'pre':pre,'post':physical_snapshot(sim,p0),'activeEvent':stable(ev)}
                        if mode.startswith('R') and action_state.one_shot_used and reentry is None and retain is not None and int(ordinal)>int(retain['phaseOrdinal']):
                            reentry={'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'disposition':rr.get('disposition'),'nativeSubmitDelta':rr.get('nativeSubmitDelta'),'qEvents':stable(getattr(sim,'q_events',[])[qstart:]),'post':physical_snapshot(sim,p0)}
            if tr is not None:tr.sample_state(sim)
            sim._sample_occupancy();sim._postfork_peak()
        fin=old.clock.finalize(sim,spec,None);sim._postfork_peak();tele=tr.finish(sim);term=payoff(sim);num=numeric(tele)
        payments=[]
        if h0_ref is not None:
            start_t=int(retain['eventTimestampMs']) if retain is not None else (int(detected['eventTimestampMs']) if detected is not None else int(seam['eventTimestampMs']))
            payments=[copy.deepcopy(x) for x in sim.resp_payment_rows if int(x.get('t') or 0)>=start_t and int(x.get('responsibilityId') or -1)==int(h0_ref['originResponsibilityId']) and float(x.get('qty') or 0.0)>EPS]
        h0_remaining=None
        if h0_ref is not None:h0_remaining=float(tele['R0']['terminalRemainingById'].get(str(h0_ref['originResponsibilityId']),tele['R0']['terminalRemainingById'].get(h0_ref['originResponsibilityId'],0.0)))
        service={'h0PaymentQtyAfterDecision':sum(float(x.get('qty') or 0.0) for x in payments),'paymentRows':stable(payments),'h0TerminalRemaining':h0_remaining,'h0ServiceReplacedOrCompleted':h0_ref is not None and h0_remaining is not None and h0_remaining<=TOL and sum(float(x.get('qty') or 0.0) for x in payments)>EPS}
        checks={'forkAndCommit':all(sim.forkSnapshot['checks'].values()) and bool(sim.oneShotReceipt and sim.oneShotReceipt['pass']),'accountingClean':all(bool(v) for v in fin['accountingChecks'].values()),'max4':int(fin['terminal']['maxSlots'])<=4,'R0TerminalZero':abs(float(tele['R0']['terminalResidualNorm'] or 0.0))<=TOL,'reservedTerminalZero':abs(float(tele['risk']['reservedUnreturnedQuoteNotionalTerminalNorm'] or 0.0))<=TOL,'cancelPendingReservedTerminalZero':abs(float(tele['risk']['cancelPendingReservedQuoteNotionalTerminal'] or 0.0))<=TOL}
        if mode.startswith('R'):
            checks.update({'retainAccepted':retain is not None and retain['result'].get('disposition')=='HANDLED_NO_EMISSION' and not retain['result'].get('rejected'),'retainZeroPhysicalLedgerMutation':retain is not None and bool(retain['result'].get('checks',{}).get('zeroPhysicalOrLedgerMutation')),'oneShotUsedExactlyOnce':action_state is not None and action_state.one_shot_used and action_state.consumed_decision_ref==f"{MID}:{retain['phaseOrdinal']}" if retain is not None else False,'nativeReentryOccurred':reentry is not None})
        return {'mode':mode,'behaviorLedgerDigest':fin['behaviorLedgerDigest'],'terminal':term,'telemetry':tele,'numeric':num,'activity':{'trade':tele['activity']['T'],'twoSided':tele['activity']['B'],'repeated':tele['activity']['R'],'fills':tele['activity']['suffixConfirmedFills'],'links':len(tele['activity']['circulationLinks'])},'P0Key':p0,'h0Ref':h0_ref,'detectedTStar':detected,'retain':retain,'nativeReentry':reentry,'sameH0Service':service,'checks':checks,'correctnessPass':all(checks.values())}
    finally:sim.close()

def main():
    ap=argparse.ArgumentParser()
    for x in ('bundle','cohort','raw-manifest','formal-source','baseline-compact','tstar-freeze','stage0-compact','execution-manifest','output'):ap.add_argument('--'+x,required=True)
    a=ap.parse_args();src=json.loads(Path(a.formal_source).read_text(encoding='utf-8'));raw=json.loads(Path(a.raw_manifest).read_text(encoding='utf-8'));co=json.loads(Path(a.cohort).read_text(encoding='utf-8'));bc=json.loads(Path(a.baseline_compact).read_text(encoding='utf-8'));fts=json.loads(Path(a.tstar_freeze).read_text(encoding='utf-8'));s0=json.loads(Path(a.stage0_compact).read_text(encoding='utf-8'))
    if s0.get('verdict')!='STAGE0_ACTION_SET_WELL_FORMED_PRIMARY_HFT_AUTHORIZED':raise RuntimeError('STAGE0_NOT_AUTHORIZED')
    seam=copy.deepcopy(src['frozenSeam']);rr=raw['row'];spec=next(x for x in co['states'] if int(x['marketId'])==MID);manifest={'rawBundleIdentity':copy.deepcopy(rr['rawBundleIdentity']),'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in rr['R0']]}
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='b3_retain_primary_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:z.extract(f'tapes/{MID}.json.xz',root)
        tape=root/'tapes'/f'{MID}.json.xz'
        N=run_branch(tape,spec,seam,manifest,'N_DISABLED')
        C0=run_branch(tape,spec,seam,manifest,'C0_DISABLED')
        if not exact_tstar_match(C0['detectedTStar'],fts):raise RuntimeError('C0_DETECTED_TSTAR_FREEZE_MISMATCH')
        R=run_branch(tape,spec,seam,manifest,'R',fts,C0['detectedTStar'])
        NR=run_branch(tape,spec,seam,manifest,'N_REPEAT')
        C0R=run_branch(tape,spec,seam,manifest,'C0_DISABLED_REPEAT')
        RR=run_branch(tape,spec,seam,manifest,'R_REPEAT',fts,C0['detectedTStar'])
    formalN=src['branches']['N'];formalC0=src['branches']['P']
    npar={'behavior':N['behaviorLedgerDigest']==formalN['behaviorLedgerDigest'],'terminal':stable(N['terminal'])==stable(formalN['terminalEconomics']),'numeric':stable(N['numeric'])==stable(formalN['numeric']),'correctness':N['correctnessPass']}
    cpar={'behavior':C0['behaviorLedgerDigest']==formalC0['behaviorLedgerDigest'],'terminal':stable(C0['terminal'])==stable(formalC0['terminalEconomics']),'numeric':stable(C0['numeric'])==stable(formalC0['numeric']),'correctness':C0['correctnessPass']}
    repeats={'N':stable(N)==stable(NR),'C0':stable(C0)==stable(C0R),'R':stable(R)==stable(RR)}
    physicalFirst=None
    if R['retain'] is not None and C0['detectedTStar'] is not None:
        cp=C0['detectedTStar']['post'];rp=R['retain']['post'];physicalFirst={'phaseOrdinal':R['retain']['phaseOrdinal'],'eventTimestampMs':R['retain']['eventTimestampMs'],'c0SubmitDelta':int(cp['submits'])-int(C0['detectedTStar']['pre']['submits']),'rSubmitDelta':int(rp['submits'])-int(R['retain']['pre']['submits']),'c0SlotCount':len(cp['slots']),'rSlotCount':len(rp['slots']),'c0ActiveKey':None if cp['qLadder'] is None else cp['qLadder'].get('activeKey'),'rPendingPreserved':rp['qPendingActive'] is not None and rp['qLadder'] is not None and rp['qLadder'].get('route')=='PENDING_ACTIVE'}
    firstPass=physicalFirst is not None and physicalFirst['c0SubmitDelta']==1 and physicalFirst['rSubmitDelta']==0 and physicalFirst['c0ActiveKey'] is not None and physicalFirst['rPendingPreserved']
    kdelta={k:float(R['numeric'][k])-float(C0['numeric'][k]) for k in K};costEffect=any(abs(v)>CASH_TOL for v in kdelta.values())
    tails=bc['newTail'];tailChecks={k:float(R['numeric'][k])<=float(v)+CASH_TOL for k,v in tails.items() if k in R['numeric'] and v is not None}
    nterm=N['terminal'];econ={'RminusC0':{'deltaU':float(R['terminal']['U'])-float(C0['terminal']['U']),'deltaD':float(R['terminal']['D'])-float(C0['terminal']['D'])},'RminusN':{'deltaU':float(R['terminal']['U'])-float(nterm['U']),'deltaD':float(R['terminal']['D'])-float(nterm['D'])},'C0minusN':{'deltaU':float(C0['terminal']['U'])-float(nterm['U']),'deltaD':float(C0['terminal']['D'])-float(nterm['D'])}}
    econ['bilateralRelativeN']=econ['RminusN']['deltaU']>CASH_TOL and econ['RminusN']['deltaD']>CASH_TOL
    anti=bool(R['activity']['trade'] and R['activity']['twoSided'] and R['activity']['repeated'] and R['sameH0Service']['h0ServiceReplacedOrCompleted'] and R['nativeReentry'] is not None)
    mechanical=all(npar.values()) and all(cpar.values()) and all(repeats.values()) and R['correctnessPass'] and firstPass and R['nativeReentry'] is not None
    if not all(npar.values()) or not all(cpar.values()) or not all(repeats.values()) or not R['correctnessPass']:verdict='CORRECTNESS_OR_PROVENANCE_STOP'
    elif R['retain'] is None or not R['checks'].get('retainAccepted'):verdict='ACTION_NOT_REACHABLE'
    elif not firstPass:verdict='ACTION_EXERCISED_NO_PHYSICAL_FIRST_STAGE'
    elif R['nativeReentry'] is None:verdict='NO_NATIVE_REENTRY_BEFORE_TERMINAL'
    elif not anti:verdict='ANTI_COLLAPSE_NOT_IDENTIFIED'
    elif not costEffect:verdict='ACTION_EXERCISED_NO_BUDGET_PATH_EFFECT'
    else:verdict='PRIMARY_MECHANISM_EXERCISED_CONTINUE_FIXED_ANCHOR_PANEL'
    tags=[]
    if not all(tailChecks.values()):tags.append('PRIMARY_INDIVIDUAL_PLVAC_BUDGET_FAIL')
    if not econ['bilateralRelativeN']:tags.append('VALUE_ORDER_REMAINS_NON_TOTAL_OR_BILATERAL_NOT_PRESERVED')
    out={'version':'B3_RETAIN_PENDING_ONCE_PRIMARY1824852_V1_20260908','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'NParity':npar,'C0DisabledParity':cpar,'repeatParity':repeats,'physicalFirstStage':physicalFirst,'physicalFirstStagePass':firstPass,'KDeltaRminusC0':kdelta,'costPathEffectPass':costEffect,'individualTailChecksR':tailChecks,'individualTailPassR':all(tailChecks.values()),'economic':econ,'antiCollapseLocalPass':anti,'mechanicalPrimaryPass':mechanical,'R':R,'C0':C0,'N':N,'verdict':verdict,'tags':tags,'sha256':{'runner':sha(Path(__file__)),'actionModule':sha(Path(a1.__file__)),'stage0Compact':sha(a.stage0_compact),'tStarFreeze':sha(a.tstar_freeze),'formalSource':sha(a.formal_source),'bundle':sha(a.bundle),'executionManifest':sha(a.execution_manifest)},'boundaries':['A1 first-class RETAIN_PENDING_ONCE once only','principal contrast R-C0','N context only','no cancel/no resize/no virtual release/no fixed time','next native receipt re-entry','full horizon PLVAC telemetry','no fresh/reserve/8781/no belief']}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'tags':tags,'tStar':[C0['detectedTStar']['phaseOrdinal'],C0['detectedTStar']['eventTimestampMs']],'physicalFirst':physicalFirst,'reentry':R['nativeReentry'],'KDelta':kdelta,'tailPass':all(tailChecks.values()),'RminusN':econ['RminusN'],'TBR':R['activity'],'sameH0Service':R['sameH0Service']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
