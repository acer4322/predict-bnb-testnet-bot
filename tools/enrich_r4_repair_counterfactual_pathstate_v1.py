from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get('BTC5M_WORKER_ROOT', r'C:\BTC5M-worker')).resolve()
if not (ROOT / 'tools').is_dir():
    ROOT = Path.cwd().resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_r2_execution_school_v0 as base
from tools.validate_r4_r3_repair_counterfactual_teacher_v1 import run_exact, compact


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest',required=True)
    ap.add_argument('--ids')
    ap.add_argument('--strategy-db',required=True)
    ap.add_argument('--tape-dir',required=True)
    ap.add_argument('--book-db',required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args()

    base.STRATEGY_DB=Path(a.strategy_db).resolve()
    base.tape_v1.ARCHIVE_DIR=Path(a.tape_dir).resolve()
    base.ex.BOOK_DB=Path(a.book_db).resolve()
    man=json.loads(Path(a.manifest).read_text(encoding='utf-8'))
    wanted=None if not a.ids else {int(x) for x in a.ids.split(',') if x.strip()}
    src_rows=[r for r in man.get('rows',[]) if wanted is None or int(r['marketId']) in wanted]
    out_rows=[]
    for src in src_rows:
        mid=int(src['marketId']); at=int(src['decisionMs'])
        row={'marketId':mid,'decisionMs':at,'originalClass':src.get('branchClass'),'originalDelta':src.get('delta'),'exactBranchApplied':False,'pathState':None}
        try:
            rep=run_exact(mid,at); cc=compact(rep); forced=cc.get('forced') or []
            exact=bool(forced and int(forced[0].get('atMs') or -1)==at)
            row['exactBranchApplied']=exact
            if exact:
                row['forcedSide']=forced[0].get('side')
                row['makerNetAtSeam']=forced[0].get('makerNet')
                row['makerAbsAtSeam']=forced[0].get('makerAbs')
                row['makerCoverageAtSeam']=forced[0].get('makerPairedCoverage')
                row['pathState']=forced[0].get('activeOrderPathState')
        except Exception as e:
            row['error']=f'{type(e).__name__}:{e}'
        out_rows.append(row)
        print(json.dumps({'marketId':mid,'exact':row['exactBranchApplied'],'activeOrderCount':((row.get('pathState') or {}).get('summary') or {}).get('activeOrderCount'),'error':row.get('error')},ensure_ascii=False),flush=True)
    rep={'version':'R4_REPAIR_COUNTERFACTUAL_PATHSTATE_ENRICHMENT_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
         'semantics':'Path state captured at exact POST_HFT_FILL_PRE_DECISION_INVENTORY_FEATURES seam before forced Repair episode injection. Original causal class/delta are carried from prior exact teacher and are not recomputed.',
         'marketCount':len(out_rows),'exactCount':sum(bool(r.get('exactBranchApplied')) for r in out_rows),'rows':out_rows,
         'strategyDb':str(base.STRATEGY_DB),'tapeDir':str(base.tape_v1.ARCHIVE_DIR),'bookDb':str(base.ex.BOOK_DB)}
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'marketCount':len(out_rows),'exactCount':rep['exactCount'],'out':str(p)},ensure_ascii=False))

if __name__=='__main__':main()
