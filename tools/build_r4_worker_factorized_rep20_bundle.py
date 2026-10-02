from __future__ import annotations
import json,sqlite3,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_simulator_factorized_execution_replication_v1_preregistered.json'
SRCDB=ROOT/'data/public_source_snapshot_archive_v2.db';TMP=ROOT/'.tmp/r4_worker_factorized_rep20_bundle';OUT=ROOT/'.tmp/r4_worker_factorized_rep20_bundle.zip'
ids=[int(x) for x in json.loads(PRE.read_text(encoding='utf-8'))['replicationCohort']]
if TMP.exists():shutil.rmtree(TMP)
for q in ['data/hft_forward_paper_v1/markets','data/execution_tape_v1/markets','data/research/r4_v0/p0_provenance_v1']:(TMP/q).mkdir(parents=True,exist_ok=True)
shutil.copy2(PRE,TMP/'data/research/r4_v0/p0_provenance_v1'/PRE.name)
missing=[]
for mid in ids:
 a=ROOT/f'data/hft_forward_paper_v1/markets/{mid}_r2_hft_closed_loop_v1.json.xz';b=ROOT/f'data/execution_tape_v1/markets/{mid}.json.xz'
 if a.exists():shutil.copy2(a,TMP/'data/hft_forward_paper_v1/markets'/a.name)
 else:missing.append(str(a))
 if b.exists():shutil.copy2(b,TMP/'data/execution_tape_v1/markets'/b.name)
 else:missing.append(str(b))
sub=TMP/'data/public_source_snapshot_archive_v2.db';s=sqlite3.connect(f'file:{SRCDB}?mode=ro',uri=True);d=sqlite3.connect(sub)
try:
 schema=s.execute("select sql from sqlite_master where type='table' and name='public_source_snapshots_v2'").fetchone()[0];d.execute(schema);cols=[r[1] for r in s.execute('pragma table_info(public_source_snapshots_v2)')];qs=','.join('?'*len(ids));rows=s.execute(f"select {','.join(cols)} from public_source_snapshots_v2 where market_id in ({qs}) order by market_id,sampled_at_ms",ids).fetchall();d.executemany(f"insert into public_source_snapshots_v2 ({','.join(cols)}) values ({','.join('?'*len(cols))})",rows);d.commit()
finally:s.close();d.close()
if OUT.exists():OUT.unlink()
with zipfile.ZipFile(OUT,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in TMP.rglob('*'):
  if p.is_file():z.write(p,p.relative_to(TMP))
print(json.dumps({'bundle':str(OUT.relative_to(ROOT)),'bytes':OUT.stat().st_size,'markets':len(ids),'publicRows':len(rows),'missing':missing}))
