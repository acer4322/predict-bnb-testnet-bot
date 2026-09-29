from __future__ import annotations
import argparse,json,sqlite3,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();ids_path=ROOT/a.ids_json;ids=json.loads(ids_path.read_text(encoding='utf-8'));srcdb=ROOT/'data/public_source_snapshot_archive_v2.db';tmp=ROOT/'.tmp/portfolio_event_ids_bundle';out=ROOT/a.out
 if tmp.exists():shutil.rmtree(tmp)
 (tmp/'data/hft_forward_paper_v1/markets').mkdir(parents=True,exist_ok=True);(tmp/'data/execution_tape_v1/markets').mkdir(parents=True,exist_ok=True);dest_ids=tmp/a.ids_json;dest_ids.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ids_path,dest_ids);missing=[]
 for mid in ids:
  for src,dd in [(ROOT/f'data/hft_forward_paper_v1/markets/{mid}_r2_hft_closed_loop_v1.json.xz',tmp/'data/hft_forward_paper_v1/markets'),(ROOT/f'data/execution_tape_v1/markets/{mid}.json.xz',tmp/'data/execution_tape_v1/markets')]:
   if src.exists():shutil.copy2(src,dd/src.name)
   else:missing.append(str(src))
 sub=tmp/'data/public_source_snapshot_archive_v2.db';s=sqlite3.connect(f'file:{srcdb}?mode=ro',uri=True);d=sqlite3.connect(sub)
 try:
  schema=s.execute("select sql from sqlite_master where type='table' and name='public_source_snapshots_v2'").fetchone()[0];d.execute(schema);cols=[r[1] for r in s.execute('pragma table_info(public_source_snapshots_v2)')];qs=','.join('?'*len(ids));rows=s.execute(f"select {','.join(cols)} from public_source_snapshots_v2 where market_id in ({qs}) order by market_id,sampled_at_ms",ids).fetchall();d.executemany(f"insert into public_source_snapshots_v2 ({','.join(cols)}) values ({','.join('?'*len(cols))})",rows);d.commit()
 finally:s.close();d.close()
 if out.exists():out.unlink()
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for p in tmp.rglob('*'):
   if p.is_file():z.write(p,p.relative_to(tmp))
 print(json.dumps({'bundle':str(out.relative_to(ROOT)),'bytes':out.stat().st_size,'markets':len(ids),'publicRows':len(rows),'missing':missing}))
if __name__=='__main__':main()
