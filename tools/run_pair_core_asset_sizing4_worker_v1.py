"""LAN-only consumed4 sizing correctness contrast, 8BE maximum, no Active."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

BUNDLE=Path(__file__).resolve().parent
ROOT=Path('C:/BTC5M-worker/.tmp/pair_core_asset_sizing4_20260910_v1')
OLD=Path('C:/BTC5M-worker/.tmp/hft244_pair_confirmed_handoff_20260910_v2')
OLD_BUNDLE=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_pair_confirmed_handoff_20260910_v2')
REF=Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json')
BASELINE=Path('C:/BTC5M-worker/.lan_worker_v1/results/pair-core-target-geometry10-20260910-v2/COMPACT.json')
TAPES=Path('C:/BTC5M-worker/.tmp/pair_core_target_geometry10_20260910_v2/tapes')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
COHORT=[(1830119,'ETH'),(2022527,'BTC'),(2026429,'BTC'),(2029246,'BTC')]
NEW=['pair_core_asset_route_sizing_v1.py','pair_core_sizing_observer_v1.py','pair_core_research_scope_preflight_v1.py']


def sha(path):
    assert path.stat().st_size<50*1024**2,'oversized bounded input'
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.bounded('asset-sizing4',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'LAN only'
    assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4,'explicit thread cap required'
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);started=time.monotonic()
    result=dict(verdict='PRECHECK',rows=[],attemptedBE=0,historicalLineageBefore=110,
                targetRuntimeInputs=False,freshUsed=0,training=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-started,historicalLineageAfter=110+result['attemptedBE'])
        raw=json.dumps(result,separators=(',',':'),allow_nan=False).encode();assert len(raw)<4*1024**2
        (out/'COMPACT.json').write_bytes(raw)
    try:
        assert not ROOT.exists(),'immutable root already exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,want in manifest['files'].items():assert sha(BUNDLE/name)==want,name
        assert sha(REF)=='064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'
        assert sha(BASELINE)=='c1dd0b211dac4dfdd95848b2ebcdff91fb2d64ccfe7bbd3205965cb923bc019d'
        assert sha(OLD_BUNDLE/'MANIFEST.json')=='ce4c35ccc41f6ae4c370db1aae8372449dca7c2dfa301f0a1b64f2ce721eaee7'
        ref=json.loads(REF.read_text());baseline=json.loads(BASELINE.read_text())
        original=json.loads((OLD_BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==ref['nativeSha256']
        frozen={};paths={}
        for name,want in ref['baseSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (OLD/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            assert sha(OLD/rel)==want,name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/rel,dest)
            frozen[rel.as_posix()]=want;paths[name]=rel
        for name,want in original['files'].items():
            if name.startswith('run_hft244_') or name.endswith('.json.xz'):continue
            source=OLD/'tools'/name;assert sha(source)==want,name
            dest=ROOT/'tools'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
            frozen['tools/'+name]=want
        for name in NEW:
            shutil.copy2(BUNDLE/name,ROOT/'tools'/name);frozen['tools/'+name]=manifest['files'][name]
        inputs={}
        for mid,asset in COHORT:
            name=f'{mid}.json.xz';source=(OLD/'tapes' if asset=='ETH' else TAPES)/name
            want=(original if asset=='ETH' else baseline['inputManifest'])['files'][name]
            assert sha(source)==want,name
            dest=ROOT/'tapes'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
            inputs[str(mid)]=dict(asset=asset,sha256=want,source=str(source),consumed=True)
        result.update(inputManifest=manifest,cohort=inputs,nativeSha256=sha(binary),frozenSources=frozen)
        sys.path.insert(0,str(BACKEND));sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools.pair_core_research_scope_preflight_v1 import validate,PROFILE_DEVIATIONS,RUNTIME_CHECKS
        declaration=dict(profile='S_ASSET_SIZING_PASSIVE',acknowledged_deviations=sorted(PROFILE_DEVIATIONS['S_ASSET_SIZING_PASSIVE']),
            claims=['MECHANISM_ONLY'],native_sha256=sha(binary),required_runtime_checks=sorted(RUNTIME_CHECKS),
            frozen_sources=frozen,target_runtime_inputs=False,preserve_original_artifacts=True,
            asset_route_sizing={'ETH':{'PASSIVE':[1,12]},'BTC':{'PASSIVE':[1,18]}},
            scope_audit='PAIR_CORE_ASSET_SIZING_SMOKE4_PREREG_20260910.md')
        assert not validate(declaration,ROOT),validate(declaration,ROOT)
        result['scopeDeclaration']=declaration
        import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.pair_core_asset_route_sizing_v1 import make_passive_sim
        from tools.pair_core_sizing_observer_v1 import make_observed,summarize
        install(minimal.v2.base,binary)
        loaded={}
        for name,rel in paths.items():
            mod=sys.modules[name];p=Path(mod.__file__).resolve()
            assert p==(ROOT/rel).resolve(),name
            assert sha(p)==ref['baseSourceHashes'][name],name
            loaded[name]=dict(path=str(p),sha256=sha(p))
        for file in NEW:
            name='tools.'+Path(file).stem;p=Path(sys.modules[name].__file__).resolve()
            assert p==(ROOT/'tools'/file).resolve() and sha(p)==manifest['files'][file]
            loaded[name]=dict(path=str(p),sha256=sha(p))
        result['loadedSources']=loaded;result['nativePath']=str(binary)
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        for mid,asset in COHORT:
            for arm in ('C12','S'):
                Parent=minimal.MinimalPairRoleSim if arm=='C12' else make_passive_sim(minimal,asset)
                Observed=make_observed(Parent,minimal)
                sim=None;result['attemptedBE']+=1;save()
                try:
                    sim=Observed(ROOT/f'tapes/{mid}.json.xz');output=sim.run_minimal('UP')
                    sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                    assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                    receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=3000
                    for key,order in sim.orders.items():
                        if sim.snap(order)['status'] not in minimal.v2.TERMINAL_STATUSES:assert key in sim.slot_key.values()
                    row=dict(marketId=mid,asset=asset,arm=arm,UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,
                        cost=sim.cost,inventory=dict(sim.inv),fills=sim.fills,submits=sim.submits,
                        alternations=output['fillSideAlternations'],roleFills=output['roleFills'],
                        receipts=receipts,native=sim._receipt_ledger.native,veto=output['vetoCounts'],
                        mro=[c.__module__+'.'+c.__qualname__ for c in Observed.__mro__],observer=summarize(sim))
                    row['pending']=[dict(key=k,status=sim.snap(sim.orders[k])['status'],qty=sim._remaining(k)) for k in sim.slot_key.values()]
                    end=sim.payload['market']['window_end_ms']
                    assert not [r for r in receipts if r['exchange_ts']>=end*1000000],'post-settlement receipt'
                    if arm=='C12':
                        known=next(r for r in (ref if asset=='ETH' else baseline)['rows'] if r['marketId']==mid and r.get('arm','A')=='A')
                        for field in ('UP','DOWN','cost','fills','submits','alternations','roleFills','receipts','native'):
                            assert row[field]==known[field],f'baseline parity {mid}:{field}'
                        row['historicalParity']='PASS'
                    else:
                        control=result['rows'][-1];assert control['marketId']==mid
                        if asset=='ETH':
                            assert row['observer']==control['observer'],'ETH full behavior/trace parity'
                            assert row['receipts']==control['receipts'] and row['pending']==control['pending']
                            row['ethParity']='PASS'
                    raw=json.dumps(sim._sz_frames,separators=(',',':'),allow_nan=False).encode()
                    assert len(raw)<32*1024**2
                    target=out/f'TRACE_{mid}_{arm}.json.gz';target.write_bytes(gzip.compress(raw,mtime=0))
                    row['trace']=dict(file=target.name,sha256=sha(target),bytes=len(raw))
                    result['rows'].append(row);save()
                    print(json.dumps(dict(marketId=mid,arm=arm,fills=row['fills'],UP=row['UP'],DOWN=row['DOWN'])),flush=True)
                finally:
                    if sim is not None:sim.close()
        result['verdict']='ASSET_SIZING_CORRECTNESS_CAPTURE_SUPPORTED'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=8))
    save();print(json.dumps(dict(verdict=result['verdict'],attemptedBE=result['attemptedBE'],error=result.get('error'))),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)


if __name__=='__main__':main()
