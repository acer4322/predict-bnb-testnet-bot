"""Existing public tape -> frozen HFT feed V1 NPZ, no native imports/replay.

Selection is external/frozen; no outcome or Target actions enter the arrays.
Original/anonymous conversion and NPZ readback are checked for every market.
Only numeric event/wakeup arrays and public source metadata are published.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import importlib.util
import json
import lzma
from pathlib import Path
import re
import shutil
import sys
sys.dont_write_bytecode = True

import numpy as np


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode()
    path.write_bytes(raw+b'\n')


def load_helper(bundle):
    path=bundle/'utilities/export_hft244_repro_samples.py'
    spec=importlib.util.spec_from_file_location('frozen_public_converter_helper',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    source=bundle/'strategy/runtime_scratch_CG1AT_2671717/tools/hftbacktest_execution_tape_feed_v1.py'
    types=bundle/'engine/patched/py-hftbacktest/hftbacktest/types.py'
    return mod,mod.pure_converter(source,types)


def window(tape):
    meta=tape['market'];title=str(meta.get('title') or '')
    # Same metadata-only title semantics as the already-published graduation
    # exporter. Predict sometimes omits the first AM/PM or :00 in its title.
    # This does not change frozen event conversion or inspect any outcomes.
    if not title.startswith('Bitcoin Up or Down - '): raise ValueError('not BTC')
    match=re.search(r'(\d{1,2})(?::(\d{2}))?(AM|PM)?\s*-\s*(\d{1,2})(?::(\d{2}))?(AM|PM)\s+ET',title,re.I)
    if not match:raise ValueError('missing title interval; BTC5M UNKNOWN')
    h1,m1,a1,h2,m2,a2=match.groups()
    minute=lambda h,m,a:(int(h)%12+(12 if a.upper()=='PM' else 0))*60+int(m or 0)
    end_min=minute(h2,m2,a2)
    if not any((end_min-minute(h1,m1,a))%1440==5 for a in ([a1] if a1 else ['AM','PM'])):
        raise ValueError('not a five-minute title interval')
    end=int(meta['window_end_ms']);return end-300_000,end


def build_one(ns, helper, tape_path, mid, expected_sha=None):
    raw=tape_path.read_bytes();source_sha=digest(raw)
    if expected_sha is not None and source_sha!=expected_sha:
        raise RuntimeError('SOURCE_CHANGED_AFTER_SELECTION')
    tape=json.loads(lzma.decompress(raw));assert int(tape['marketId'])==mid
    start,end=window(tape)
    assert tape['version']=='PREDICT_EXECUTION_TAPE_ARCHIVE_V1'
    assert isinstance(tape.get('updates'),list) and isinstance(tape.get('matches'),list)
    # Read a single immutable byte snapshot. Only load_archive's I/O is replaced;
    # original feed/event_row/normalize_match functions execute unchanged.
    ns['ARCHIVE_DIR']=tape_path.parent
    ns['load_archive']=lambda _: tape
    original,times,info=ns['build_archive_events'](mid,trade_offset='mid')
    clean=helper.sanitize_tape(tape)
    ns['load_archive']=lambda _: clean
    events,anon_times,anon_info=ns['build_archive_events'](mid,trade_offset='mid')
    assert events.dtype==original.dtype and events.shape==original.shape
    assert events.tobytes()==original.tobytes() and times==anon_times
    assert info['normalizedTrades']>0 and info['updates']>0
    assert events.dtype.itemsize==64 and set(events.dtype.names)=={'ev','exch_ts','local_ts','px','qty','order_id','ival','fval'}
    assert np.all(events['order_id']==0) and np.all(events['ival']==0) and np.all(events['fval']==0)
    assert np.all(np.isfinite(events['px'])) and np.all(np.isfinite(events['qty']))
    assert np.all(events['qty']>=0)
    assert np.all(np.diff(events['local_ts'])>=0)
    assert np.all(events['exch_ts']==events['local_ts'])
    trade=(events['ev'] & np.uint64(0xff))==np.uint64(ns['TRADE_EVENT'])
    assert int(np.count_nonzero(trade))==int(info['normalizedTrades'])
    updates=tape['updates'];recv=[int(x[1]) for x in updates];source=[int(x[0]) for x in updates]
    checkpoint=next(x for x in sorted(updates,key=lambda r:(int(r[1]),int(r[0])))
                    if int(x[3])==1 and x[4] is not None and x[5] is not None)
    normalized=[ns['_normalize_match'](x) for x in tape['matches']]
    observed_times=[int(x['tsMs']) for x in normalized if x is not None]
    market_mismatches=[x for x in tape['matches'] if isinstance(x.get('market'),dict)
                       and x['market'].get('id') is not None and int(x['market']['id'])!=mid]
    assert not market_mismatches, 'public match market ID differs from archive'
    info={k:v for k,v in info.items() if k!='archivePath'}
    metadata=dict(market_id=mid,title=tape['market']['title'],window_start_ms=start,window_end_ms=end,
      window_start_derivation='window_end_ms - 300000, after title BTC + five-minute interval validation',
      archive_status=tape['market'].get('status'),source_tape_sha256=source_sha,source_tape_bytes=len(raw),
      source_min_ms=min(source),source_max_ms=max(source),received_min_ms=min(recv),received_max_ms=max(recv),
      first_full_checkpoint_source_ms=int(checkpoint[0]),first_full_checkpoint_received_ms=int(checkpoint[1]),
      observed_source_start_missing_ms=max(0,int(checkpoint[0])-start),observed_source_tail_missing_ms=max(0,end-max(source)),
      source_max_gap_ms=max(np.diff(sorted(set(source))).tolist(),default=0),
      source_gaps_over_5000_ms=int(np.count_nonzero(np.diff(sorted(set(source)))>5000)),
      full_window_boundaries_covered_source=(int(checkpoint[0])<=start and max(source)>=end),
      first_public_trade_ms=min(observed_times),last_public_trade_ms=max(observed_times),
      public_trades_before_window=sum(t<start for t in observed_times),
      public_trades_within_window=sum(start<=t<=end for t in observed_times),
      public_trades_after_window=sum(t>end for t in observed_times),
      raw_match_market_id_mismatch_count=len(market_mismatches),
      observed_received_start_missing_ms=max(0,int(checkpoint[1])-start),observed_received_tail_missing_ms=max(0,end-max(recv)),
      received_max_gap_ms=max(np.diff(sorted(set(recv))).tolist(),default=0),
      full_window_boundaries_covered_received=(int(checkpoint[1])<=start and max(recv)>=end),
      public_trade_collection_completeness='UNKNOWN: archive has no complete pagination/venue-matching certificate',
      source_clock_unit='Unix ms',event_clock_unit='Unix ns',event_dtype=events.dtype.descr,
      event_itemsize=64,event_array_sha256=digest(events.tobytes()),local_wakeup_count=len(times),
      local_wakeup_array_sha256=digest(np.asarray(times,dtype=np.int64).tobytes()),
      anonymous_sort_parity='PASS',native_executed=False,conversion=info)
    return events,np.asarray(times,dtype=np.int64),metadata


def setup_source(out,bundle):
    source=bundle/'strategy/runtime_scratch_CG1AT_2671717/tools'
    mapping=[(bundle/'utilities/export_hft244_repro_samples.py','converter_source/export_hft244_repro_samples.py'),
             (bundle/'engine/patched/py-hftbacktest/hftbacktest/types.py','converter_source/types.py')]
    mapping += [(source/name,'converter_source/'+name) for name in (
        'hftbacktest_execution_tape_feed_v1.py','hftbacktest_execution_shift_audit_v0.py','hftbacktest_true_match_calibration_v0.py')]
    for src,rel in mapping:
        dest=out/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
    shutil.copy2(Path(__file__),out/'convert_batch.py')
    save(out/'CONVERTER_SOURCE_SHA256.json',{rel:digest(src.read_bytes()) for src,rel in mapping})


def verify_reference(bundle):
    helper,ns=load_helper(bundle)
    for mid in (2671717,2671719,2671768):
        folder=bundle/'fixtures'/str(mid)
        events,times,meta=build_one(ns,helper,folder/f'{mid}.json.xz',mid)
        with np.load(folder/'events.npz',allow_pickle=False) as saved:
            assert events.tobytes()==saved['data'].tobytes()
            assert times.tobytes()==saved['local_times_ms'].tobytes()
    print(json.dumps(dict(reference_markets=3,reference_parity='PASS',native_executed=False)),flush=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--selection',type=Path)
    p.add_argument('--tapes',type=Path)
    p.add_argument('--out',type=Path)
    p.add_argument('--check-reference-only',action='store_true')
    a=p.parse_args();verify_reference(a.bundle)
    if a.check_reference_only:return
    assert a.selection and a.tapes and a.out
    selection=json.loads(a.selection.read_text());assert 1<=len(selection['markets'])<=100
    if a.out.exists():raise RuntimeError('output exists; preserve prior batch')
    a.out.mkdir(parents=True);setup_source(a.out,a.bundle)
    shutil.copy2(a.selection,a.out/'SELECTION.json')
    helper,ns=load_helper(a.bundle);records=[];errors=[]
    for row in selection['markets']:
        mid=int(row['market_id']);dest=a.out/'markets'/str(mid)
        try:
            ev,times,meta=build_one(ns,helper,a.tapes/f'{mid}.json.xz',mid,row['source_sha256'])
            dest.mkdir(parents=True,exist_ok=False)
            with (dest/'events.npz').open('xb') as f:np.savez_compressed(f,data=ev,local_times_ms=times)
            with np.load(dest/'events.npz',allow_pickle=False) as disk:
                assert disk['data'].dtype==ev.dtype and disk['data'].tobytes()==ev.tobytes()
                assert disk['local_times_ms'].tobytes()==times.tobytes()
            meta.update(target_exports=row.get('target_exports',[]),selection_group=row['selection_group'],
                        npz_sha256=digest((dest/'events.npz').read_bytes()),npz_bytes=(dest/'events.npz').stat().st_size,
                        npz_readback_parity='PASS')
            save(dest/'META.json',meta)
            records.append(dict(market_id=mid,path=f'markets/{mid}/events.npz',**{k:meta[k] for k in (
                'npz_sha256','npz_bytes','event_array_sha256','local_wakeup_array_sha256','target_exports','selection_group',
                'full_window_boundaries_covered_received')},events=len(ev),public_trades=meta['conversion']['normalizedTrades']))
            print(json.dumps(dict(market_id=mid,status='PASS',events=len(ev),public_trades=meta['conversion']['normalizedTrades'],bytes=meta['npz_bytes'])),flush=True)
        except Exception as exc:
            errors.append(dict(market_id=mid,status='FAILED',error=type(exc).__name__+': '+str(exc)))
            print(json.dumps(errors[-1]),flush=True)
    save(a.out/'INDEX.json',dict(version='HFT244_PUBLIC_FEED_V1_NPZ_BATCH_20261002_V1',
        records=records,failed=errors,markets=len(records),native_executed=False,model_fits=0,
        source_repro_commit='3bf09cf645e1d83284825c32196e483294fcd182',
        feed_clock='V1 receivedMs depth exchange/local; true public matches executedAt + mid half-second offset',
        target_membership='Offline cohort tag only; Target actions/labels absent from event arrays'))
    if errors:raise RuntimeError(f'{len(errors)} failed conversions; do not publish incomplete output silently')


if __name__=='__main__':main()
