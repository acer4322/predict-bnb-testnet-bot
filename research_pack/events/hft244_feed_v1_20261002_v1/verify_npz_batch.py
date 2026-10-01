"""Read-only numeric NPZ/file integrity verification; no native HFT imports."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
import numpy as np


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    a=p.parse_args();root=a.root.resolve();index=json.loads((root/'INDEX.json').read_text())
    if (root/'SHA256SUMS.json').exists():
        manifest=json.loads((root/'SHA256SUMS.json').read_text())['files']
        for name,h in manifest.items():assert sha(root/name)==h,name
    total=0;trades=0
    for row in index['records']:
        folder=root/'markets'/str(row['market_id']);meta=json.loads((folder/'META.json').read_text())
        path=folder/'events.npz';assert sha(path)==row['npz_sha256']==meta['npz_sha256']
        with np.load(path,allow_pickle=False) as data:
            assert set(data.files)=={'data','local_times_ms'}
            ev=data['data'];times=data['local_times_ms']
            assert len(ev)==row['events'] and ev.dtype.itemsize==64
            assert ev.dtype.names==('ev','exch_ts','local_ts','px','qty','order_id','ival','fval')
            assert ev.dtype.hasobject is False and times.dtype==np.dtype('int64')
            assert hashlib.sha256(ev.tobytes()).hexdigest()==meta['event_array_sha256']
            assert hashlib.sha256(times.tobytes()).hexdigest()==meta['local_wakeup_array_sha256']
            assert np.all(ev['order_id']==0) and np.all(ev['ival']==0) and np.all(ev['fval']==0)
            assert np.all(np.diff(ev['local_ts'])>=0) and np.all(np.diff(times)>0)
            assert np.all(ev['exch_ts']==ev['local_ts'])
            assert np.isfinite(ev['px']).all() and np.isfinite(ev['qty']).all()
            assert np.all(ev['qty']>=0)
            assert int(np.count_nonzero((ev['ev'] & np.uint64(0xff))==np.uint64(2)))==row['public_trades']
            total+=len(ev);trades+=row['public_trades']
        assert meta['anonymous_sort_parity']==meta['npz_readback_parity']=='PASS'
    assert not index['failed'] and len(index['records'])==index['markets']
    print(json.dumps(dict(status='PASS',markets=len(index['records']),events=total,public_trades=trades,
         private_identifiers='numeric-only arrays, zero order_id/ival/fval; metadata allowlist',native_executed=False)))


if __name__=='__main__':main()
