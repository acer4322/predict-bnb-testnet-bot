from __future__ import annotations
import json, lzma, sqlite3, zlib, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SNAP=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
ETH=ROOT/'data/wallet_maker_book_inference_eth5m.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_min_notional_hft_pilot_v1'
TAPES=OUT/'tapes'
BUNDLE=OUT/'bundle.zip'

def dec(b): return json.loads(zlib.decompress(b).decode('utf-8')) if b else None

def compact_changes(v):
    out={'bids':[],'asks':[]}
    if not isinstance(v,dict): return out
    for side in ('bids','asks'):
        for r in v.get(side,[]) or []:
            if isinstance(r,dict): out[side].append([float(r.get('price',0)),float(r.get('before',0)),float(r.get('after',0)),float(r.get('delta',0))])
    return out

def archive(con,mid):
    TAPES.mkdir(parents=True,exist_ok=True); path=TAPES/f'{mid}.json.xz'
    con.row_factory=sqlite3.Row
    rows=con.execute('select source_timestamp_ms,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)).fetchall()
    meta=[[int(r[0]),int(r[1]),int(r[2]),dec(r[3]),dec(r[4])] for r in con.execute('select source_timestamp_ms,received_at_ms,order_count,last_order_settled_z,settlements_pending_z from maker_execution_orderbook_meta_v1 where market_id=? order by source_timestamp_ms,received_at_ms,id',(mid,))]
    matches=[dec(r[0]) for r in con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(mid,))]
    m=con.execute('select * from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
    payload={'version':'PREDICT_EXECUTION_TAPE_ARCHIVE_V1_READONLY_ETH_PILOT','marketId':mid,'market':dict(m) if m else None,'schema':{'updates':'[sourceMs,receivedMs,orderCount,isCheckpoint,bids?,asks?,changes]','executionMeta':'[sourceMs,receivedMs,orderCount,lastOrderSettled,settlementsPending]','matches':'raw Predict /v1/orders/matches payloads'},'updates':[[int(r['source_timestamp_ms']),int(r['received_at_ms']),int(r['order_count']),int(r['is_checkpoint']),dec(r['native_bids_z']),dec(r['native_asks_z']),compact_changes(dec(r['changes_z']))] for r in rows],'executionMeta':meta,'matches':matches}
    path.write_bytes(lzma.compress(json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode(),preset=3))
    return {'marketId':mid,'updates':len(rows),'meta':len(meta),'matches':len(matches),'bytes':path.stat().st_size}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    s=sqlite3.connect(SNAP); s.row_factory=sqlite3.Row
    rows=list(s.execute("select parent_id,market_id,role,side,first_event_ms,last_event_ms,average_price,shares,fill_legs from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"))
    e=sqlite3.connect(ETH); e.row_factory=sqlite3.Row
    cohort=[]; cur=None; up=down=0.0
    for r in rows:
        mid=int(r['market_id'])
        if mid!=cur: cur=mid; up=down=0.0
        pre_up,pre_down=up,down; side=str(r['side']); gap=abs(up-down)
        weak=(up<down and side=='UP') or (down<up and side=='DOWN')
        if str(r['role'])=='TAKER' and float(r['average_price'])<=0.0500000001 and weak:
            u=e.execute('select count(*),min(received_at_ms),max(received_at_ms) from maker_book_inference_updates where market_id=?',(mid,)).fetchone()
            meta=e.execute('select count(*) from maker_execution_orderbook_meta_v1 where market_id=?',(mid,)).fetchone()[0]
            matches=e.execute('select count(*) from maker_execution_matches_v1 where market_id=?',(mid,)).fetchone()[0]
            if u[0] and meta and int(r['first_event_ms'])>=int(u[1] or 0) and int(r['first_event_ms'])<=int(u[2] or 0)+1000:
                price=float(r['average_price']); obs=float(r['shares'])
                cohort.append({'parentId':r['parent_id'],'marketId':mid,'side':side,'firstEventMs':int(r['first_event_ms']),'lastEventMs':int(r['last_event_ms']),'price':price,'observedFillQty':obs,'fillLegs':int(r['fill_legs']),'preUp':pre_up,'preDown':pre_down,'gap':gap,'minLegalQty':1.0/price,'minLegalToGap':(1.0/price)/gap if gap>0 else None,'updates':int(u[0]),'metaRows':int(meta),'matchRows':int(matches),'coverageStartMs':int(u[1]),'coverageEndMs':int(u[2])})
        if side=='UP': up+=float(r['shares'])
        else: down+=float(r['shares'])
    mids=sorted({x['marketId'] for x in cohort})
    tapes=[archive(e,m) for m in mids]
    payload={'version':'ETH_MIN_NOTIONAL_HFT_PILOT_V1_COHORT','selection':'all ETH Target TAKER parent orders with price<=0.05 and weak-side acquisition in frozen snapshot, requiring contemporaneous ETH L2 updates + execution meta coverage; no outcome selection','rows':cohort,'markets':mids,'tapes':tapes,'boundaries':['Target parent event_ms is observed execution chronology, not original submit timestamp','minimum legal qty assumes 1 USDT minimum notional at Target quote price','pre inventory uses prior Target parent observed fills only']}
    (OUT/'cohort.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
    with zipfile.ZipFile(BUNDLE,'w',compression=zipfile.ZIP_STORED) as z:
        z.write(OUT/'cohort.json','cohort.json')
        for m in mids: z.write(TAPES/f'{m}.json.xz',f'tapes/{m}.json.xz')
    print(json.dumps({'ok':True,'rows':len(cohort),'markets':len(mids),'bundle':str(BUNDLE),'bundleBytes':BUNDLE.stat().st_size},ensure_ascii=False))
if __name__=='__main__': main()
