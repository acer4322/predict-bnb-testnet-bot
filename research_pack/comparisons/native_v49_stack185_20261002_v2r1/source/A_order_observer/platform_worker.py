"""One sequential A job, immutable cloud lab source and pinned V49 backend."""
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS'):
    os.environ[key] = '1'
import argparse
import hashlib
import importlib.metadata
import json
import platform
import runpy
import sys
import time
from pathlib import Path

P = Path(__file__).resolve().parent
OUT = Path(os.environ.get('BTC5M_LAN_RESULT_DIR', str(P/'not-dispatched')))
FX = Path(r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_platform185_20261002_v1\fixtures')

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')

def verify():
    assert platform.node().upper() == 'DESKTOP-JIERAGF'
    manifest = json.loads((P/'MANIFEST.json').read_text())
    for name, expected in manifest['files'].items():
        assert sha(P/name) == expected, name
    assert manifest['markets'] == 185 and manifest['parallel_paths'] == 1 and manifest['max_threads'] == 4
    candidate = json.loads((P/'CANDIDATE.json').read_text())
    backend = Path(candidate['backend'])
    binary = backend/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    assert sha(binary) == candidate['sha256']
    expected = json.loads((P/'EXPECTED_BACKEND.json').read_text())
    for row in expected['python']:
        assert sha(backend/row['path']) == row['sha256'], row['path']
    sys.path.insert(0, str(backend))
    import hftbacktest
    assert Path(hftbacktest.__file__).resolve() == (backend/'hftbacktest/__init__.py').resolve()
    # Import only. No backtest, order or fit in check-only mode.
    sys.path.insert(0, str(P/'repro/strategy/runtime_scratch_CG1AT_2671717'))
    import tools.hftbacktest_execution_shift_audit_v0 as ex
    assert ex.hbt is hftbacktest
    labels = json.loads((P/'LABELS.json').read_text())['records']
    assert len(labels) == len({r['market_id'] for r in labels}) == 185
    import numpy as np
    for r in labels:
        assert r['winner'] in ('UP','DOWN')
        fx = FX/str(r['market_id'])
        with np.load(fx/'events.npz') as z:
            assert len(z['data']) > 0 and z['data'].dtype == hftbacktest.event_dtype
        assert isinstance(json.loads((fx/'FIXTURE.json').read_text())['conversion_info']['firstReceivedMs'], int)
    return {'host':platform.node(), 'python':sys.version, 'engine_version':hftbacktest.__version__, 'native_sha256':sha(binary), 'wrapper_hashes_verified':len(expected['python']), 'versions':{n:importlib.metadata.version(n) for n in ('numpy','numba','llvmlite')}, 'lab_sha256':sha(P/'strategy_lab.py'), 'manifest_sha256':sha(P/'MANIFEST.json'), 'markets':185, 'strategies':['FAV_TAKER','UNDER_TAKER'], 'entry_latency_ms':250, 'response_latency_ms':250, 'queue_model':'risk', 'fees':0, 'tick_size':.01, 'lot_size':.01, 'parallel_paths':1, 'max_threads':4}

def main(check):
    readback = verify()
    if check:
        print(json.dumps({'status':'LOAD_ONLY_PASS', 'native_executed':0, **readback})); return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and not (OUT/'local.json').exists()
    save(OUT/'ENGINE_READBACK.json', readback)
    began = time.monotonic()
    sys.argv = [str(P/'strategy_lab.py'), str(P/'repro'), str(FX), str(P/'LABELS.json'), '--only','FAV_TAKER,UNDER_TAKER','--out',str(OUT/'local.json')]
    # Observe completed Python run() returns; never replace a strategy or actuator.
    # This also retains complete market rows if the unchanged final statistics fail.
    completed=[]; order_summaries=[]
    def progress(frame, event, arg):
        if event=='return' and frame.f_code.co_name=='run' and frame.f_code.co_filename==str(P/'strategy_lab.py') and isinstance(arg,dict) and 'pnl' in arg:
            completed.append({'strategy':frame.f_locals['strat'], **arg})
            mid=arg['market']; strategy=frame.f_locals['strat']
            meta=json.loads((P/'META'/f'{mid}.json').read_text())
            os=[{'order_sequence':o['n'],'place_ms':o['t'],'official_window_seconds':(o['t']-meta['window_start_ms'])/1000,'lab_window_seconds':(o['t']-frame.f_locals['start'])/1000,'side':o['side'],'qty':o['qty']} for o in frame.f_locals['orders'].values()]
            order_summaries.append({'market_id':mid,'strategy':strategy,'orders':os,'order_count':len(os),'average_official_window_seconds':sum(o['official_window_seconds'] for o in os)/len(os) if os else None,'sum_order_seconds':sum(o['official_window_seconds'] for o in os),'observer':'sys.setprofile run return-frame read-only; original lab bytecode unchanged'})
            if len(completed)%5==0 or len(completed)==370:
                save(OUT/'PARTIAL.json', {'completed':len(completed),'rows':completed})
                print(json.dumps({'completed':len(completed),'expected':370,'market':arg['market'],'strategy':frame.f_locals['strat']}),flush=True)
    sys.setprofile(progress)
    try:
        runpy.run_path(str(P/'strategy_lab.py'), run_name='__main__')
    finally:
        sys.setprofile(None)
    save(OUT/'A_ORDER_SUMMARIES.json', {'rows':order_summaries,'observer':'return-frame only; no advance, submit, cancel or strategy replacement in observer'})
    result = json.loads((OUT/'local.json').read_text())
    expected = {r['market_id'] for r in json.loads((P/'LABELS.json').read_text())['records']}
    assert set(result) == {'FAV_TAKER','UNDER_TAKER'}
    for strategy, rows in result.items():
        assert len(rows) == 185 and {r['market'] for r in rows} == expected
        assert all(r['inferred'] is False for r in rows)
        assert all(__import__('math').isfinite(float(r[n])) for r in rows for n in ('pnl','cost','up','dn'))
    save(OUT/'RESULT.json', {'status':'COMPLETE_PLATFORM_ORDER_AUDIT185', 'job_id':os.environ['BTC5M_LAN_WORKER_JOB_ID'], 'markets':185, 'strategy_market_rows':370, 'elapsed_seconds':time.monotonic()-began, 'local_sha256':sha(OUT/'local.json'), 'cloud_comparison':'PENDING_REQUIRED_CLOUD_RESULT_FILE', 'model_fits':0, 'live_changes':0})
    print(json.dumps({'status':'COMPLETE_PLATFORM_ORDER_AUDIT185', 'rows':370}), flush=True)

if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--check-only',action='store_true')
    main(ap.parse_args().check_only)
