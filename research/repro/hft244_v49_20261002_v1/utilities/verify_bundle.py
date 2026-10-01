"""Check source/file integrity and rebuild existing feed inputs, without HFT."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True

import numpy as np

from export_hft244_repro_samples import pure_converter


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    a=p.parse_args();root=a.root.resolve()
    manifest=json.loads((root/'SHA256SUMS.json').read_text())
    bad=[name for name,h in manifest['files'].items() if not (root/name).is_file() or digest(root/name)!=h]
    if bad: raise RuntimeError('file hash mismatch: '+repr(bad))
    src=root/'strategy/runtime_scratch_CG1AT_2671717/tools/hftbacktest_execution_tape_feed_v1.py'
    types=root/'engine/patched/py-hftbacktest/hftbacktest/types.py'
    ns=pure_converter(src,types)
    reports=[]
    for mid in (2671717,2671719,2671768):
        dest=root/'fixtures'/str(mid)
        ns['ARCHIVE_DIR']=dest
        events,times,info=ns['build_archive_events'](mid)
        meta=json.loads((dest/'FIXTURE.json').read_text())
        with np.load(dest/'events.npz',allow_pickle=False) as saved:
            assert events.dtype==saved['data'].dtype and events.shape==saved['data'].shape
            assert events.tobytes()==saved['data'].tobytes(),mid
            assert np.asarray(times,dtype=np.int64).tobytes()==saved['local_times_ms'].tobytes(),mid
        assert hashlib.sha256(events.tobytes()).hexdigest()==meta['event_array_sha256'],mid
        golden=[]
        for arm in ('CG1AT','FULL'):
            gold=dest/'golden'/arm
            clock=digest(gold/'execution_clock.json')
            assert clock==next(x['original_execution_clock_sha256'] for x in meta['golden'] if x['arm']==arm)
            audit=json.loads((gold/'AUDIT.json').read_text())
            golden.append(dict(arm=arm,failed_checks=audit.get('failed_checks',[])))
        reports.append(dict(market_id=mid,events=len(events),local_wakeups=len(times),feed_npz_parity='PASS',golden=golden))
    print(json.dumps(dict(status='PASS',files_verified=len(manifest['files']),hash_mismatches=0,
                          markets=reports,native_executed=False,model_fits=0),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
