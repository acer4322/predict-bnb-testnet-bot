import sqlite3,csv,argparse
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--out',default='data/research/r4_v0/p0_provenance_v1/target_btc_eth_market_liquidity_v1.csv'); a=p.parse_args()
paths={'BTC':'data/wallet_maker_book_inference.db','ETH':'data/wallet_maker_book_inference_eth5m.db'}
traj=sqlite3.connect('data/predict_fun_observer.db')
spread={}
for asset,mid,n,sp in traj.execute("select asset,market_id,count(*),avg((up_ask-up_bid+down_ask-down_bid)/2.0) from predict_fun_trajectory where asset in ('BTC','ETH') and up_bid is not null and up_ask is not null and down_bid is not null and down_ask is not null group by asset,market_id"):
    spread[(asset,int(mid))]=(int(n),float(sp))
rows=[]
for asset,path in paths.items():
    c=sqlite3.connect(path)
    q="""select m.market_id,m.window_end_ms,count(u.id),avg(u.order_count),avg(u.bid_level_count+u.ask_level_count),min(u.source_timestamp_ms),max(u.source_timestamp_ms) from maker_book_inference_markets m join maker_book_inference_updates u on u.market_id=m.market_id where m.window_end_ms is not null group by m.market_id,m.window_end_ms"""
    for mid,end,n,oc,levels,lo,hi in c.execute(q):
        ts=spread.get((asset,int(mid)))
        rows.append([asset,int(mid),int(end),int(n),float(oc or 0),float(levels or 0),(int(hi)-int(lo))/1000.0 if lo is not None and hi is not None else None,ts[0] if ts else 0,ts[1] if ts else None])
    c.close()
traj.close()
Path(a.out).parent.mkdir(parents=True,exist_ok=True)
with open(a.out,'w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['asset','market_id','window_end_ms','updates','avg_order_count','avg_total_levels','coverage_sec','spread_samples','avg_spread']); w.writerows(rows)
print(a.out,len(rows),sum(1 for r in rows if r[-1] is not None))
