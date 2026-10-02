"""Baseline-only raw native identity freeze for PLVAC first treatment 1824758.

No treatment dispatch occurs.  The script stops at the already-frozen phase/timestamp,
verifies state/prefix/R0 against the baseline-complete result, and serializes the native
A/P candidate identities with Python's round-trip float representation plus float.hex
sidecars.  It never searches for another seam and never reads outcome labels.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, os, tempfile, zipfile
from pathlib import Path
import sys
STAGE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists(): sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_b3_plvac_baseline_freeze_v2 as bl
from tools import run_native_selection_margin_prevalence_stagea16_v1 as margin
b2=margin.b2; base=margin.base
MID=1824758

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x): return b2.stable(x)
def cert(i):
    return {k:({'value':float(v),'hex':float(v).hex()} if k in {'price','qty'} else copy.deepcopy(v)) for k,v in i.items()}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--cohort',required=True); ap.add_argument('--baseline-result',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    baseline=json.loads(Path(a.baseline_result).read_text(encoding='utf-8')); row=next(x for x in baseline['rows'] if int(x['marketId'])==MID); frozen=row['seam']
    co=json.loads(Path(a.cohort).read_text(encoding='utf-8')); spec=next(x for x in co['states'] if int(x['marketId'])==MID)
    sim=bl.clock.InstrumentedFork(None,spec,'II','N') if False else None
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='plvac_rawid_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z: z.extract(f'tapes/{MID}.json.xz',root)
        tape=root/'tapes'/f'{MID}.json.xz'; sim=bl.clock.InstrumentedFork(tape,spec,'II','N')
        try:
            updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0]))); first=int(sim.meta['firstReceivedMs']); base.v2.base.ex.advance_to(sim.bt,first); end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
            found=None
            for ordinal,u in enumerate(updates):
                t=int(u[1]); sim.set_phase(ordinal,t); base.v2.base.ex.advance_to(sim.bt,t); sim.process(t); sim.cancel_expired(t); sim._refresh_slots(t); base.v2.base.apply(sim.book,u); qv=base.v2.base.quotes(sim.book)
                if qv: sim._risk_contract_if_needed(t); sim._reanchor_stale(t)
                if ordinal==int(frozen['phaseOrdinal']):
                    if t!=int(frozen['eventTimestampMs']) or qv is None: raise RuntimeError('FROZEN_LOCATOR_MISMATCH')
                    before=b2.behavior_state_digest(sim); aud=margin.audit_current_state(sim,ordinal,t,qv,end); after=b2.behavior_state_digest(sim)
                    ref=aud['reference']; A=copy.deepcopy(ref['candidates']['BOUNDED_ACTIVE']); P=copy.deepcopy(ref['candidates']['PASSIVE_PRIMARY']); ai=A.get('identity'); pi=P.get('identity'); r0=bl.open_lots(sim)
                    checks={'stateHashExact':aud['stateHash']==frozen['stateHash'],'prefixDigestExact':before==frozen['behaviorPrefixDigest'],'observerReadOnly':before==after and aud['observerMutationInert'],
                            'referenceClean':aud['errorsClean'] and aud['menuScopePass'],'ACompleteLegal':ai is not None and A.get('L')=='TRUE' and A.get('A')=='TRUE','PCompleteLegal':pi is not None and P.get('L')=='TRUE' and P.get('A')=='TRUE',
                            'nativePriorityA':ref.get('priority')=='BOUNDED_ACTIVE','stableARepresentationMatchesBaseline':stable(ai)==stable(frozen['AIdentity']),'stablePRepresentationMatchesBaseline':stable(pi)==stable(frozen['PIdentity']),'stableR0MatchesBaseline':stable(r0)==stable(frozen['R0'])}
                    found={'marketId':MID,'phaseOrdinal':ordinal,'eventTimestampMs':t,'stateHash':aud['stateHash'],'behaviorPrefixDigest':before,'rawBundleIdentity':{'A':ai,'P':pi},'rawIdentityCertificate':{'A':cert(ai),'P':cert(pi)},'R0':r0,'checks':checks,'pass':all(checks.values())}
                    break
                if qv: sim._open_one_option(t,qv,end)
                sim._sample_occupancy()
            if found is None: raise RuntimeError('FROZEN_SEAM_NOT_REACHED')
        finally: sim.close()
    if not found['pass']: raise RuntimeError('RAW_IDENTITY_FREEZE_CORRECTNESS_FAIL:'+json.dumps(found['checks'],sort_keys=True))
    out={'version':'B3_PLVAC_FIRST_TREATMENT_RAW_IDENTITY_MANIFEST_V1_20260908','researchOnly':True,'runtimeAuthority':False,'createdWithoutTreatment':True,'winnerRead':False,'row':found,
         'sha256':{'builder':sha(Path(__file__)),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'baselineResult':sha(a.baseline_result)},
         'boundary':['fixed 1824758 frozen seam only','baseline N prefix only','no action dispatched at seam','lossless round-trip float identity plus float.hex certificate','no winner/future/P outcome']}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'marketId':MID,'phase':found['phaseOrdinal'],'Ahex':found['rawIdentityCertificate']['A'],'Phex':found['rawIdentityCertificate']['P']},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
