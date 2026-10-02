from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.backfill_r2_fresh_dev_v21_lifecycles_v1 as base

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--split',required=True);ap.add_argument('--report',required=True);a=ap.parse_args()
 src=json.loads((ROOT/a.split).read_text(encoding='utf-8'))
 tmp=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_fresh_adaptation_v1_backfill_union.json'
 tmp.write_text(json.dumps({'developmentMarkets':[int(x) for x in src['backfillMarkets']]},indent=2),encoding='utf-8')
 base.SPLIT=tmp;base.REPORT=ROOT/a.report
 return base.main()
if __name__=='__main__':raise SystemExit(main())
