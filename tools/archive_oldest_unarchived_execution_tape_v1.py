from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.execution_tape_archive_v1 import archive_market_to_xz
DB=ROOT/'data'/'wallet_maker_book_inference.db'; OUT=ROOT/'data'/'execution_tape_v1'/'markets'

def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--count',type=int,default=50); args=ap.parse_args()
 c=sqlite3.connect(DB)
 c.execute('create table if not exists maker_execution_archive_manifest_v1 (market_id integer primary key,archive_path text not null,archive_bytes integer not null,l2_rows integer not null,meta_rows integer not null,match_rows integer not null,archived_at_ms integer not null,version text not null)'); c.commit()
 ids=[int(r[0]) for r in c.execute('select u.market_id from maker_book_inference_updates u left join maker_execution_archive_manifest_v1 a on a.market_id=u.market_id group by u.market_id having max(a.market_id) is null order by min(u.received_at_ms) limit ?',(max(1,args.count),))]; c.close()
 total=0
 for i,m in enumerate(ids,1):
  x=archive_market_to_xz(DB,m,OUT,overwrite=False); total+=int(x.get('archiveBytes') or x.get('archive_bytes') or 0)
 print(json.dumps({'ok':True,'markets':len(ids),'archiveBytes':total,'first':ids[0] if ids else None,'last':ids[-1] if ids else None}))
 return 0
if __name__=='__main__': raise SystemExit(main())
