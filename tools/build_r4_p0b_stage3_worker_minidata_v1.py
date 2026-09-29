from __future__ import annotations
import csv, json, sqlite3, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
LABEL=P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.csv'
SRC_DB=ROOT/'data/public_source_snapshot_archive_v2.db'
MINI=P/'r4_p0b_stage3_lane_f_public_source_31_v1.db'
BUNDLE=P/'r4_p0b_stage3_lane_f_worker_minidata_v1.zip'

def main():
 rows=list(csv.DictReader(LABEL.open(encoding='utf-8-sig'))); mids=[int(r['marketId']) for r in rows]
 if MINI.exists(): MINI.unlink()
 src=sqlite3.connect(SRC_DB); dst=sqlite3.connect(MINI)
 schema=src.execute("select sql from sqlite_master where type='table' and name='public_source_snapshots_v2'").fetchone()[0]
 dst.execute(schema); ph=','.join('?'*len(mids))
 data=src.execute(f'select * from public_source_snapshots_v2 where market_id in ({ph})',mids).fetchall()
 ncols=len(src.execute('pragma table_info(public_source_snapshots_v2)').fetchall())
 dst.executemany('insert into public_source_snapshots_v2 values ('+','.join('?'*ncols)+')',data)
 dst.execute('create index idx_ps_market_time on public_source_snapshots_v2(market_id,sampled_at_ms)'); dst.commit(); dst.close(); src.close()
 missing=[]
 with zipfile.ZipFile(BUNDLE,'w',compression=zipfile.ZIP_STORED) as z:
  z.write(MINI,arcname='public_source_snapshot_archive_v2.db')
  for mid in mids:
   f=ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz'
   if not f.exists(): missing.append(mid); continue
   z.write(f,arcname=f'execution_tape_v1/markets/{mid}.json.xz')
  z.writestr('manifest.json',json.dumps([{'marketId':int(r['marketId']),'candidateKey':r['candidateKey'],'role':r['knownRole'],'parentLogical':r['candidateKey'].split('|',1)[1]} for r in rows],indent=2))
 out={'version':'R4_P0B_STAGE3_WORKER_MINIDATA_V1','markets':len(mids),'publicSourceRows':len(data),'miniDbBytes':MINI.stat().st_size,'bundleBytes':BUNDLE.stat().st_size,'missingExecutionTapes':missing}
 print(json.dumps(out,indent=2))
if __name__=='__main__': main()
