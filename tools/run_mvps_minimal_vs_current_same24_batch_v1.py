from __future__ import annotations
import argparse, importlib.util, sys
from pathlib import Path
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_mvps_minimal_vs_current_same24_smoke4_v1.py'
SRC=STAGED if STAGED.exists() else Path(__file__).resolve().parent/'run_mvps_minimal_vs_current_same24_smoke4_v1.py'
sp=importlib.util.spec_from_file_location('mvps_same24_base',SRC);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
FULL=[1945866,1945869,1945898,1945986,1946036,1946298,1946317,1946448,1946468,1946475,1946488,1946640,1946653,1946656,1946668,1946683,1946748,1946756,1946760,1946784,1946792,1946872,1946876,1946899]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if not mids or any(x not in FULL for x in mids):raise ValueError(f'invalid market IDs {mids}')
    m.MIDS=mids
    old=sys.argv[:]
    try:
        sys.argv=[str(SRC),'--bundle',a.bundle,'--output',a.output]
        m.main()
    finally:sys.argv=old
if __name__=='__main__':main()
