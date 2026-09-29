import sqlite3, statistics, json, argparse
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--btc',default='data/wallet_maker_book_inference.db')
p.add_argument('--eth',default='data/wallet_maker_book_inference_eth5m.db')
p.add_argument('--out',default='data/research/r4_v0/p0_provenance_v1/target_btc_eth_liquidity_common_windows_v1.json')
a=p.parse_args()

def weighted(rows, idx):
    den=sum(r[0] for r in rows if r[idx] is not None)
    return sum(r[0]*r[idx] for r in rows if r[idx] is not None)/den if den else None

def summarize(con, ids):
    rows=[]
    for mid in ids:
        rows.append(con.execute('select count(*),avg(order_count),avg(bid_level_count),avg(ask_level_count),min(source_timestamp_ms),max(source_timestamp_ms) from maker_book_inference_updates where market_id=?',(mid,)).fetchone())
    return {
      'markets':len(rows),
      'updates':sum(r[0] for r in rows),
      'medianUpdatesPerMarket':statistics.median(r[0] for r in rows) if rows else None,
      'meanOrderCount':weighted(rows,1),
      'meanBidLevels':weighted(rows,2),
      'meanAskLevels':weighted(rows,3),
      'medianCoverageSec':statistics.median((r[5]-r[4])/1000 for r in rows if r[4] is not None and r[5] is not None) if rows else None,
    }

btc=sqlite3.connect(a.btc); eth=sqlite3.connect(a.eth)
mb={int(r[1]):int(r[0]) for r in btc.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}
me={int(r[1]):int(r[0]) for r in eth.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}
common=sorted(set(mb)&set(me))
bids=[mb[w] for w in common]; eids=[me[w] for w in common]
out={'version':'TARGET_BTC_ETH_LIQUIDITY_COMMON_WINDOWS_V1','commonWindows':len(common),'firstWindowEndMs':common[0] if common else None,'lastWindowEndMs':common[-1] if common else None,'BTC':summarize(btc,bids),'ETH':summarize(eth,eids),'boundary':['order_count and level counts are public-book structure/activity proxies, not exchange-reported traded volume','same window_end_ms only']}
Path(a.out).parent.mkdir(parents=True,exist_ok=True); Path(a.out).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
btc.close(); eth.close()
