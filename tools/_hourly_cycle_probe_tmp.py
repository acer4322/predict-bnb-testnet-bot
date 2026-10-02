import json,sqlite3,urllib.request,time
out={}
con=sqlite3.connect('data/hft_forward_paper_v1.db')
for s in ('R2','CAP100'):
 r=con.execute("select count(*),coalesce(sum(realized_pnl_usdt),0),coalesce(sum(case when realized_pnl_usdt>0 then 1 else 0 end),0),coalesce(avg(total_cost_usdt),0),coalesce(max(total_cost_usdt),0) from hft_forward_runs_v1 where strategy_key=? and status='COMPLETE' and realized_pnl_usdt is not null",(s,)).fetchone()
 out[s]={'n':r[0],'pnl':r[1],'positive':r[2],'positiveRate':r[2]/r[0] if r[0] else None,'meanCapital':r[3],'maxCapital':r[4]}
out['capBreaches']=con.execute("select market_id,total_cost_usdt from hft_forward_runs_v1 where strategy_key='CAP100' and status='COMPLETE' and realized_pnl_usdt is not null and total_cost_usdt>100 order by total_cost_usdt desc").fetchall()
con.close()
for p in (8778,8783,8784,8786,8788):
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{p}/health',timeout=4) as r: x=json.loads(r.read().decode())
  out[str(p)]={k:x.get(k) for k in ('ok','status','sourceReady','bookReady','lastError','lastSnapshotAgeMs','lastLoopAgeMs','websocketStatus','lastReceivedMs','latest_received_ms','sampleAgeMs','strategyInputReady','missingFeatures')}
 except Exception as e: out[str(p)]={'error':repr(e)}
print(json.dumps(out,ensure_ascii=False))
