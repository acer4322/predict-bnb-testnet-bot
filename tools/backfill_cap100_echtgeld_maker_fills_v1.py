from __future__ import annotations
import json, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'data'/'echtgeld_engine_v1.db'
STRAT=ROOT/'data'/'strategy_cap100_echtgeld_v1.db'
MARKET=1513668

eng=sqlite3.connect(ENGINE); eng.row_factory=sqlite3.Row
out=sqlite3.connect(STRAT); out.row_factory=sqlite3.Row
rows=eng.execute("""select * from engine_cap100_events where source_market_id=? and event_type='FILL_DELTA' and upper(role)='MAKER' order by seq""",(MARKET,)).fetchall()
inserted=0; skipped=0; missing=[]
for e in rows:
    cid=str(e['client_order_id'] or '')
    order=out.execute('select * from our_orders where order_id=?',(cid,)).fetchone()
    if order is None:
        missing.append(cid); continue
    fid=f"{cid}:ENGINE_FILL_DELTA:{int(e['seq'])}"
    if out.execute('select 1 from our_fills where fill_id=?',(fid,)).fetchone():
        skipped+=1; continue
    shares=float(e['delta_shares'] or 0); price=float(e['fill_price'] or 0)
    if shares<=0: continue
    state=str(e['state'] or '').upper()
    fill_state=json.dumps({'venueConfirmed':True,'engine':'8781','engineEventSeq':int(e['seq']),'engineState':state,'deltaShares':shares,'deltaUsdt':float(e['delta_usdt'] or 0),'historicalBackfill':True},separators=(',',':'))
    payload=json.dumps({'paperOnly':False,'executionOwner':'8781_ONLY','historicalBackfill':True},separators=(',',':'))
    out.execute("""insert into our_fills(fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,price,shares,filled_at_ms,decision_state_json,fill_state_json,payload_json) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fid,order['strategy_version'],order['market_id'],order['placement_decision_id'],cid,order['channel'],'PASSIVE_MAKER_REAL',order['side'],order['quote_type'],price,shares,int(e['occurred_at_ms']),order['placement_state_json'],fill_state,payload))
    if state=='FILLED':
        out.execute("update our_orders set status='FILLED',filled_at_ms=?,fill_price=?,fill_state_json=?,updated_at_ms=? where order_id=?",(int(e['occurred_at_ms']),price,fill_state,int(e['occurred_at_ms']),cid))
    inserted+=1
out.commit()
maker=out.execute("select count(*) n,coalesce(sum(shares),0) s from our_fills where market_id=? and channel='MAKER'",(MARKET,)).fetchone()
print(json.dumps({'marketId':MARKET,'engineMakerFillDeltas':len(rows),'inserted':inserted,'skippedExisting':skipped,'missingOrders':len(set(missing)),'recorderMakerFillRows':int(maker['n']),'recorderMakerShares':float(maker['s'])},indent=2))
eng.close(); out.close()
