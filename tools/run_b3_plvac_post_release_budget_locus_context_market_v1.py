"""Context-market wrapper for B3 PLVAC post-release budget locus audit.

Reuses the parity-certified primary tracer implementation.  No new behavior treatment.
One N_TRACE/P_TRACE pair per fixed context market.  Formal failed metrics are read from
the already-valid PLVAC source result; only monotone prefix-certifiable metrics are used.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, os, tempfile, zipfile
from pathlib import Path
import sys
STAGE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists(): sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import importlib.util
_core_path=STAGE/'run_b3_plvac_post_release_budget_locus_primary1824852_v2.py'
if _core_path.exists():
    _spec=importlib.util.spec_from_file_location('b3_plvac_primary_v2_stage',_core_path); core=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(core)
else:
    from tools import run_b3_plvac_post_release_budget_locus_primary1824852_v2 as core

ALLOWED={1824758,1825962,1825994}
SUPPORTED={'R0LotBurdenTail','grossIntegral','grossPeak','cashAtRiskPeak','newBurden_UP','newPeak_UP'}
EPS=1e-9; CASH_TOL=1e-7

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x): return core.stable(x)

def prefix_values(tr):
    q=max(EPS,float(tr.q)); c=max(EPS,float(tr.c)); H=max(EPS,float(tr.H))
    lot=[]
    for rid,q0 in tr.r0init.items(): lot.append(float(tr.r0Burden.get(rid,0.0))/max(EPS,float(q0)*H))
    return {
      'R0LotBurdenTail':max(lot) if lot else 0.0,
      'grossIntegral':float(tr.grossIntegral)/(q*H),
      'grossPeak':float(tr.grossPeak)/q,
      'cashAtRiskPeak':float(tr.cashRiskPeak)/c,
      'newBurden_UP':float(tr.newBurden.get('UP',0.0))/(q*H),
      'newPeak_UP':float(tr.newPeak.get('UP',0.0))/q,
    }

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--bundle',required=True); ap.add_argument('--cohort',required=True); ap.add_argument('--baseline-compact',required=True); ap.add_argument('--raw-manifest',required=True); ap.add_argument('--source-result',required=True); ap.add_argument('--prereg',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    mid=int(a.market_id)
    if mid not in ALLOWED: raise RuntimeError('MARKET_NOT_FIXED_CONTEXT_CONTROL')
    prereg=json.loads(Path(a.prereg).read_text(encoding='utf-8')); source=json.loads(Path(a.source_result).read_text(encoding='utf-8')); compact=json.loads(Path(a.baseline_compact).read_text(encoding='utf-8')); raw=json.loads(Path(a.raw_manifest).read_text(encoding='utf-8')); co=json.loads(Path(a.cohort).read_text(encoding='utf-8'))
    if sha(a.source_result)!=prereg['frozenSource']['sourceHashes'][str(mid)]: raise RuntimeError('SOURCE_RESULT_HASH_MISMATCH')
    if sha(a.baseline_compact)!=prereg['frozenSource']['baselineCompactSha256']: raise RuntimeError('BASELINE_COMPACT_HASH_MISMATCH')
    if int(source.get('marketId') or -1)!=mid or not source.get('allCorrectnessPass') or not source.get('exercisePass') or not all(source.get('parity',{}).values()): raise RuntimeError('SOURCE_RESULT_NOT_VALID')
    srcpf=(source.get('preflightHashFreeze') or {}).get('sha256') or {}
    if srcpf.get('oneShotRunner') and sha(Path(core.old.__file__))!=srcpf.get('oneShotRunner'): raise RuntimeError('ONESHOT_RUNNER_HASH_MISMATCH')
    if srcpf.get('baselineRunnerV2') and sha(Path(core.bl.__file__))!=srcpf.get('baselineRunnerV2'): raise RuntimeError('BASELINE_TRACKER_HASH_MISMATCH')
    failed=[k for k,v in source['assessor']['tailChecks'].items() if not v]
    unsupported=[k for k in failed if k not in SUPPORTED]
    if unsupported: raise RuntimeError('PREFIX_BOUND_NOT_CERTIFIABLE:'+','.join(unsupported))
    if int(raw.get('row',{}).get('marketId') or -1)!=mid or not raw.get('row',{}).get('pass'): raise RuntimeError('RAW_MANIFEST_NOT_CLEAN')
    spec=next(x for x in co['states'] if int(x['marketId'])==mid); seam=copy.deepcopy(source['frozenSeam']); manifest=core.make_manifest_row(raw['row'],seam); tails=compact['newTail']
    core.MID=mid; core.PRIMARY=tuple(failed); core.prefix_values=prefix_values
    pre={'version':'B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_CONTEXT_HASH_FREEZE_V1','createdBeforeTraceReplay':True,'marketId':mid,'failedMetrics':failed,
         'sha256':{'runner':sha(Path(__file__)),'primaryTracerV2':sha(Path(core.__file__)),'prereg':sha(a.prereg),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'baselineCompact':sha(a.baseline_compact),'rawIdentityManifest':sha(a.raw_manifest),'sourceResult':sha(a.source_result),'oneShotRunner':sha(Path(core.old.__file__)),'baselineTrackerV2':sha(Path(core.bl.__file__)),'menuObserver':sha(Path(core.margin.__file__))}}
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); (outdir/'preflight_hash_freeze.json').write_text(json.dumps(pre,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix=f'b3_locus_{mid}_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z: z.extract(f'tapes/{mid}.json.xz',root)
        tape=root/'tapes'/f'{mid}.json.xz'; traces={}
        for label,srcarm in (('N_TRACE','N'),('P_TRACE','P')):
            traces[label]=core.trace_branch(tape,spec,seam,manifest,source['branches'][srcarm],label,tails)
            print(json.dumps({'marketId':mid,'arm':label,'parityPass':traces[label]['parityPass'],'rho':traces[label].get('rho'),'tau':None if traces[label].get('tau') is None else {'phaseOrdinal':traces[label]['tau']['phaseOrdinal'],'eventTimestampMs':traces[label]['tau']['eventTimestampMs']},'sigmas':traces[label].get('sigmas')},ensure_ascii=False),flush=True)
    correctness=all(x['parityPass'] for x in traces.values())
    if not correctness: verdict='CORRECTNESS_OR_PROVENANCE_STOP'; detail={}
    elif traces['P_TRACE'].get('rho') is None: verdict='LOCUS_TEST_NOT_IDENTIFIED'; detail={'reason':'RHO_NOT_IDENTIFIED'}
    elif not failed: verdict='CONTEXT_CONTROL_NO_FORMAL_TAIL_FAILURE'; detail={}
    else: verdict,detail=core.classify(traces['P_TRACE'],tails)
    P=traces['P_TRACE']; tau=P.get('tau')
    if tau is None:
        cash_lb=float(source['branches']['P']['numeric']['cashAtRiskPeak']); cash_lb_source='ORIGINAL_P_FINAL_NO_POST_RELEASE_H0_LEVER'
    else:
        cash_lb=float(tau['prefix']['cashAtRiskPeak']); cash_lb_source='TAU_MINUS_PREFIX_RUNNING_PEAK'
    out={'version':'B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_FALSIFICATION_AUDIT4_V1_CONTEXT_MARKET_V1_20260908','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'primary':False,'formalFailedMetrics':failed,'preflightHashFreeze':pre,'traces':traces,'correctnessPass':correctness,'verdict':verdict,'verdictDetail':detail,
         'cashAtRiskPeakPostReleaseClassLowerBound':cash_lb,'cashAtRiskPeakLowerBoundSource':cash_lb_source,
         'boundaries':['same formal N/P policies only','no new behavior treatment','no winner used in locator/verdict','official PLVAC V2 tracker cadence','context control only; no new hypothesis','no fresh/reserve/8781/budget retuning']}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'marketId':mid,'correctnessPass':correctness,'verdict':verdict,'cashAtRiskPeakLowerBound':cash_lb},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
