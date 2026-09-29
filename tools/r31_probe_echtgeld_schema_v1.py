import sqlite3, json
from pathlib import Path
p=Path('data/echtgeld_engine_v1.db')
con=sqlite3.connect(p)
out=[]
for (t,) in con.execute("select name from sqlite_master where type='table' order by name"):
    cols=[r[1] for r in con.execute(f'pragma table_info({t})')]
    if 'event_type' in cols or 'client_order_id' in cols or 'source_id' in cols:
        out.append({'table':t,'columns':cols})
con.close()
print(json.dumps(out,ensure_ascii=False))
