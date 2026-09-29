from pathlib import Path
import sqlite3,csv,pandas as pd
out=Path('data/research/r4_v0/p0_provenance_v1/r4_target_sequence_teacher_v1_worker_bundle/data')
mids=[int(x) for x in pd.read_csv(out/'topology_rows.csv',usecols=['market_id']).market_id.unique()]
db=sqlite3.connect('data/target_wallet_official_v1.db')
db.execute('create temp table mids(mid integer primary key)')
db.executemany('insert into mids(mid) values(?)',[(m,) for m in mids])
q="""select p.market_id,p.parent_id,p.role,p.side,p.quote_type,lower(p.order_hash),p.first_event_ms,p.last_event_ms,p.average_price,p.shares,p.fill_legs
from target_parent_orders p indexed by idx_target_parents_asset_market_time join mids m on m.mid=p.market_id
where p.asset='BTC' and p.quote_type='BID' order by p.market_id,p.first_event_ms,p.parent_id"""
rows=list(db.execute(q))
with open(out/'parents.csv','w',newline='',encoding='utf-8') as f:
 w=csv.writer(f);w.writerow(['market_id','parent_id','role','side','quote_type','order_hash','first_event_ms','last_event_ms','average_price','shares','fill_legs']);w.writerows(rows)
mk=list(db.execute('select t.market_id,t.window_end_ms from target_markets t join mids m on m.mid=t.market_id order by t.market_id'))
with open(out/'markets.csv','w',newline='',encoding='utf-8') as f:
 w=csv.writer(f);w.writerow(['market_id','window_end_ms']);w.writerows(mk)
db.close()
print({'parents':len(rows),'markets':len(mk),'parentBytes':(out/'parents.csv').stat().st_size,'marketBytes':(out/'markets.csv').stat().st_size})
