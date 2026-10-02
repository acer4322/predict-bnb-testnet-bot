from __future__ import annotations
import argparse,copy,hashlib,json,math,os,tempfile,zipfile,sys
from pathlib import Path
STAGE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists(): sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_b3_handoff_native_bundle_oneshot_smoke4_v1 as old
from tools import run_b3_plvac_baseline_freeze_v2 as bl
MID=1824852; EPS=1e-9; TOL=1e-8; CASH_TOL=1e-7

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x): return old.stable(x)
def close(a,b,tol=TOL): return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)

def numeric(tele):
    r=tele['risk']; z={'R0TerminalResidual':tele['R0']['terminalResidualNorm'],'R0Burden':tele['R0']['burdenNorm'],
      'R0LotResidualTail':max(float(v) for v in tele['R0']['lotResidualNorm'].values()),'R0LotBurdenTail':max(float(v) for v in tele['R0']['lotBurdenNorm'].values()),
      'cashAtRiskPeak':r['cashAtRiskPeakNorm'],'reservedQuotePeak':r['reservedUnreturnedQuoteNotionalPeakNorm'],'reservedQuoteTerminal':r['reservedUnreturnedQuoteNotionalTerminalNorm'],
      'grossIntegral':r['grossIntegralNorm'],'absNetIntegral':r['absNetIntegralNorm'],'burn':r['nonReplenishingBurnNorm'],'grossPeak':r['grossPeakNorm'],'absNetPeak':r['absNetPeakNorm']}
    for s in ('UP','DOWN'):
        z[f'newPeak_{s}']=tele['newService']['peakNormByServiceSide'][s]; z[f'newTerminal_{s}']=tele['newService']['terminalNormByServiceSide'][s]; z[f'newBurden_{s}']=tele['newService']['burdenNormByServiceSide'][s]
    return z

def p0_snap(sim,key):
    o=sim.orders.get(key) or {}
    try: s=sim.snap(o) if o else {}
    except Exception: s={}
    st=str(s.get('status') or o.get('status') or '').upper(); cum=float(s.get('cumExecQty') if s.get('cumExecQty') is not None else o.get('cum') or 0.0)
    qty=float(o.get('qty') or 0.0); rem=max(0.0,qty-cum); sid=next((int(sid) for sid,k in sim.slot_key.items() if str(k)==str(key)),None)
    cur=None
    try: cur=sim.bt.orders(0).get(o.get('n')) if o else None
    except Exception: cur=None
    return {'status':st,'cum':cum,'qty':qty,'remaining':rem,'cancelRequested':bool(o.get('cancelRequested')),'inSlotReservation':sid is not None,
            'slotId':sid,'cancellable':bool(getattr(cur,'cancellable',False)) if cur is not None else False,'reservedQuote':rem*float(o.get('price') or 0.0) if sid is not None else 0.0}

