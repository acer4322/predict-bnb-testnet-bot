from __future__ import annotations
import argparse,sqlite3,json,pickle
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.train_r3_stable_expansion_teacher_v1 as t
ap=argparse.ArgumentParser();ap.add_argument('--shard',type=int,required=True);ap.add_argument('--nshards',type=int,default=12);a=ap.parse_args()
con=sqlite3.connect(t.DB); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; mids=mids[a.shard::a.nshards]; data=[]
for m in mids:data.extend(t.build(con,m))
con.close(); out=t.OUT/f'r3_stable_expansion_rows_shard{a.shard:02d}_of{a.nshards:02d}.pkl'; out.write_bytes(pickle.dumps(data,protocol=pickle.HIGHEST_PROTOCOL)); print(json.dumps({'shard':a.shard,'markets':len(mids),'rows':len(data),'out':str(out)}))
