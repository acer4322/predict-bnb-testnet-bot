from __future__ import annotations
import argparse,sqlite3,json,pickle,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.train_r3_stable_expansion_teacher_v1 as t
ap=argparse.ArgumentParser();ap.add_argument('--parent',type=int,required=True);ap.add_argument('--part',type=int,required=True);a=ap.parse_args()
con=sqlite3.connect(t.DB); allm=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; mids=allm[a.parent::12][a.part::2]; data=[]
for m in mids:data.extend(t.build(con,m))
con.close(); out=t.OUT/f'r3_stable_expansion_rows_parent{a.parent:02d}_part{a.part}.pkl';out.write_bytes(pickle.dumps(data,protocol=pickle.HIGHEST_PROTOCOL));print(json.dumps({'parent':a.parent,'part':a.part,'markets':len(mids),'rows':len(data),'out':str(out)}))