def ident(sim,key,target):
    o=sim.orders.get(key) or {}; return {'side':str(o.get('side')),'role':str(sim.key_role.get(key,'UNASSIGNED')),'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),'targetExpandSide':target}

def exact_ident(a,b):
    return str(a.get('side'))==str(b.get('side')) and str(a.get('role'))==str(b.get('role')) and float(a.get('price')).hex()==float(b.get('price')).hex() and float(a.get('qty')).hex()==float(b.get('qty')).hex() and a.get('targetExpandSide')==b.get('targetExpandSide')

def active_event(evs,start,rid):
    for e in evs[start:]:
        et=str(e.get('event') or e.get('type') or '')
        rr=e.get('originResponsibilityId') if e.get('originResponsibilityId') is not None else e.get('responsibilityId')
        if 'ACTIVE_SUBMIT' in et and rr is not None and int(rr)==int(rid): return copy.deepcopy(e)
    return None

def payoff(sim): return bl.payoff(sim)

def run_branch(tape,spec,seam,manifest,tstar,mode):
    sim=old.B3Fork(tape,spec,'P',seam,manifest); tr=None; p0=None; timeline=[]; mutation=None; tstar_check=None; active_ident=None
    try:
        updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0]))); first=int(sim.meta['firstReceivedMs']); old.base.v2.base.ex.advance_to(sim.bt,first)
        end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
            t=int(u[1]);
            if tr is not None: tr.advance(t,sim)
            sim.set_phase(ordinal,t); old.base.v2.base.ex.advance_to(sim.bt,t); sim.process(t); sim.cancel_expired(t); sim._refresh_slots(t)
            if p0 is not None and ordinal>=int(tstar['phaseOrdinal']): timeline.append({'phaseOrdinal':int(ordinal),'eventTimestampMs':t,'frontier':'AFTER_REFRESH_BEFORE_BOOK',**p0_snap(sim,p0)})
            old.base.v2.base.apply(sim.book,u); qv=old.base.v2.base.quotes(sim.book)
            if qv: sim._risk_contract_if_needed(t); sim._reanchor_stale(t)
            if ordinal==int(seam['phaseOrdinal']):
                tr=bl.Tracker(sim,t,end,seam['AIdentity'],seam['PIdentity']); sim.dispatch_target(ordinal,t,qv,end); p0=str(sim.selectedKey); tr.sample_state(sim)
            elif qv:
                if ordinal==int(tstar['phaseOrdinal']):
                    if t!=int(tstar['eventTimestampMs']): raise RuntimeError('TSTAR_TIMESTAMP_MISMATCH')
                    pre_digest=old.b2.behavior_state_digest(sim); ps=p0_snap(sim,p0); L=copy.deepcopy(sim.q_ladder); P=copy.deepcopy(sim.q_pending_active); rid=int(tstar['H0']['originResponsibilityId'])
                    tstar_check={'preBehaviorDigestExact':pre_digest==str(tstar['preBehaviorDigest']),'P0KeyExact':p0==str(tstar['P0']['key']),'P0SlotExact':ps['slotId']==int(tstar['P0']['slotId']),
                                 'P0RemainingExact':close(ps['remaining'],tstar['P0']['remainingAtTStar']),'P0LiveReserved':ps['inSlotReservation'] and ps['remaining']>EPS,
                                 'P0NotCancelPending':not ps['cancelRequested'],'P0Cancellable':ps['cancellable'],
                                 'H0PendingExact':L is not None and P is not None and L.get('route')=='PENDING_ACTIVE' and int(L.get('originResponsibilityId'))==rid and int(P.get('originResponsibilityId'))==rid}
                    if mode=='X':
                        before_hist=len(sim.slot_history); ok=sim._request_cancel(t,int(ps['slotId']),'B3_PRE_RELEASE_P0_CANCEL_AT_H0_COMMIT'); post=p0_snap(sim,p0)
                        mutation={'nativeCancelReturned':bool(ok),'cancelRequestedAfter':post['cancelRequested'],'reservationRetainedImmediately':post['inSlotReservation'],'remainingAfterRequest':post['remaining'],
                                  'slotHistory':stable(sim.slot_history[before_hist:])}
                    qstart=len(getattr(sim,'q_events',[])); n0=int(sim.n); sim._open_one_option(t,qv,end); evt=active_event(getattr(sim,'q_events',[]),qstart,rid)
                    made=[]
                    for n in range(n0,int(sim.n)):
                        for side in ('UP','DOWN'):
                            k=f'{side}_{n}'
                            if k in sim.orders: made.append(k)
                    akey=made[0] if len(made)==1 else (str(evt.get('key')) if evt else None)
                    active_ident=None if akey is None else ident(sim,akey,tstar['H0']['targetExpandSide'])
                    tstar_check['samePhaseH0ActiveSubmitted']=evt is not None and akey is not None
                    tstar_check['AIdentityExact']=active_ident is not None and exact_ident(active_ident,tstar['C0NativeAAtTStar'])
                else: sim._open_one_option(t,qv,end)
            if tr is not None: tr.sample_state(sim)
            sim._sample_occupancy(); sim._postfork_peak()
        fin=old.clock.finalize(sim,spec,None); sim._postfork_peak(); tele=tr.finish(sim); term=payoff(sim); num=numeric(tele)
        direct=[x for x in sim.fill_accounting[sim.forkSnapshot['preFillAccountingLen']:] if str(x.get('key'))==str(p0)]
        dq=sum(float(x.get('confirmedQty') or 0.0) for x in direct)
        checks={'forkAndCommit':all(sim.forkSnapshot['checks'].values()) and bool(sim.oneShotReceipt and sim.oneShotReceipt['pass']),'tStarChecks':bool(tstar_check and all(tstar_check.values())),
                'accountingClean':all(bool(v) for v in fin['accountingChecks'].values()),'max4':int(fin['terminal']['maxSlots'])<=4,'R0TerminalZero':abs(float(tele['R0']['terminalResidualNorm'] or 0.0))<=TOL,
                'reservedTerminalZero':abs(float(tele['risk']['reservedUnreturnedQuoteNotionalTerminalNorm'] or 0.0))<=TOL,'cancelPendingReservedTerminalZero':abs(float(tele['risk']['cancelPendingReservedQuoteNotionalTerminal'] or 0.0))<=TOL}
        return {'mode':mode,'behaviorLedgerDigest':fin['behaviorLedgerDigest'],'terminal':term,'telemetry':tele,'numeric':num,'activity':{'T':tele['activity']['T'],'B':tele['activity']['B'],'R':tele['activity']['R'],'fills':tele['activity']['suffixConfirmedFills'],'links':len(tele['activity']['circulationLinks'])},
                'P0Key':p0,'P0ConfirmedQty':dq,'timeline':timeline,'tStarChecks':tstar_check,'mutation':mutation,'AIdentityAtTStar':active_ident,'checks':checks,'correctnessPass':all(checks.values())}
    finally: sim.close()

