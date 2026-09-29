from __future__ import annotations
import json, os, sqlite3, time
from pathlib import Path
SRC=Path('data/microstructure.db')
DST=Path('data/microstructure.compact.ready.db')
STATUS=Path('data/microstructure.compact.status.json')
def write(x): STATUS.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
if DST.exists(): DST.unlink()
start=time.time(); write({'status':'RUNNING','started_at':time.time(),'src_size':SRC.stat().st_size})
try:
    con=sqlite3.connect(str(SRC),timeout=60)
    con.execute('PRAGMA busy_timeout=60000')
    con.execute("VACUUM INTO 'data/microstructure.compact.ready.db'")
    con.close()
    d=sqlite3.connect(str(DST),timeout=30)
    out={
      'status':'COMPLETE','elapsed_s':round(time.time()-start,2),
      'src_size':SRC.stat().st_size,'dst_size':DST.stat().st_size,
      'dst_pages':d.execute('PRAGMA page_count').fetchone()[0],
      'dst_free':d.execute('PRAGMA freelist_count').fetchone()[0],
      'dst_page_size':d.execute('PRAGMA page_size').fetchone()[0],
      'tables':[r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    }
    d.close(); write(out)
except Exception as e:
    write({'status':'ERROR','elapsed_s':round(time.time()-start,2),'error':repr(e)})
    raise
