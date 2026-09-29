import sqlite3,sys,json
for p in sys.argv[1:]:
    con=sqlite3.connect(p)
    tabs=[r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
    out={'db':p,'tables':{}}
    for t in tabs:
        cols=[r[1] for r in con.execute(f'pragma table_info({t})')]
        n=con.execute(f'select count(*) from {t}').fetchone()[0]
        out['tables'][t]={'n':n,'cols':cols}
    con.close();print(json.dumps(out,ensure_ascii=False))