def first_physical_div(c0,x):
    a={(r['phaseOrdinal'],r['eventTimestampMs']):r for r in c0['timeline']}; b={(r['phaseOrdinal'],r['eventTimestampMs']):r for r in x['timeline']}
    for k in sorted(set(a)&set(b)):
        A=a[k];B=b[k]
        diffs=[]
        for f in ('inSlotReservation','status'):
            if A[f]!=B[f]: diffs.append(f)
        for f in ('cum','remaining','reservedQuote'):
            if not close(A[f],B[f]): diffs.append(f)
        if diffs: return {'phaseOrdinal':k[0],'eventTimestampMs':k[1],'differingPhysicalFields':diffs,'C0':A,'X':B}
    return None

def main():
    ap=argparse.ArgumentParser()
    for x in ('bundle','cohort','raw-manifest','formal-source','baseline-compact','stage0-freeze','execution-manifest','tstar-freeze','output'): ap.add_argument('--'+x,required=True)
    a=ap.parse_args(); src=json.loads(Path(a.formal_source).read_text(encoding='utf-8')); raw=json.loads(Path(a.raw_manifest).read_text(encoding='utf-8')); co=json.loads(Path(a.cohort).read_text(encoding='utf-8')); bc=json.loads(Path(a.baseline_compact).read_text(encoding='utf-8')); ts=json.loads(Path(a.tstar_freeze).read_text(encoding='utf-8'))
    if not ts.get('XAuthorized') or int(ts.get('marketId'))!=MID: raise RuntimeError('X_NOT_AUTHORIZED')
    if sha(a.formal_source)!='484c83bf452f3986ee656defae0b2e3ddd57b5810c086af199856f4ebb2746b7': raise RuntimeError('FORMAL_SOURCE_HASH_STOP')
    if sha(Path(old.__file__))!='c8da3a6f217fb57ad341ef58954b256bba4e91de926ee52f58fc00fb93e0887a' or sha(Path(bl.__file__))!='12eb9f1691ad69dc6dcb986aa6990ab65d21f44bff3959675fdae896230e0774': raise RuntimeError('SUBSTRATE_HASH_STOP')
    seam=copy.deepcopy(src['frozenSeam']); rr=raw['row']; spec=next(x for x in co['states'] if int(x['marketId'])==MID)
    tstar={'phaseOrdinal':int(ts['tStar']['phaseOrdinal']),'eventTimestampMs':int(ts['tStar']['eventTimestampMs']),'frontier':ts['tStar']['frontier'],'preBehaviorDigest':ts['tStar']['preBehaviorDigest'],'P0':copy.deepcopy(ts['P0']),'H0':copy.deepcopy(ts['H0']),'C0NativeAAtTStar':copy.deepcopy(ts['C0NativeAAtTStar'])}
    manifest={'rawBundleIdentity':copy.deepcopy(rr['rawBundleIdentity']),'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in rr['R0']]}
    with tempfile.TemporaryDirectory(prefix='pre_release_x_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z: z.extract(f'tapes/{MID}.json.xz',root)
        tape=root/'tapes'/f'{MID}.json.xz'; c0=run_branch(tape,spec,seam,manifest,tstar,'C0_DISABLED_REPEAT'); x=run_branch(tape,spec,seam,manifest,tstar,'X')
    c0par={'behavior':c0['behaviorLedgerDigest']==src['branches']['P']['behaviorLedgerDigest'],'terminal':stable(c0['terminal'])==stable(src['branches']['P']['terminalEconomics']),'telemetry':stable(c0['telemetry'])==stable(src['branches']['P']['telemetry']),'numeric':stable(c0['numeric'])==stable(src['branches']['P']['numeric']),'correctness':c0['correctnessPass']}
    iso={'cancelAdmission':bool(x['mutation'] and x['mutation']['nativeCancelReturned'] and x['mutation']['cancelRequestedAfter']),'reservationRetainedImmediately':bool(x['mutation'] and x['mutation']['reservationRetainedImmediately']),'AUnchangedSamePhase':bool(x['tStarChecks'] and x['tStarChecks']['samePhaseH0ActiveSubmitted'] and x['tStarChecks']['AIdentityExact']),'XCorrectness':x['correctnessPass']}
    first=first_physical_div(c0,x); firstStage=first is not None
    tails=bc['newTail']; tailChecks={k:float(x['numeric'][k])<=float(v)+CASH_TOL for k,v in tails.items() if k in x['numeric'] and v is not None}
    J=['R0LotBurdenTail','grossIntegral','grossPeak']; jDelta={k:float(x['numeric'][k])-float(c0['numeric'][k]) for k in J}; jImproved=[k for k,v in jDelta.items() if v < -CASH_TOL]
    N=src['branches']['N']['terminalEconomics']; xn={'deltaU':float(x['terminal']['U'])-float(N['U']),'deltaD':float(x['terminal']['D'])-float(N['D'])}
    xc0={'deltaU':float(x['terminal']['U'])-float(c0['terminal']['U']),'deltaD':float(x['terminal']['D'])-float(c0['terminal']['D'])}
    bilateral=xn['deltaU']>CASH_TOL and xn['deltaD']>CASH_TOL
    tbr=bool(x['activity']['T'] and x['activity']['B'] and x['activity']['R'])
    if not all(c0par.values()) or not iso['XCorrectness']: verdict='CORRECTNESS_OR_PROVENANCE_STOP'
    elif not iso['cancelAdmission'] or not iso['reservationRetainedImmediately'] or not iso['AUnchangedSamePhase']: verdict='ACTION_UNIT_NOT_ISOLATABLE'
    elif not firstStage: verdict='REQUEST_EXERCISED_OCCUPANCY_FIRST_STAGE_ABSENT'
    elif not jImproved: verdict='BUDGET_PATH_SEPARATION_NOT_IDENTIFIED'
    elif not all(tailChecks.values()): verdict='BUDGET_PATH_EFFECT_WITHOUT_FEASIBLE_SEPARATION'
    elif not bilateral: verdict='BILATERAL_WITNESS_NOT_PRESERVED'
    elif not tbr: verdict='ANTI_COLLAPSE_NOT_IDENTIFIED'
    else: verdict='PRIMARY_LOCAL_GAIN_COST_SEPARATION_PASS_CONTINUE_STAGE1_ANCHORS'
    out={'version':'B3_PRE_RELEASE_P0_CANCEL_PRIMARY1824852_X_V1_20260908','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'tStarFreezeSha256':sha(a.tstar_freeze),'C0Parity':c0par,'isolation':iso,'firstPhysicalStage':first,'firstStagePass':firstStage,
         'JDeltaXminusC0':jDelta,'JImproved':jImproved,'individualTailChecksX':tailChecks,'individualTailPassX':all(tailChecks.values()),'economic':{'XminusC0':xc0,'XminusN':xn,'bilateralRelativeN':bilateral},
         'activityX':x['activity'],'antiCollapseLocalPass':tbr,'C0':c0,'X':x,'verdict':verdict,
         'boundaries':['one native cancel(P0) only at frozen tStar','cancel-pending reservation retained until confirmed release','same-phase native H0 A identity exact or fail','no raw submit fallback','X-C0 primary causal contrast','N formal branch context only','no winner-conditioned trigger/no fresh/no8781/no belief']}
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'isolation':iso,'firstStage':None if first is None else [first['phaseOrdinal'],first['eventTimestampMs'],first['differingPhysicalFields']], 'JDelta':jDelta,'tailPass':all(tailChecks.values()),'XminusN':xn,'TBR':x['activity']},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
