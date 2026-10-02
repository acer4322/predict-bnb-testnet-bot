"""Generic baseline-only lossless native identity freeze for authorized PLVAC remaining markets."""
from __future__ import annotations
import argparse,copy,hashlib,json,os,tempfile,zipfile,sys
from pathlib import Path
STAGE=Path(__file__).resolve().parent;ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists():sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_b3_plvac_baseline_freeze_v2 as bl
from tools import run_native_selection_margin_prevalence_stagea16_v1 as margin
b2=margin.b2;base=margin.base

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x):return b2.stable(x)
def cert(i):return {k:({'value':float(v),'hex':float(v).hex()} if k in {'price','qty'} else copy.deepcopy(v)) for k,v in i.items()}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--bundle',required=True);ap.add_argument('--cohort',required=True);ap.add_argument('--baseline-result',required=True);ap.add_argument('--authorization',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id)
 auth=json.loads(Path(a.authorization).read_text(encoding='utf-8'))
 if not auth.get('remainingThreeAuthorizedByPreregisteredGate') or mid not in [int(x) for x in auth.get('remainingMarkets',[])]:raise RuntimeError('MARKET_NOT_AUTHORIZED')
 baseline=json.loads(Path(a.baseline_result).read_text(encoding='utf-8'));row=next(x for x in baseline['rows'] if int(x['marketId'])==mid);frozen=row['seam']
 co=json.loads(Path(a.cohort).read_text(encoding='utf-8'));spec=next(x for x in co['states'] if int(x['marketId'])==mid)
 outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
 with tempfile.TemporaryDirectory(prefix='plvac_rawid_market_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:z.extract(f'tapes/{mid}.json.xz',root)
  sim=bl.clock.InstrumentedFork(root/'tapes'/f'{mid}.json.xz',spec,'II','N')
  try:
   updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);base.v2.base.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs']);found=None
   for ordinal,u in enumerate(updates):
    t=int(u[1]);sim.set_phase(ordinal,t);base.v2.base.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);sim._refresh_slots(t);base.v2.base.apply(sim.book,u);qv=base.v2.base.quotes(sim.book)
    if qv:sim._risk_contract_if_needed(t);sim._reanchor_stale(t)
    if ordinal==int(frozen['phaseOrdinal']):
     if t!=int(frozen['eventTimestampMs']) or qv is None:raise RuntimeError('FROZEN_LOCATOR_MISMATCH')
     before=b2.behavior_state_digest(sim);aud=margin.audit_current_state(sim,ordinal,t,qv,end);after=b2.behavior_state_digest(sim);ref=aud['reference'];A=copy.deepcopy(ref['candidates']['BOUNDED_ACTIVE']);P=copy.deepcopy(ref['candidates']['PASSIVE_PRIMARY']);ai=A.get('identity');pi=P.get('identity');r0=bl.open_lots(sim)
     checks={'stateHashExact':aud['stateHash']==frozen['stateHash'],'prefixDigestExact':before==frozen['behaviorPrefixDigest'],'observerReadOnly':before==after and aud['observerMutationInert'],'referenceClean':aud['errorsClean'] and aud['menuScopePass'],'ACompleteLegal':ai is not None and A.get('L')=='TRUE' and A.get('A')=='TRUE','PCompleteLegal':pi is not None and P.get('L')=='TRUE' and P.get('A')=='TRUE','nativePriorityA':ref.get('priority')=='BOUNDED_ACTIVE','stableARepresentationMatchesBaseline':stable(ai)==stable(frozen['AIdentity']),'stablePRepresentationMatchesBaseline':stable(pi)==stable(frozen['PIdentity']),'stableR0MatchesBaseline':stable(r0)==stable(frozen['R0'])}
     found={'marketId':mid,'phaseOrdinal':ordinal,'eventTimestampMs':t,'stateHash':aud['stateHash'],'behaviorPrefixDigest':before,'rawBundleIdentity':{'A':ai,'P':pi},'rawIdentityCertificate':{'A':cert(ai),'P':cert(pi)},'R0':r0,'checks':checks,'pass':all(checks.values())};break
    if qv:sim._open_one_option(t,qv,end)
    sim._sample_occupancy()
  finally:sim.close()
 if found is None or not found['pass']:raise RuntimeError('RAW_IDENTITY_FREEZE_CORRECTNESS_FAIL:'+json.dumps(None if found is None else found['checks'],sort_keys=True))
 out={'version':'B3_PLVAC_RAW_IDENTITY_MANIFEST_MARKET_V1_20260908','researchOnly':True,'runtimeAuthority':False,'createdWithoutTreatment':True,'winnerRead':False,'row':found,'sha256':{'builder':sha(Path(__file__)),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'baselineResult':sha(a.baseline_result),'authorization':sha(a.authorization)},'boundary':['authorized remaining market only','frozen baseline seam only','no action dispatched at seam','lossless float identity + hex','no winner/future/P outcome']}
 op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'marketId':mid,'phase':found['phaseOrdinal'],'A':found['rawIdentityCertificate']['A'],'P':found['rawIdentityCertificate']['P']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
