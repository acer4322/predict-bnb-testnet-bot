"""Frozen A/F full-lifecycle boundary contrast; LAN-only, bounded6BE."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/pair_core_full_horizon3_20260910_v1')
OLD=Path('C:/BTC5M-worker/.tmp/hft244_pair_confirmed_handoff_20260910_v2')
OLD_BUNDLE=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_pair_confirmed_handoff_20260910_v2')
REF=Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
IDS=[2022527,2026429,2029246]
BASELINE=Path('C:/BTC5M-worker/.lan_worker_v1/results/pair-core-target-geometry10-20260910-v2/COMPACT.json')
TAPES=Path('C:/BTC5M-worker/.tmp/pair_core_target_geometry10_20260910_v2/tapes')


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
        mod.bounded('full-horizon3',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'LAN only'
    started=time.monotonic();out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    result=dict(verdict='PRECHECK',rows=[],attemptedBE=0,historicalLineageBefore=104,training=False,freshUsed=0,
                policy='B_PAIR_ONLY_MAX4_UNCHANGED',targetRuntimeInputs=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-started,historicalLineageAfter=104+result['attemptedBE'])
        raw=json.dumps(result,separators=(',',':'),allow_nan=False).encode();assert len(raw)<4*1024**2
        (out/'COMPACT.json').write_bytes(raw)
    try:
        assert not ROOT.exists(),'immutable root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        assert sha(REF)=='064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'
        ref=json.loads(REF.read_text())
        assert sha(BASELINE)=='c1dd0b211dac4dfdd95848b2ebcdff91fb2d64ccfe7bbd3205965cb923bc019d'
        baseline=json.loads(BASELINE.read_text()); baseline_rows={r['marketId']:r for r in baseline['rows']}
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
            name=f'{mid}.json.xz';assert sha(TAPES/name)==baseline['inputManifest']['files'][name]
            dest=ROOT/'tapes'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(TAPES/name,dest)
        shutil.copy2(BUNDLE/'pair_core_full_horizon_v1.py',ROOT/'tools/pair_core_full_horizon_v1.py')
        result.update(inputManifest=manifest,nativeSha256=sha(binary),baseSourceHashes=ref['baseSourceHashes'])
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        from tools.pair_core_full_horizon_v1 import make_sim
        install(minimal.v2.base,binary)
        Horizon=make_sim(minimal)
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        for mid,label,full in [(m,l,f) for m in IDS for l,f in (('A',False),('F',True))]:
            sim=None;result['attemptedBE']+=1;save()
            try:
                sim=Horizon(ROOT/f'tapes/{mid}.json.xz',full)
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
                if label=='A':assert row==baseline_rows[mid], 'full baseline row parity'
                row.update(arm=label,boundary=sim._probe_boundary,risk=sim._probe_risk,lateAdmissions=sim._probe_late_admissions)
                end=int(sim.payload['market']['window_end_ms'])
                row['pendingAtTapeEnd']=[dict(key=k,side=sim.orders[k]['side'],qty=sim._remaining(k),status=sim.snap(sim.orders[k])['status']) for k in sim.slot_key.values()]
                row['postSettlementReceipts']=[r for r in receipts if r['exchange_ts']>=end*1000000]
                if label=='F':
                    a=result['rows'][-1];assert a['marketId']==mid and a['arm']=='A'
                    assert row['boundary']==a['boundary'],'boundary full prefix parity'
                    if row['boundary'] is not None:
                        bound=row['boundary']['t']*1000000
                        assert [r for r in receipts if r['receive_ts']<=bound]==[r for r in a['receipts'] if r['receive_ts']<=bound]
                result['rows'].append(row)
                if row['postSettlementReceipts']:
                    raise RuntimeError('SETTLEMENT_BOUNDARY_STOP: native receipt after scheduled closure')
                if len(result['rows'])==6:result['smoke3Correctness']='PASS'
                save();print(json.dumps(dict(marketId=mid,arm=label,fills=sim.fills,UP=row['UP'],DOWN=row['DOWN'])),flush=True)
            finally:
                if sim is not None:sim.close()
        result['verdict']='FULL_HORIZON_SMOKE3_CAPTURE_SUPPORTED'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=8))
    save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','inputManifest','baseSourceHashes','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)


if __name__=='__main__':main()
