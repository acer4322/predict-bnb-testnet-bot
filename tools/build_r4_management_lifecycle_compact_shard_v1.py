from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.predict_wallet_maker_book_inference_collector_v2_1_impl import MakerBookConsumableLifecycleCollector
MAIN=ROOT/'data/wallet_maker_book_inference.db'

def copy_table(src:sqlite3.Connection,dst:sqlite3.Connection,table:str,ids:list[int]):
 cols=[r[1] for r in src.execute(f'pragma table_info({table})')]
 qs=','.join('?'*len(ids)); rows=src.execute(f"select {','.join(cols)} from {table} where market_id in ({qs})",ids).fetchall()
 if rows:
  ph=','.join('?'*len(cols)); dst.executemany(f"insert or replace into {table}({','.join(cols)}) values({ph})",rows)
 return len(rows)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--out-db',required=True);a=ap.parse_args()
 ids=[int(x) for x in json.loads((ROOT/a.ids_json).read_text())['backfillMarkets']]
 out=ROOT/a.out_db;out.parent.mkdir(parents=True,exist_ok=True)
 if out.exists():out.unlink()
 dummy=out.with_suffix('.target_dummy.db')
 if dummy.exists():dummy.unlink()
 c=MakerBookConsumableLifecycleCollector(out,dummy);c.stop()
 src=sqlite3.connect(f'file:{MAIN.resolve().as_posix()}?mode=ro',uri=True);dst=sqlite3.connect(out)
 try:
  counts={}
  for t in ['maker_book_inference_markets','maker_book_inference_updates','maker_book_inference_target_events']:
   counts[t]=copy_table(src,dst,t,ids)
  dst.commit()
  status=dict(dst.execute('select status,count(*) from maker_book_inference_target_events group by status').fetchall())
 finally:src.close();dst.close()
 meta={'version':'R4_MANAGEMENT_LIFECYCLE_COMPACT_SHARD_V1','ids':ids,'counts':counts,'targetStatus':status,'db':str(out.relative_to(ROOT)).replace('\\','/'),'bytes':out.stat().st_size}
 out.with_suffix('.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,indent=2))
if __name__=='__main__':main()
