"""Build a bounded consumed-only BTC5M transfer package for V20 core-cycle zero-retune.

Main-host preparation only: bounded parquet extraction, hash/copy existing source/tapes.
No HFT, no training, no fresh/SEALED scan, no 8781 changes.
"""
from __future__ import annotations
import gzip, hashlib, json, shutil
from pathlib import Path
import duckdb

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1'
MIDS=(2023438,2026085,2026817,2028352,2029246)
FEATURES=('predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps','spotMinusChainlinkBps',
          'spotReturn1sBps','spotReturn3sBps','futuresReturn1sBps','futuresReturn3sBps',
          'perpSpotBasisBps','spotQueueImbalance','futuresQueueImbalance',
          'spotTakerImbalance1s','futuresTakerImbalance1s')

def sha(p:Path):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()

def rows(con,sql,params):
    cur=con.execute(sql,params); names=[x[0] for x in cur.description]
    return [dict(zip(names,r)) for r in cur.fetchall()]

def main():
    assert not OUT.exists(), OUT
    source=ROOT/'data/research/market_capsule_v1/source_bundle_50_v1'
    cap=ROOT/'data/research/market_capsule_v1/benchmark_50_v1'
    sm=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    tapes={int(x['marketId']):x for x in sm['tapes']}
    con=duckdb.connect(config={'threads':'1','memory_limit':'256MB'})
    placeholders=','.join('?'*len(MIDS))
    cohort=rows(con,f'SELECT market_id,window_start_ms,window_end_ms,quality_status,target_actions,public_snapshots,book_updates FROM read_parquet(?) WHERE market_id IN ({placeholders}) ORDER BY window_start_ms,market_id',[str(cap/'markets.parquet'),*MIDS])
    assert {x['market_id'] for x in cohort}==set(MIDS) and len(cohort)==len(MIDS)
    OUT.mkdir(parents=True); (OUT/'tapes').mkdir()
    windows=[]
    for row in cohort:
        mid=int(row['market_id'])
        actions=rows(con,'SELECT source_leg_id,market_id,role,quote_type,side,order_hash,event_ms,observed_at_ms,price,shares FROM read_parquet(?) WHERE market_id=? ORDER BY event_ms,source_leg_id',[str(cap/'target_actions.parquet'),mid])
        parents=rows(con,'SELECT market_id,asset,role,side,quote_type,order_hash,first_event_ms,last_event_ms,average_price,shares,fill_legs FROM read_parquet(?) WHERE market_id=? ORDER BY first_event_ms,order_hash',[str(cap/'target_parents.parquet'),mid])
        books=rows(con,'SELECT source_ms,received_ms,best_bid,best_ask,top5_bids_json,top5_asks_json FROM read_parquet(?) WHERE market_id=? ORDER BY received_ms,source_ms',[str(cap/'book_updates.parquet'),mid])
        for b in books:
            b['bids']=json.loads(b.pop('top5_bids_json') or '[]'); b['asks']=json.loads(b.pop('top5_asks_json') or '[]')
        publics=rows(con,'SELECT id,sampled_at_ms,timestamp_ns,archived_at_ms,snapshot_json FROM read_parquet(?) WHERE market_id=? ORDER BY sampled_at_ms,id',[str(cap/'public_snapshots.parquet'),mid])
        for p in publics:
            s=json.loads(p.pop('snapshot_json')); assert int(s['marketId'])==mid
            clocks=[p['sampled_at_ms'],p['timestamp_ns'],p['archived_at_ms'],s.get('sampledAtMs'),s.get('timestampNs')]
            p['available_ms']=(max(int(clocks[0]),(int(clocks[1])+999999)//1000000,int(clocks[2]),int(clocks[3]),(int(clocks[4])+999999)//1000000) if all(x is not None and int(x)>0 for x in clocks) else None)
            p['features']={k:s.get(k) for k in FEATURES}
        assert len(actions)==row['target_actions'] and len(publics)==row['public_snapshots'] and len(books)==row['book_updates']
        tape=source/tapes[mid]['file']; assert tape.exists() and tape.stat().st_size==tapes[mid]['bytes'] and sha(tape)==tapes[mid]['sha256']
        shutil.copy2(tape,OUT/'tapes'/f'{mid}.json.xz')
        bundle=dict(market=row,targetActions=actions,targetParents=parents,books=books,public=publics,
                    tape=dict(bytes=tape.stat().st_size,sha256=sha(tape),manifestMatch=True,contentsDecompressed=False),
                    targetObservationOnly=True,originalOrderQuantity=None,ownState=None,originalTerminalState=None,zeroFillOrderUniverse=None)
        raw=json.dumps(bundle,separators=(',',':'),allow_nan=False).encode(); assert len(raw)<=8*1024**2
        (OUT/f'input_{mid}.json.gz').write_bytes(gzip.compress(raw,mtime=0))
        windows.append(dict(marketId=mid,window=[int(row['window_start_ms']),int(row['window_end_ms'])],sourceCapturePass=True,qualityStatus=row['quality_status']))
    con.close()
    ws=OUT/'WINDOW_SOURCE_COMPACT.json'; ws.write_text(json.dumps({'version':'V20_CONSUMED_BTC5_TRANSFER5_WINDOW_SOURCE_V1','rows':windows},indent=2),encoding='utf-8')
    files={p.relative_to(OUT).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p)) for p in OUT.rglob('*') if p.is_file() and p.name!='MANIFEST.json'}
    manifest=dict(version='V20_CONSUMED_BTC5_TRANSFER5_V1',markets=list(MIDS),cohort='ALREADY_CONSUMED_BTC5_TARGET_MATCHED_TIMELINE_AUDIT_20260910',
                  targetRuntimeAuthority=False,targetScoringOnly=True,freshOrSealedConsumed=False,
                  sourceManifestSha256=sha(source/'manifest.json'),marketWindows={str(r['marketId']):r['window'] for r in windows},marketWindowSourceSha256=sha(ws),
                  frozenThetaSource='original consumed3 V20; no refit on transfer5',files=files,
                  limits=dict(HFT=0,training=0,live=0,fresh=0,mainHost='bounded extract/hash/copy only'))
    (OUT/'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'package':OUT.relative_to(ROOT).as_posix(),'markets':list(MIDS),'files':len(files),'bytes':sum(x['bytes'] for x in files.values()),'manifestSha256':sha(OUT/'MANIFEST.json')}))

if __name__=='__main__':main()
