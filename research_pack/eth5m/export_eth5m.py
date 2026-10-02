"""Freeze/export public ETH5M books, strict official WON labels and BTC feed V1 NPZ.

Only the new output folder is written; SQLite and original archives are read-only.
No native engine, orders, services, model fits or wallet fields are exported.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import lzma
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import numpy as np

sys.dont_write_bytecode = True


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()+b'\n'
    path.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == '.gz' else raw)


def read(path):
    raw = path.read_bytes()
    if path.suffix == '.gz': raw = gzip.decompress(raw)
    if path.suffix == '.xz': raw = lzma.decompress(raw)
    return json.loads(raw)


def eth_title(title):
    if not str(title).startswith('Ethereum Up or Down - '): return False
    m = re.search(r'(\d{1,2})(?::(\d{2}))?(AM|PM)?\s*-\s*(\d{1,2})(?::(\d{2}))?(AM|PM)\s+ET', title, re.I)
    if not m: return False
    h1, m1, a1, h2, m2, a2 = m.groups()
    def minute(h, m, a):
        return (int(h)%12+(12 if a.upper()=='PM' else 0))*60+int(m or 0)
    return any((minute(h2,m2,a2)-minute(h1,m1,a))%1440 == 5 for a in ([a1] if a1 else ['AM','PM']))


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def select(args):
    assert not args.out.exists(), 'preserve existing selection/output'
    assert not args.frozen.exists(), 'preserve frozen source'
    now = int(time.time()*1000)
    cutoff = now-900000  # completed at least 15 min ago; independent of outcomes
    con = sqlite3.connect((args.repo/'data/wallet_maker_book_inference_eth5m.db').as_uri()+'?mode=ro', uri=True)
    con.execute('PRAGMA query_only=ON')
    rows = con.execute('''SELECT m.market_id,m.title,m.window_end_ms,a.archive_path,
      a.l2_rows,a.match_rows,a.archived_at_ms,q.quality_status
      FROM maker_book_inference_markets m JOIN maker_execution_archive_manifest_v1 a USING(market_id)
      LEFT JOIN maker_execution_market_quality_v1 q USING(market_id)
      WHERE m.window_end_ms IS NOT NULL AND m.window_end_ms<=?
      ORDER BY m.window_end_ms,m.market_id''', (cutoff,)).fetchall()
    con.close()
    assert rows and all(eth_title(r[1]) for r in rows)
    runs=[]; current=[]
    for row in rows:
        if current and row[2] != current[-1][2]+300000:
            runs.append(current); current=[]
        current.append(row)
    if current: runs.append(current)
    chosen = max(runs, key=lambda run:(len(run),run[-1][2]))
    assert len(chosen)>=100
    args.out.mkdir(parents=True); args.frozen.mkdir(parents=True)
    records=[]
    for mid,title,end,path,l2,matches,archived,quality in chosen:
        raw = Path(path).read_bytes()
        (args.frozen/f'{mid}.json.xz').write_bytes(raw)
        records.append(dict(market_id=mid,title=title,window_start_ms=end-300000,window_end_ms=end,
          source_sha256=sha(raw),source_bytes=len(raw),manifest_l2_rows=l2,manifest_match_rows=matches,
          archived_at_ms=archived,collector_quality_status=quality))
    policy='All markets in the longest continuous archived ETH5M run at a fixed snapshot, ending at least 15 minutes before selection. No outcome, price, performance, quality or trade-count filtering; ties choose latest run.'
    save(args.out/'SELECTION.json',dict(version='ETH5M_CONTINUOUS_PUBLIC_20261002_V1',selected_at_ms=now,
      cutoff_end_ms=cutoff,selection_policy=policy,source='ETH5M public archive manifest',
      records=records,markets=len(records),adjacent_window_step_ms=300000,
      source_outcome_fields_read=False,excluded_other_runs=[dict(markets=len(r),first_end_ms=r[0][2],last_end_ms=r[-1][2]) for r in runs if r is not chosen]))
    source = args.btc/'converter_source'
    for name in ('export_hft244_repro_samples.py','types.py','hftbacktest_execution_tape_feed_v1.py',
                 'hftbacktest_execution_shift_audit_v0.py','hftbacktest_true_match_calibration_v0.py'):
        dest=args.out/'converter_source'/name; dest.parent.mkdir(exist_ok=True); shutil.copy2(source/name,dest)
    shutil.copy2(args.btc/'convert_batch.py',args.out/'converter_source/btc_convert_batch.py')
    shutil.copy2(Path(__file__),args.out/'export_eth5m.py')
    save(args.out/'CONVERTER_SOURCE_SHA256.json',{p.relative_to(args.out).as_posix():sha(p.read_bytes()) for p in (args.out/'converter_source').glob('*.py')})
    print(json.dumps(dict(selected=len(records),first=records[0],last=records[-1],quality_counts=dict(Counter(r['collector_quality_status'] for r in records)))),flush=True)


def api_key(repo):
    value=os.environ.get('PREDICT_FUN_API_KEY')
    if not value:
        for line in (repo/'.env').read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('PREDICT_FUN_API_KEY='):
                value=line.partition('=')[2].strip().strip('\"\''); break
    if not value: raise RuntimeError('public API access unavailable')
    return value


def strict_winner(data):
    winners=[str(o.get('name','')).upper() for o in data.get('outcomes',[]) if str(o.get('status','')).upper()=='WON']
    if str(data.get('status','')).upper() not in ('RESOLVED','SETTLED'): return None
    return winners[0] if len(winners)==1 and winners[0] in ('UP','DOWN') else None


def fetch(args, reverse_prefetch=False):
    import httpx
    cfg=read(args.out/'SELECTION.json'); unknown=[]; proof=[]; labels=[]
    with httpx.Client(timeout=20,trust_env=False,headers={'x-api-key':api_key(args.repo),
      'Accept':'application/json','User-Agent':'research-readonly/1.0'}) as client:
        rows=list(reversed(cfg['records'])) if reverse_prefetch else cfg['records']
        for i,row in enumerate(rows,1):
            mid=row['market_id']; path=args.out/'labels/api'/f'{mid}.json'
            if reverse_prefetch and path.exists():
                break  # forward reader will assemble the complete chronological labels
            if path.exists(): data=read(path)
            else:
                url=f'https://api.predict.fun/v1/markets/{mid}'
                data=None
                for attempt in range(4):
                    try:
                        response=client.get(url)
                        if response.status_code in (429,500,502,503,504):
                            time.sleep(min(10,2+attempt*2)); continue
                        response.raise_for_status(); payload=response.json(); assert payload.get('success') is not False
                        d=payload['data']; assert int(d['id'])==mid and eth_title(d['title'])
                        assert d.get('categorySlug')==f"eth-updown-5m-{row['window_start_ms']//1000}", 'official window differs'
                        assert d.get('marketVariant')=='CRYPTO_UP_DOWN'
                        data=dict(market_id=mid,title=d['title'],status=str(d.get('status','')).upper(),
                          marketVariant=d.get('marketVariant'),categorySlug=d['categorySlug'],feeRateBps=d.get('feeRateBps'),
                          outcomes=[dict(name=o.get('name'),status=o.get('status')) for o in d.get('outcomes',[])],
                          source=url,fetched_at_ms=int(time.time()*1000),label_rule='Exactly one outcomes[].status=WON; RESOLVED/SETTLED market; UP or DOWN only')
                        data['winner']=strict_winner(data)
                        if reverse_prefetch:
                            temp=path.with_suffix('.prefetch.tmp');save(temp,data);os.replace(temp,path)
                        else:save(path,data)
                        break
                    except (httpx.HTTPError,ValueError,KeyError,AssertionError) as error:
                        if attempt==3:
                            unknown.append(dict(market_id=mid,reason=type(error).__name__));break
                        time.sleep(2+attempt)
                time.sleep(.85) # leave most shared-key capacity for running collectors
            if data:
                winner=strict_winner(data); proof.append(data)
                if winner: labels.append(dict(market_id=mid,winner=winner))
                else: unknown.append(dict(market_id=mid,reason='NO_UNIQUE_OFFICIAL_WON'))
            if i%25==0 or i==len(cfg['records']):
                print(json.dumps(dict(official_checked=i,total=len(cfg['records']),labels=len(labels),unknown=len(unknown))),flush=True)
    if reverse_prefetch:
        print(json.dumps(dict(reverse_prefetch_complete=True,cached=len(proof))),flush=True)
        return
    save(args.out/'labels/OFFICIAL_WON_LABELS.json',dict(records=labels))
    save(args.out/'labels/OFFICIAL_WON_PROVENANCE.json',dict(records=proof,unknown=unknown,
      selection_sha256=sha((args.out/'SELECTION.json').read_bytes()),inference_used=False,
      api_documentation='https://dev.predict.fun/get-market-by-id-25552989e0'))
    save(args.out/'labels/STATUS.json',dict(selected=len(cfg['records']),labels=len(labels),unknown=unknown))


def prefetch(args):
    fetch(args, reverse_prefetch=True)


def books(tape,start,end):
    levels={'bids':{},'asks':{}}; result=[]; chain=None
    for u in sorted(tape['updates'],key=lambda x:(int(x[0]),int(x[1]))):
        src,recv,_,cp,cb,ca,changes=u; src=int(src);recv=int(recv)
        if cp and cb is not None and ca is not None:
            levels={side:{float(p):float(q) for p,q in v.items() if float(q)>0} for side,v in [('bids',cb),('asks',ca)]};chain=recv
        else:
            if chain is None: continue
            chain=max(chain,recv)
            for side in ('bids','asks'):
                for p,_,after,_ in (changes or {}).get(side,[]):
                    if float(after)<=1e-9:levels[side].pop(float(p),None)
                    else:levels[side][float(p)]=float(after)
        if not start-2000<=src<=end+2000: continue
        bids=sorted(levels['bids'].items(),reverse=True)[:5];asks=sorted(levels['asks'].items())[:5]
        assert all(math.isfinite(p) and math.isfinite(q) and 0<p<1 and q>0 for p,q in bids+asks)
        result.append(dict(source_ms=src,received_ms=recv,chain_received_max_ms=chain,
          best_bid=bids[0][0] if bids else None,best_ask=asks[0][0] if asks else None,
          bids=[list(x) for x in bids],asks=[list(x) for x in asks]))
    return result


def export(args):
    cfg=read(args.out/'SELECTION.json');source=args.out/'converter_source'
    helper=module(source/'export_hft244_repro_samples.py','public_helper')
    btc=module(source/'btc_convert_batch.py','btc_converter')
    ns=helper.pure_converter(source/'hftbacktest_execution_tape_feed_v1.py',source/'types.py')
    def eth_window(tape):
        assert eth_title(tape['market']['title'])
        end=int(tape['market']['window_end_ms']);return end-300000,end
    btc.window=eth_window
    # Check exact feed bytes against three already published BTC fixtures.
    ref=read(args.btc/'INDEX.json')['records'][:3]
    for row in ref:
        folder=(args.btc/row['path']).parent
        mid=row['market_id']; ns['ARCHIVE_DIR']=args.repo/'data/execution_tape_v1/markets'
        reference,reference_times,_=ns['build_archive_events'](mid,trade_offset='mid')
        with np.load(folder/'events.npz',allow_pickle=False) as z:
            assert z['data'].dtype==ns['event_dtype'] and z['data'].dtype.itemsize==64
            assert z['data'].tobytes()==reference.tobytes()
            assert z['local_times_ms'].tobytes()==np.asarray(reference_times,dtype=np.int64).tobytes()
    records=[]
    for i,row in enumerate(cfg['records'],1):
        mid=row['market_id'];tp=args.frozen/f'{mid}.json.xz';raw=tp.read_bytes()
        assert sha(raw)==row['source_sha256'];tape=json.loads(lzma.decompress(raw))
        assert int(tape['marketId'])==mid and int(tape['market']['market_id'])==mid
        assert tape['market']['title']==row['title']
        start,end=eth_window(tape);assert [start,end]==[row['window_start_ms'],row['window_end_ms']]
        ev,times,meta=btc.build_one(ns,helper,tp,mid,row['source_sha256'])
        meta['window_start_derivation']='window_end_ms - 300000; ETH title five-minute interval validated; official categorySlug checked separately'
        meta.update(asset='ETH',timeframe='5M',selection_group='continuous_archived_eth5m',collector_quality_status=row['collector_quality_status'])
        dest=args.out/'events/markets'/str(mid);dest.mkdir(parents=True,exist_ok=False)
        with (dest/'events.npz').open('xb') as f:np.savez_compressed(f,data=ev,local_times_ms=times)
        with np.load(dest/'events.npz',allow_pickle=False) as disk:
            assert disk.files==['data','local_times_ms'] and disk['data'].tobytes()==ev.tobytes() and disk['local_times_ms'].tobytes()==times.tobytes()
        meta.update(npz_sha256=sha((dest/'events.npz').read_bytes()),npz_bytes=(dest/'events.npz').stat().st_size,npz_readback_parity='PASS')
        save(dest/'META.json',meta)
        bs=books(tape,start,end);assert bs
        p=args.out/'public_markets'/str(mid)/f'public_{mid}.json.gz'
        save(p,dict(market=dict(market_id=mid,asset='ETH',timeframe='5M',title=row['title'],window_start_ms=start,window_end_ms=end),books=bs))
        book_meta=dict(market_id=mid,frames=len(bs),empty_bid_frames=sum(not b['bids'] for b in bs),empty_ask_frames=sum(not b['asks'] for b in bs),
          chain_received_later_frames=sum(b['chain_received_max_ms']>b['received_ms'] for b in bs),
          source_order='source_ms then received_ms; checkpoint/delta reconstruction, matching BTC public exports',
          causal_availability='max(received_ms,chain_received_max_ms); received_ms retains the actual callback timestamp',
          top_levels='Up to five actual levels per side; no fabricated padding',public_sha256=sha(p.read_bytes()))
        save(p.with_name('META.json'),book_meta)
        records.append(dict(market_id=mid,window_start_ms=start,window_end_ms=end,public_path=p.relative_to(args.out).as_posix(),
          event_path=(dest/'events.npz').relative_to(args.out).as_posix(),frames=len(bs),events=len(ev),public_trades=meta['conversion']['normalizedTrades'],
          public_sha256=book_meta['public_sha256'],npz_sha256=meta['npz_sha256'],collector_quality_status=row['collector_quality_status']))
        if i%25==0 or i==len(cfg['records']):print(json.dumps(dict(exported=i,total=len(cfg['records']),events=sum(x['events'] for x in records),public_trades=sum(x['public_trades'] for x in records))),flush=True)
    save(args.out/'INDEX.json',dict(version='ETH5M_CONTINUOUS_PUBLIC_PACK_V1',records=records,markets=len(records),
      labels_path='labels/OFFICIAL_WON_LABELS.json',label_provenance_path='labels/OFFICIAL_WON_PROVENANCE.json',
      selection_sha256=sha((args.out/'SELECTION.json').read_bytes()),selection_policy=cfg['selection_policy'],
      clock_contract='Exact BTC feed V1: L2 receivedMs in Unix ns; real matches executedAt plus mid half-second offset. No native replay.',
      public_trade_collection_completeness='UNKNOWN: retained archives have no complete pagination certificate',
      native_executed=False,model_fits=0))


def verify(args):
    cfg=read(args.out/'SELECTION.json');idx=read(args.out/'INDEX.json')
    labels=read(args.out/'labels/OFFICIAL_WON_LABELS.json')['records'];proof=read(args.out/'labels/OFFICIAL_WON_PROVENANCE.json')
    ids=[r['market_id'] for r in cfg['records']];assert ids==[r['market_id'] for r in idx['records']]
    assert len(ids)==len(set(ids))>=100
    assert all(b['window_start_ms']==a['window_end_ms'] for a,b in zip(cfg['records'],cfg['records'][1:]))
    by={r['market_id']:r for r in proof['records']};assert len(by)==len(ids)
    expected=[dict(market_id=mid,winner=strict_winner(by[mid])) for mid in ids if strict_winner(by[mid])]
    assert labels==expected and len(labels)>=100
    assert all(r['winner'] in ('UP','DOWN') for r in labels)
    nonbinary=[dict(by[mid],reason='MULTIPLE_OFFICIAL_WON' if sum(str(o.get('status','')).upper()=='WON' for o in by[mid]['outcomes'])>1 else 'NO_UNIQUE_OFFICIAL_WON') for mid in ids if strict_winner(by[mid]) is None]
    assert {r['market_id'] for r in nonbinary}=={r['market_id'] for r in proof['unknown']}
    # Preserve every frozen market even when the official result cannot fit UP|DOWN.
    save(args.out/'labels/OFFICIAL_NONBINARY_RESULTS.json',dict(records=nonbinary,selection_unchanged=True,inference_used=False))
    total_events=0; total_trades=0; chain_frames=0; max_gap=0
    for r in idx['records']:
        mid=r['market_id'];p=args.out/r['public_path'];e=args.out/r['event_path']
        assert sha(p.read_bytes())==r['public_sha256'] and sha(e.read_bytes())==r['npz_sha256']
        d=read(p);assert d['market']['market_id']==mid and d['market']['asset']=='ETH'
        assert d['market']['window_end_ms']-d['market']['window_start_ms']==300000
        assert len(d['books'])==r['frames']
        for b in d['books']:
            assert type(b['received_ms']) is int and type(b['source_ms']) is int
            assert len(b['bids'])<=5 and len(b['asks'])<=5
            assert b['bids']==sorted(b['bids'],reverse=True) and b['asks']==sorted(b['asks'])
        meta=read(e.with_name('META.json'));chain_frames+=read(p.with_name('META.json'))['chain_received_later_frames']
        max_gap=max(max_gap,meta['received_max_gap_ms'])
        with np.load(e,allow_pickle=False) as z:
            ev=z['data'];times=z['local_times_ms']
            assert ev.dtype.itemsize==64 and sha(ev.tobytes())==meta['event_array_sha256']
            assert sha(times.tobytes())==meta['local_wakeup_array_sha256']
            assert np.all(np.diff(ev['local_ts'])>=0) and np.all(ev['exch_ts']==ev['local_ts'])
            assert np.all(ev['order_id']==0) and np.all(ev['ival']==0) and np.all(ev['fval']==0)
            assert np.all(np.isfinite(ev['px'])) and np.all(np.isfinite(ev['qty'])) and np.all(ev['qty']>=0)
            trades=int(np.count_nonzero((ev['ev']&255)==2));assert trades==r['public_trades']
            total_events+=len(ev);total_trades+=trades
    initial_binary=next((i for i,mid in enumerate(ids) if strict_winner(by[mid]) is None),len(ids))
    assert initial_binary>=100, 'initial chronological block must satisfy requested minimum with unique official WON'
    audit=dict(status='PASS_WITH_OFFICIAL_NONBINARY' if nonbinary else 'PASS',markets=len(ids),official_WON_labels=len(labels),official_nonbinary_results=len(nonbinary),official_nonbinary_market_ids=[r['market_id'] for r in nonbinary],initial_consecutive_unique_WON_markets=initial_binary,consecutive_windows=True,
      frames=sum(r['frames'] for r in idx['records']),events=total_events,public_trades=total_trades,
      collector_quality_counts=dict(Counter(r['collector_quality_status'] for r in cfg['records'])),
      chain_received_later_frames=chain_frames,max_received_gap_ms=max_gap,npz_readback_all='PASS',native_executed=False,
      labels_inferred=False,private_fields_exported=False)
    save(args.out/'VALIDATION.json',audit)
    manifest={p.relative_to(args.out).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p.read_bytes()))
      for p in sorted(args.out.rglob('*')) if p.is_file() and p.name!='SHA256SUMS.json'}
    save(args.out/'SHA256SUMS.json',manifest)
    print(json.dumps(dict(audit,total_bytes=sum(r['bytes'] for r in manifest.values()),files=len(manifest))),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['select','fetch','prefetch','export','verify'])
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--frozen',type=Path,required=True);parser.add_argument('--btc',type=Path,required=True)
    args=parser.parse_args()
    for k in ('repo','out','frozen','btc'):setattr(args,k,getattr(args,k).resolve())
    globals()[args.mode](args)


if __name__=='__main__':main()
