import json, sqlite3, time
HIST=[1490557,1490930,1490940,1491074,1491858,1491861,1492232,1492235,1492246,1492362,1492396,1492500,1492547,1493063,1493070,1493077,1493151]
c=sqlite3.connect('file:data/strategy_input_snapshot_archive_v1.db?mode=ro',uri=True,timeout=5)
out=[]; started=time.time()
for mid in HIST:
    r=c.execute('select count(*),sum(book_source_ms is not null),min(sampled_at_ms),max(sampled_at_ms) from strategy_input_snapshots_v1 indexed by idx_strategy_input_snapshots_v1_market where market_id=?',(mid,)).fetchone()
    out.append({'marketId':mid,'rows':int(r[0] or 0),'bookContextRows':int(r[1] or 0),'minSampleMs':r[2],'maxSampleMs':r[3]})
c.close()
print(json.dumps({'elapsedSeconds':time.time()-started,'marketsWithRows':sum(x['rows']>0 for x in out),'marketsWithBookContext':sum(x['bookContextRows']>0 for x in out),'rows':out}))
