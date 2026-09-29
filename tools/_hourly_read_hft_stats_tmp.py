import sqlite3
c=sqlite3.connect('data/hft_forward_paper_v1.db')
q="""select strategy_key,count(*),sum(realized_pnl_usdt),sum(case when realized_pnl_usdt>0 then 1 else 0 end),avg(total_cost_usdt),max(total_cost_usdt) from hft_forward_runs_v1 where status='COMPLETE' and settled_at_ms is not null group by strategy_key order by strategy_key"""
for r in c.execute(q): print(r)
print('matched',c.execute("select count(distinct market_id) from hft_forward_runs_v1 where status='COMPLETE' and settled_at_ms is not null").fetchone()[0])
print('latestCompleteMs',c.execute("select max(completed_at_ms) from hft_forward_runs_v1 where status='COMPLETE'").fetchone()[0])
