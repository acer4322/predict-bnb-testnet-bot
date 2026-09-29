from __future__ import annotations
import copy, importlib.util, json, os, sys, tempfile, zipfile
from pathlib import Path
STAGE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_b3_retain_pending_once_primary1824852_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('retain_primary_staged',_STAGED); rp=importlib.util.module_from_spec(sp);sys.modules[sp.name]=rp;sp.loader.exec_module(rp)
else:
    from tools import run_b3_retain_pending_once_primary1824852_v1 as rp
MID=1824852

def canonical(x):
    keys=('behaviorLedgerDigest','terminal','telemetry','numeric','activity','P0Key','h0Ref','detectedTStar','retain','nativeReentry','sameH0Service','checks','correctnessPass')
    return rp.stable({k:copy.deepcopy(x.get(k)) for k in keys})

def main():
    import argparse
    ap=argparse.ArgumentParser()
    for x in ('bundle','cohort','raw-manifest','formal-source','baseline-compact','tstar-freeze','first-result','output'):ap.add_argument('--'+x,required=True)
    a=ap.parse_args();src=json.load(open(a.formal_source,encoding='utf-8'));raw=json.load(open(a.raw_manifest,encoding='utf-8'));co=json.load(open(a.cohort,encoding='utf-8'));fts=json.load(open(a.tstar_freeze,encoding='utf-8'));first=json.load(open(a.first_result,encoding='utf-8'))
    seam=copy.deepcopy(src['frozenSeam']);rr=raw['row'];spec=next(x for x in co['states'] if int(x['marketId'])==MID);manifest={'rawBundleIdentity':copy.deepcopy(rr['rawBundleIdentity']),'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in rr['R0']]}
    dt=first['C0']['detectedTStar']
    with tempfile.TemporaryDirectory(prefix='retain_repeat_controls_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:z.extract(f'tapes/{MID}.json.xz',root)
        tape=root/'tapes'/f'{MID}.json.xz'
        nr=rp.run_branch(tape,spec,seam,manifest,'N_REPEAT')
        cr=rp.run_branch(tape,spec,seam,manifest,'C0_DISABLED_REPEAT')
        rrpt=rp.run_branch(tape,spec,seam,manifest,'R_REPEAT',fts,dt)
    comparisons={'N':canonical(first['N'])==canonical(nr),'C0':canonical(first['C0'])==canonical(cr),'R':canonical(first['R'])==canonical(rrpt)}
    detail={k:{'behavior':first[k if k!='R' else 'R']['behaviorLedgerDigest']==z['behaviorLedgerDigest'],'terminal':rp.stable(first[k if k!='R' else 'R']['terminal'])==rp.stable(z['terminal']),'telemetry':rp.stable(first[k if k!='R' else 'R']['telemetry'])==rp.stable(z['telemetry']),'numeric':rp.stable(first[k if k!='R' else 'R']['numeric'])==rp.stable(z['numeric']),'correctness':bool(z['correctnessPass'])} for k,z in [('N',nr),('C0',cr),('R',rrpt)]}
    verdict='PRIMARY_REPEAT_CANONICAL_PARITY_PASS' if all(comparisons.values()) and all(all(v.values()) for v in detail.values()) else 'CORRECTNESS_OR_PROVENANCE_STOP'
    out={'version':'B3_RETAIN_PENDING_ONCE_PRIMARY1824852_REPEAT_CONTROLS_V1_20260908','marketId':MID,'researchOnly':True,'runtimeAuthority':False,'comparisons':comparisons,'detail':detail,'verdict':verdict,'branchEquivalents':3,'boundary':['canonical parity excludes only branch mode label','full behavior/terminal/telemetry/numeric/action path compared','no new economic arm/no parameter change']}
    op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out))
if __name__=='__main__':main()
