"""Unchanged Minimal Pair cross-market baseline; LAN-only, bounded 10BE."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/pair_core_target_geometry10_20260910_v2')
OLD=Path('C:/BTC5M-worker/.tmp/hft244_pair_confirmed_handoff_20260910_v2')
OLD_BUNDLE=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_pair_confirmed_handoff_20260910_v2')
REF=Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
IDS=[2022527,2026429,2029246,2023478,2024133,2026085,2026817,2027678,2028352,2028561]


def sha(p):
    assert p.stat().st_size<50*1024**2, 'hash input exceeds bounded artifact limit'
    digest=hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):digest.update(chunk)
    return digest.hexdigest()


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.bounded('target-geometry10',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'LAN only'
    started=time.monotonic();out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    result=dict(verdict='PRECHECK',rows=[],attemptedBE=0,historicalLineageBefore=91,training=False,freshUsed=0,
                policy='B_PAIR_ONLY_MAX4_UNCHANGED',targetRuntimeInputs=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-started,historicalLineageAfter=91+result['attemptedBE'])
        raw=json.dumps(result,separators=(',',':'),allow_nan=False).encode();assert len(raw)<4*1024**2
        (out/'COMPACT.json').write_bytes(raw)
    try:
        assert not ROOT.exists(),'immutable root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        assert sha(REF)=='064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'
        ref=json.loads(REF.read_text())
        assert sha(OLD_BUNDLE/'MANIFEST.json')=='ce4c35ccc41f6ae4c370db1aae8372449dca7c2dfa301f0a1b64f2ce721eaee7'
        original=json.loads((OLD_BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==ref['nativeSha256']
        for name,digest in ref['baseSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (OLD/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            assert sha(OLD/rel)==digest,name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/rel,dest)
        for name,digest in original['files'].items():
            if name.startswith('run_hft244_') or name.endswith('.json.xz'):continue
            source=OLD/'tools'/name;assert sha(source)==digest,name
            dest=ROOT/'tools'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        for mid in IDS:
            name=f'{mid}.json.xz';assert name in manifest['files']
            dest=ROOT/'tapes'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BUNDLE/name,dest)
        result.update(inputManifest=manifest,nativeSha256=sha(binary),baseSourceHashes=ref['baseSourceHashes'])
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        install(minimal.v2.base,binary)
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        for mid in IDS:
            sim=None;result['attemptedBE']+=1;save()
            try:
                sim=minimal.MinimalPairRoleSim(ROOT/f'tapes/{mid}.json.xz',4,False)
                output=sim.run_minimal('UP')
                sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                for k,o in sim.orders.items():
                    if sim.snap(o)['status'] not in minimal.v2.TERMINAL_STATUSES:assert k in sim.slot_key.values()
                receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=3000,'receipt cap'
                row=dict(marketId=mid,correctness=True,UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,
                    cost=sim.cost,inventory=dict(sim.inv),fills=sim.fills,submits=sim.submits,
                    alternations=output['fillSideAlternations'],roleFills=output['roleFills'],
                    maxPhysicalSlots=sim.max_simultaneous_slots,native=sim._receipt_ledger.native,receipts=receipts,
                    pairBlocks=output['minimalPairBlocks'],veto=output['vetoCounts'])
                anatomy=receipt_anatomy(receipts,row)
                row['anatomy']={k:v for k,v in anatomy.items() if k not in ('pairs','residual')}
                result['rows'].append(row)
                if len(result['rows'])==3:result['smoke3Correctness']='PASS'
                save();print(json.dumps(dict(marketId=mid,fills=sim.fills,UP=row['UP'],DOWN=row['DOWN'])),flush=True)
            finally:
                if sim is not None:sim.close()
        result['verdict']='MATCHED_BASELINE10_CAPTURED_NOT_POLICY_PROMOTION'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=8))
    save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','inputManifest','baseSourceHashes','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)


if __name__=='__main__':main()
