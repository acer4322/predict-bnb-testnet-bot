"""Frozen nine-BE observation replay. LAN only, no training or policy changes."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_pair_decision_lineage_20260910_v1')
OLD=Path('C:/BTC5M-worker/.tmp/hft244_pair_confirmed_handoff_20260910_v2')
OLD_BUNDLE=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_pair_confirmed_handoff_20260910_v2')
REFERENCE=Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sig(v):return hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False).encode()).hexdigest()


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.bounded('decision-lineage',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'LAN research only'
    start=time.monotonic();out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    result=dict(verdict='PRECHECK',attemptedBE=0,historicalLineageBefore=76,rows=[],comparisons=[],
                freshUsed=0,training=False,promotion=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-start,historicalLineageAfter=76+result['attemptedBE'])
        raw=json.dumps(result,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>1024**2:raise RuntimeError('INSUFFICIENT_TRACE: compact limit')
        (out/'COMPACT.json').write_bytes(raw)
    try:
        assert not ROOT.exists(),'immutable root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        assert sha(REFERENCE)=='064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'
        ref=json.loads(REFERENCE.read_text());oldrows={(r['marketId'],r['arm']):r for r in ref['rows']}
        assert sha(OLD_BUNDLE/'MANIFEST.json')==manifest['frozenBaseManifestSha256']
        old_manifest=json.loads((OLD_BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==ref['nativeSha256']
        result.update(inputHashes=manifest,referenceSha256=sha(REFERENCE),nativeSha256=sha(binary))
        for name,digest in ref['baseSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (OLD/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            assert sha(OLD/rel)==digest,name
            target=ROOT/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/rel,target)
        for name,digest in old_manifest['files'].items():
            if name.startswith('run_hft244_'):continue
            source=OLD/('tapes' if name.endswith('.json.xz') else 'tools')/name
            assert sha(source)==digest,name
            target=ROOT/('tapes' if name.endswith('.json.xz') else 'tools')/name
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        shutil.copy2(BUNDLE/'hft244_pair_decision_lineage_v1.py',ROOT/'tools/hft244_pair_decision_lineage_v1.py')
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_confirmed_handoff_v1 import make_sim
        from tools.hft244_pair_decision_lineage_v1 import make_sim as observe, Recorder
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        install(minimal.v2.base,binary);Sim=observe(make_sim(minimal))
        assert minimal.v2.base.TTL==5000,'observer expiry semantics mismatch'
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        for mid in (1830119,1829115,2023609):
            control=None
            for arm in ('A','C','T'):
                sim=None;result['attemptedBE']+=1;save()
                recorder=Recorder(keep=arm=='C',reference=control.frames if arm=='T' else None)
                try:
                    sim=Sim(ROOT/f'tapes/{mid}.json.xz',arm,recorder)
                    output=sim.run_minimal('UP')
                    sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                    assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                    for key,o in sim.orders.items():
                        if sim.snap(o)['status'] not in minimal.v2.TERMINAL_STATUSES:assert key in sim.slot_key.values()
                    receipts=list(sim._receipt_ledger.seen.values())
                    core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                    old=oldrows[mid,arm]
                    assert sig(core)==old['signature'],('full behavior parity',mid,arm)
                    assert sim._probe_mark==old['mark'] and sim._probe_ready==old['ready'],'mark/readiness parity'
                    assert sim._probe_risk==old['risk'],'exposure parity'
                    assert sim._probe_events==old['events'],'protocol parity'
                    anatomy=receipt_anatomy(receipts,old)
                    assert anatomy==old['anatomy'],'FIFO parity'
                    stats=recorder.summary()
                    if arm=='T':
                        actual=[r for r in receipts if r['order_id']==sim.orders[sim._probe_key]['n']]
                        assert actual==old['directReceipts'] and any(r['maker']==0 for r in actual),'direct exercise parity'
                        result['comparisons'].append(dict(marketId=mid,**stats))
                    result['rows'].append(dict(marketId=mid,arm=arm,signature=sig(core),correctness=True,
                        UP=old['UP'],DOWN=old['DOWN'],fills=old['fills'],alternations=old['alternations'],
                        traceClocks=stats['clocks'],traceHash=stats['rollingHash'],
                        finalReceiptsOutsideDecision=len(sim._probe_trace_pending_receipts),
                        finalCancelEventsOutsideDecision=len(sim._probe_trace_pending_cancels),
                        finalExpiryEventsOutsideDecision=len(sim._probe_trace_expiry)))
                    if arm=='C':control=recorder
                    save()
                except Exception:
                    if sim is not None:result['lastPythonOnly']=dict(marketId=mid,arm=arm,
                        mark=sim._probe_mark,stage=sim._probe_stage,traceClocks=recorder.count)
                    raise
                finally:
                    if sim is not None:sim.close()
            control=None
        result['verdict']='INERT_DECISION_LINEAGE_CAPTURE_SUPPORTED'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=8))
    save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','comparisons','inputHashes','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)


if __name__=='__main__':main()
