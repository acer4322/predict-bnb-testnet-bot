from __future__ import annotations
import json, random, re, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import r2_execution_graduation_exam_steward_v2_1 as steward
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
REPORT=OUT/'r2_unseen20b_20260822_split_v1.json'
SEED=2026082202
pat=re.compile(rb'(?<!\d)(1[45-7]\d{5})(?!\d)')
seen=set()
for root in [ROOT/'data/research']:
    for p in root.rglob('*'):
        if not p.is_file() or p==REPORT: continue
        try:
            # filenames first
            for m in re.findall(r'(?<!\d)(1[45-7]\d{5})(?!\d)',p.name): seen.add(int(m))
            if p.stat().st_size>20_000_000: continue
            b=p.read_bytes()
            for m in pat.findall(b): seen.add(int(m))
        except Exception: pass
con=sqlite3.connect(ROOT/'data/strategy_input_snapshot_archive_v1.db')
rows=con.execute('select market_id,min(sampled_at_ms) first_ms from strategy_input_snapshots_v1 group by market_id order by first_ms,market_id').fetchall(); con.close()
pool=[int(r[0]) for r in rows if int(r[0]) not in seen]
rng=random.Random(SEED); rng.shuffle(pool)
accepted=[]; rejected=[]
for mid in pool:
    try:
        qc=steward.quality_check_without_answer(mid)
        if not bool(qc.get('eligible')): raise RuntimeError(f"{qc.get('stage')} / {(qc.get('input') or {}).get('reasons')}")
        accepted.append(mid); print(json.dumps({'accepted':len(accepted),'marketId':mid}),flush=True)
        if len(accepted)>=20: break
    except Exception as e:
        rejected.append({'marketId':mid,'error':repr(e)})
rep={'version':'R2_UNSEEN20B_20260822_SPLIT_V1','researchOnly':True,'seed':SEED,'selection':'Exact Strategy archive markets absent from all text-like data/research artifacts at selection time; fixed-seed shuffle; formal-grade quality_check_without_answer; no strategy PnL/winner inspected before freeze.','developmentMarkets':accepted,'rejectedCount':len(rejected),'rejectedPreview':rejected[:20]}
REPORT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
