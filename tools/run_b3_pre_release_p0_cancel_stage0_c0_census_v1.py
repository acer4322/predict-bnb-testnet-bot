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
def digest(x): return old.digest(x)
def live_status(s): return old.base.v2.base.live(str(s or ''))

def p0_snapshot(sim,key):
    o=sim.orders.get(key) or {}
    try: s=sim.snap(o) if o else {}
    except Exception: s={}
    st=str(s.get('status') or o.get('status') or '').upper(); cum=float(s.get('cumExecQty') if s.get('cumExecQty') is not None else o.get('cum') or 0.0)
    qty=float(o.get('qty') or 0.0); rem=max(0.0,qty-cum)
    sid=next((int(sid) for sid,k in sim.slot_key.items() if str(k)==str(key)),None)
    cur=None
    try: cur=sim.bt.orders(0).get(o.get('n')) if o else None
    except Exception: cur=None
    return {'key':str(key),'slotId':sid,'status':st,'cum':cum,'qty':qty,'remaining':rem,'cancelRequested':bool(o.get('cancelRequested')),
            'inSlotReservation':sid is not None,'cancellable':bool(getattr(cur,'cancellable',False)) if cur is not None else False,
            'terminal':st in old.base.v2.TERMINAL_STATUSES}

def order_ident(sim,key,target_expand_side=None):
    o=sim.orders.get(key) or {}
    return {'key':str(key),'side':str(o.get('side')),'role':str(sim.key_role.get(key,'UNASSIGNED')),'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),'targetExpandSide':target_expand_side}

def qevent_active_submit(events,start,orig_id):
    for e in events[start:]:
        et=str(e.get('event') or e.get('type') or '')
        rid=e.get('originResponsibilityId') if e.get('originResponsibilityId') is not None else e.get('responsibilityId')
        if 'ACTIVE_SUBMIT' in et and rid is not None and int(rid)==int(orig_id): return e
    return None

def numeric(tele):
    r=tele['risk']; z={'R0TerminalResidual':tele['R0']['terminalResidualNorm'],'R0Burden':tele['R0']['burdenNorm'],
      'R0LotResidualTail':max(float(v) for v in tele['R0']['lotResidualNorm'].values()),'R0LotBurdenTail':max(float(v) for v in tele['R0']['lotBurdenNorm'].values()),
      'cashAtRiskPeak':r['cashAtRiskPeakNorm'],'reservedQuotePeak':r['reservedUnreturnedQuoteNotionalPeakNorm'],'reservedQuoteTerminal':r['reservedUnreturnedQuoteNotionalTerminalNorm'],
      'grossIntegral':r['grossIntegralNorm'],'absNetIntegral':r['absNetIntegralNorm'],'burn':r['nonReplenishingBurnNorm'],'grossPeak':r['grossPeakNorm'],'absNetPeak':r['absNetPeakNorm']}
    for s in ('UP','DOWN'):
        z[f'newPeak_{s}']=tele['newService']['peakNormByServiceSide'][s]; z[f'newTerminal_{s}']=tele['newService']['terminalNormByServiceSide'][s]; z[f'newBurden_{s}']=tele['newService']['burdenNormByServiceSide'][s]
    return z

def main():
    ap=argparse.ArgumentParser();
    for x in ('bundle','cohort','raw-manifest','formal-source','baseline-compact','stage0-freeze','execution-manifest','output'): ap.add_argument('--'+x,required=True)
    a=ap.parse_args(); em=json.loads(Path(a.execution_manifest).read_text(encoding='utf-8')); exp=em['expectedSha256']
    checks_hash={'stage0Freeze':sha(a.stage0_freeze)==exp['stage0Freeze'],'formalSource1824852':sha(a.formal_source)==exp['formalSource1824852'],
      'rawIdentity1824852':sha(a.raw_manifest)==exp['rawIdentity1824852'],'cohort':sha(a.cohort)==exp['cohort'],'baselineCompact':sha(a.baseline_compact)==exp['baselineCompact'],
      'bundle':sha(a.bundle)==exp['bundle'],'oneShotRunner':sha(Path(old.__file__))==exp['oneShotRunner'],'plvacBaselineRunnerV2':sha(Path(bl.__file__))==exp['plvacBaselineRunnerV2']}
    if not all(checks_hash.values()): raise RuntimeError('PROVENANCE_HASH_STOP:'+json.dumps(checks_hash))
    src=json.loads(Path(a.formal_source).read_text(encoding='utf-8')); raw=json.loads(Path(a.raw_manifest).read_text(encoding='utf-8')); co=json.loads(Path(a.cohort).read_text(encoding='utf-8'))
    seam=copy.deepcopy(src['frozenSeam']); rr=raw['row']; spec=next(x for x in co['states'] if int(x['marketId'])==MID)
    manifest={'rawBundleIdentity':copy.deepcopy(rr['rawBundleIdentity']),'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],
              'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in rr['R0']]}
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='pre_release_c0_') as td:
      root=Path(td)
      with zipfile.ZipFile(a.bundle) as z: z.extract(f'tapes/{MID}.json.xz',root)
      sim=old.B3Fork(root/'tapes'/f'{MID}.json.xz',spec,'P',seam,manifest); tr=None; tstar=None; p0key=None
      try:
        updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0]))); first=int(sim.meta['firstReceivedMs']); old.base.v2.base.ex.advance_to(sim.bt,first)
        end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
          t=int(u[1]);
          if tr is not None: tr.advance(t,sim)
          sim.set_phase(ordinal,t); old.base.v2.base.ex.advance_to(sim.bt,t); sim.process(t); sim.cancel_expired(t); sim._refresh_slots(t); old.base.v2.base.apply(sim.book,u); qv=old.base.v2.base.quotes(sim.book)
          if qv: sim._risk_contract_if_needed(t); sim._reanchor_stale(t)
          if ordinal==int(seam['phaseOrdinal']):
            tr=bl.Tracker(sim,t,end,seam['AIdentity'],seam['PIdentity']); sim.dispatch_target(ordinal,t,qv,end); p0key=str(sim.selectedKey); tr.sample_state(sim)
          elif qv:
            pre_pending=copy.deepcopy(sim.q_pending_active); pre_ladder=copy.deepcopy(sim.q_ladder); pre_p0=p0_snapshot(sim,p0key) if p0key else None
            pre_digest=old.b2.behavior_state_digest(sim); qstart=len(getattr(sim,'q_events',[])); n0=int(sim.n)
            sim._open_one_option(t,qv,end)
            if tstar is None and pre_pending is not None and pre_ladder is not None:
              orig=sim.forkSnapshot.get('originalHandoff') or {}; orig_id=orig.get('originResponsibilityId') or orig.get('responsibilityId')
              evt=qevent_active_submit(getattr(sim,'q_events',[]),qstart,orig_id) if orig_id is not None else None
              if evt is not None:
                created=[]
                for n in range(n0,int(sim.n)):
                  for side in ('UP','DOWN'):
                    k=f'{side}_{n}'
                    if k in sim.orders: created.append(k)
                akey=created[0] if len(created)==1 else str(evt.get('key') or '')
                h0_match=(pre_ladder.get('route')=='PENDING_ACTIVE' and pre_pending.get('originResponsibilityId')==orig_id and pre_ladder.get('originResponsibilityId')==orig_id)
                p0_checks={'exists':pre_p0 is not None,'liveReserved':bool(pre_p0 and pre_p0['inSlotReservation'] and not pre_p0['terminal'] and pre_p0['remaining']>EPS),
                           'notCancelPending':bool(pre_p0 and not pre_p0['cancelRequested']),'nativeCancellable':bool(pre_p0 and pre_p0['cancellable'])}
                tstar={'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'frontier':'POST_REFRESH_REANCHOR_PRE_NATIVE_OPEN',
                       'preBehaviorDigest':pre_digest,'H0':copy.deepcopy(orig),'prePendingActive':pre_pending,'preQLadder':pre_ladder,'h0LineageMatch':bool(h0_match),
                       'P0':pre_p0,'P0Checks':p0_checks,'C0NativeActiveEvent':copy.deepcopy(evt),'C0AIdentity':order_ident(sim,akey,orig.get('targetExpandSide')),
                       'C0ActiveKey':akey,'samePhaseNativeSubmit':True}
          if tr is not None: tr.sample_state(sim)
          sim._sample_occupancy(); sim._postfork_peak()
        fin=old.clock.finalize(sim,spec,None); tele=tr.finish(sim); term=bl.payoff(sim)
        c0parity={'behaviorLedgerDigest':fin['behaviorLedgerDigest']==src['branches']['P']['behaviorLedgerDigest'],
          'terminal':stable(term)==stable(src['branches']['P']['terminalEconomics']),
          'telemetry':stable(tele)==stable(src['branches']['P']['telemetry']),
          'numeric':stable(numeric(tele))==stable(src['branches']['P']['numeric']),
          'selectedKey':p0key==str(src['branches']['P']['selectedExecution']['key']),
          'selectedConfirmedQty':math.isclose(float(src['branches']['P']['selectedExecution']['confirmedQty']),0.0,abs_tol=TOL),
          'accountingClean':all(bool(v) for v in fin['accountingChecks'].values()),'max4':int(fin['terminal']['maxSlots'])<=4}
      finally: sim.close()
    reachable=bool(tstar and tstar['h0LineageMatch'] and all(tstar['P0Checks'].values()))
    if not all(c0parity.values()): verdict='CORRECTNESS_OR_PROVENANCE_STOP'
    elif tstar is None or not reachable: verdict='PRE_RELEASE_SEAM_NOT_REACHABLE'
    else: verdict='STAGE0_C0_TSTAR_FROZEN_X_ISOLATION_TEST_AUTHORIZED'
    out={'version':'B3_PRE_RELEASE_P0_CANCEL_STAGE0_C0_CENSUS_V1_20260908','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'hashChecks':checks_hash,
         'C0Parity':c0parity,'t0':{'phaseOrdinal':seam['phaseOrdinal'],'eventTimestampMs':seam['eventTimestampMs'],'stateHash':seam['stateHash']},'P0Key':p0key,'tStar':tstar,
         'tStarReachable':reachable,'C0FinalNumeric':numeric(tele),'C0Terminal':term,'verdict':verdict,'XAuthorized':verdict=='STAGE0_C0_TSTAR_FROZEN_X_ISOLATION_TEST_AUTHORIZED',
         'boundaries':['C0 only; no X mutation executed','first actual original-H0 native Active submit frontier only','P0 future fill/release/budget/winner not used to select tStar','native cancellable inspected at current frontier only','full C0 formal parity required']}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'parity':all(c0parity.values()),'tStar':None if tstar is None else [tstar['phaseOrdinal'],tstar['eventTimestampMs']], 'P0':None if tstar is None else tstar['P0']},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
