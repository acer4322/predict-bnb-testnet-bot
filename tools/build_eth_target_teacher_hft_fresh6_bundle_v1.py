from __future__ import annotations
import json,lzma,sqlite3,zipfile,shutil,tempfile,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ETHDB=ROOT/'data/wallet_maker_book_inference_eth5m.db'
TARGETDB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_target_teacher_hft_fresh6_v1'
AFTER_MARKET_ID=1815060
COUNT=6

def dec(b): return json.loads(zlib.decompress(b).decode('utf-8')) if b else None

def compact_changes(v):
    out={'bids':[],'asks':[]}
    if not isinstance(v,dict): return out
    for side in ('bids','asks'):
        for r in (v.get(side) or []):
            if isinstance(r,dict): out[side].append([float(r.get('price',0)),float(r.get('before',0)),float(r.get('after',0)),float(r.get('delta',0))])
    return out

def archive_market(con,mid,out):
    con.row_factory=sqlite3.Row
    u=con.execute('select source_timestamp_ms,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)).fetchall()
    meta=[[int(r[0]),int(r[1]),int(r[2]),dec(r[3]),dec(r[4])] for r in con.execute('select source_timestamp_ms,received_at_ms,order_count,last_order_settled_z,settlements_pending_z from maker_execution_orderbook_meta_v1 where market_id=? order by source_timestamp_ms,received_at_ms,id',(mid,))]
    matches=[dec(r[0]) for r in con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(mid,))]
    mr=con.execute('select * from maker_book_inference_markets where market_id=?',(mid,)).fetchone(); market=dict(mr) if mr else None
    updates=[[int(r['source_timestamp_ms']),int(r['received_at_ms']),int(r['order_count']),int(r['is_checkpoint']),dec(r['native_bids_z']),dec(r['native_asks_z']),compact_changes(dec(r['changes_z']))] for r in u]
    payload={'version':'PREDICT_EXECUTION_TAPE_ARCHIVE_V1_READONLY_ASSET','marketId':mid,'market':market,'schema':{'updates':'[sourceMs,receivedMs,orderCount,isCheckpoint,bids?,asks?,changes]','executionMeta':'[sourceMs,receivedMs,orderCount,lastOrderSettled,settlementsPending]','matches':'raw Predict /v1/orders/matches payloads'},'updates':updates,'executionMeta':meta,'matches':matches}
    out.write_bytes(lzma.compress(json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode(),preset=3)); return out.stat().st_size

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    e=sqlite3.connect(ETHDB); t=sqlite3.connect(TARGETDB); rows=[]
    try:
        cand=[]
        for r in e.execute("select market_id,count(*) n,min(received_at_ms),max(received_at_ms) from maker_execution_orderbook_meta_v1 where market_id>? group by market_id having count(*)>500 order by max(received_at_ms)",(AFTER_MARKET_ID,)):
            mid=int(r[0]); tr=t.execute("select winner,net_pnl_usdt,buy_notional_usdt,up_position_shares,down_position_shares from target_market_results where asset='ETH' and market_id=? and winner in ('UP','DOWN')",(mid,)).fetchone(); mc=e.execute('select count(*) from maker_execution_matches_v1 where market_id=?',(mid,)).fetchone()[0]
            if tr and mc>0: cand.append((int(r[3]),mid,r,tr,mc))
        cand=sorted(cand)[:COUNT]
        for _,mid,r,tr,mc in cand:
            rows.append({'marketId':mid,'metaRows':int(r[1]),'matchRows':int(mc),'winner':str(tr[0]),'targetPnl':float(tr[1]),'targetBuy':float(tr[2]),'targetUp':float(tr[3]),'targetDown':float(tr[4])})
        if len(rows)<COUNT: raise SystemExit(f'need {COUNT} fresh settled markets after {AFTER_MARKET_ID}, got {len(rows)}: {[r["marketId"] for r in rows]}')
        tmp=Path(tempfile.mkdtemp(prefix='eth_teacher_fresh6_'))
        try:
            tapes=tmp/'tapes'; tapes.mkdir(parents=True)
            for i,row in enumerate(rows,1):
                p=tapes/f"{row['marketId']}.json.xz"; row['tapeBytes']=archive_market(e,int(row['marketId']),p); print(json.dumps({'progress':i,'total':len(rows),'marketId':row['marketId'],'bytes':row['tapeBytes']}),flush=True)
            (tmp/'cohort.json').write_text(json.dumps({'version':'ETH_TARGET_TEACHER_HFT_FRESH6_COHORT_V1','boundary':['chronology-forward markets strictly after dev20 max market 1815060','teacher model frozen before these markets','Target winner/PnL included only for settlement scoring; no Target action enters decisions'],'rows':rows},indent=2),encoding='utf-8')
            bundle=OUT/'bundle.zip'
            with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
                z.write(tmp/'cohort.json','cohort.json')
                for p in tapes.glob('*.json.xz'): z.write(p,f'tapes/{p.name}')
            print(json.dumps({'ok':True,'rows':len(rows),'ids':[r['marketId'] for r in rows],'bundle':str(bundle),'bytes':bundle.stat().st_size},ensure_ascii=False))
        finally: shutil.rmtree(tmp,ignore_errors=True)
    finally: e.close();t.close()
if __name__=='__main__': main()
