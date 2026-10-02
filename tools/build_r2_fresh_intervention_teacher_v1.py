from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_r2_execution_school_teacher_v0 import (
    TARGET_DB, BOOK_DB, high_conf_lifecycles, target_fills,
    target_context, target_inventory_before, classify,
)

OUT = ROOT / 'data' / 'research' / 'execution_aware_fill_lifecycle_v0'
SPLIT = OUT / 'r2_pending_management_fresh_split_v1.json'
REPORT = OUT / 'r2_fresh_intervention_teacher_v1.json'
ROWS = OUT / 'r2_fresh_intervention_teacher_v1_rows.csv'


def main() -> int:
    split=json.loads(SPLIT.read_text(encoding='utf-8'))
    ids=[int(x) for x in split['developmentMarkets']]
    tc=sqlite3.connect(TARGET_DB); tc.row_factory=sqlite3.Row
    bc=sqlite3.connect(BOOK_DB); bc.row_factory=sqlite3.Row
    rows: list[dict[str,Any]]=[]; coverage=[]
    try:
        for mid in ids:
            p=OUT/f'r2_fresh_baseline_market{mid}_v1.json'
            if not p.exists():
                continue
            rep=json.loads(p.read_text(encoding='utf-8'))
            tf=target_fills(tc,mid); lc=high_conf_lifecycles(bc,mid)
            if not tf or not lc:
                continue
            n0=len(rows)
            for src in rep.get('orderStateRows') or []:
                context=str(src.get('context') or '')
                if not context.startswith('BEFORE_ADD:'):
                    continue
                parts=context.split(':'); action_side=parts[1] if len(parts)>1 else None
                side=str(src.get('side') or '').upper()
                if action_side!=side:
                    continue
                row=dict(src); cp=int(row['checkpointMs'])
                row.update(target_context(lc,cp,side))
                ti=target_inventory_before(tf,cp)
                row.update({f'teacherTarget{k[0].upper()}{k[1:]}':v for k,v in ti.items()})
                label,reason=classify(row)
                row['teacherExecutionLabel']=label; row['teacherReason']=reason; row['teacherTargetDataRuntimeAllowed']=False
                rows.append(row)
            coverage.append({'marketId':mid,'rows':len(rows)-n0,'targetFills':len(tf),'lifecycles':len(lc)})
    finally:
        tc.close(); bc.close()
    rows.sort(key=lambda r:(int(r['marketId']),int(r['checkpointMs']),str(r['orderId'])))
    counts=Counter(str(r['teacherExecutionLabel']) for r in rows)
    payload={'version':'R2_FRESH_INTERVENTION_TEACHER_V1','researchOnly':True,'liveTradingChanges':False,'studentScale':'PRE_CAP100_ORIGINAL_R2','developmentOnly':True,'markets':len({int(r['marketId']) for r in rows}),'rows':len(rows),'labelCounts':dict(counts),'coverage':coverage,'guards':['Only fresh development markets are opened. Sealed holdout remains untouched.','Rows are same-side BEFORE_ADD intervention checkpoints only.','HFT future fill and Target lifecycle are post-hoc labels only, never runtime features.']}
    REPORT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    if rows:
        fields=sorted({k for r in rows for k in r if k!='portfolio'})+['portfolio_json']
        with ROWS.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader()
            for r in rows:
                z={k:v for k,v in r.items() if k!='portfolio'}; z['portfolio_json']=json.dumps(r.get('portfolio') or {},ensure_ascii=False,separators=(',',':'),allow_nan=True); w.writerow(z)
    print(json.dumps({'ok':True,'report':str(REPORT),'rowsCsv':str(ROWS),'markets':payload['markets'],'rows':len(rows),'labelCounts':dict(counts)},ensure_ascii=False))
    return 0

if __name__=='__main__': raise SystemExit(main())
