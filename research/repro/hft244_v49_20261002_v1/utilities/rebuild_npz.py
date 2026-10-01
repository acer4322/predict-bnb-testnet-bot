"""Convert a supplied sanitized archive through frozen feed V1; no native HFT."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True

import numpy as np

from export_hft244_repro_samples import pure_converter


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--market',type=int,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve()
    if a.output.exists(): raise RuntimeError('output already exists')
    ns=pure_converter(root/'strategy/runtime_scratch_CG1AT_2671717/tools/hftbacktest_execution_tape_feed_v1.py',
                      root/'engine/patched/py-hftbacktest/hftbacktest/types.py')
    ns['ARCHIVE_DIR']=root/'fixtures'/str(a.market)
    events,times,meta=ns['build_archive_events'](a.market)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('xb') as f:
        np.savez_compressed(f,data=events,local_times_ms=np.asarray(times,dtype=np.int64))
    print(json.dumps(dict(market_id=a.market,events=len(events),local_wakeups=len(times),
         event_array_sha256=hashlib.sha256(events.tobytes()).hexdigest(),bytes=a.output.stat().st_size,
         native_executed=False)))


if __name__=='__main__': main()
