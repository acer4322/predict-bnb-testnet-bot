"""Bounded, read-only source extraction for Minimal student pretraining QA; no HFT."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
MIDS = (2022527, 2022538, 2022602)
FEATURES = ('predictUpMid', 'spotMinusStrikeBps', 'chainlinkMinusStrikeBps',
            'spotMinusChainlinkBps', 'spotReturn1sBps', 'spotReturn3sBps',
            'futuresReturn1sBps', 'futuresReturn3sBps', 'perpSpotBasisBps',
            'spotQueueImbalance', 'futuresQueueImbalance',
            'spotTakerImbalance1s', 'futuresTakerImbalance1s')
CODE = ('run_eth_role_separated_minimal_pair_safety_smoke.py',
        'run_eth_role_separated_multislot_v3_smoke.py',
        'run_eth_target_grounded_distinct_multislot_v2_smoke.py',
        'pair_core_asset_route_sizing_v2.py')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(262144), b''):
            h.update(block)
    return h.hexdigest()


def rows(con, sql, params):
    cur = con.execute(sql, params)
    names = [x[0] for x in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def main():
    import duckdb
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    out = (ROOT / args.output).resolve()
    if not out.is_relative_to(ROOT / '.lan_worker_v1') or out.exists():
        raise ValueError('a new task-specific .lan_worker_v1 directory is required')
    start = time.monotonic()
    source = ROOT / 'data/research/market_capsule_v1/source_bundle_50_v1'
    cap = ROOT / 'data/research/market_capsule_v1/benchmark_50_v1'
    source_manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    con = duckdb.connect(config={'threads': '1', 'memory_limit': '192MB'})
    cohort = rows(con, 'SELECT market_id,window_start_ms,window_end_ms,quality_status,'
                 'target_actions,public_snapshots,book_updates FROM read_parquet(?) '
                 'ORDER BY window_start_ms,market_id LIMIT 3', [str(cap / 'markets.parquet')])
    assert tuple(x['market_id'] for x in cohort) == MIDS
    tapes = {int(x['marketId']): x for x in source_manifest['tapes']}
    metadata = {}
    for name in ('markets.parquet', 'target_actions.parquet', 'target_parents.parquet',
                 'public_snapshots.parquet', 'book_updates.parquet'):
        p = cap / name
        metadata[name] = dict(bytes=p.stat().st_size, modifiedNs=p.stat().st_mtime_ns,
            columns=[x[0] for x in con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [str(p)]).fetchall()])
        assert p.stat().st_size < 50 * 1024**2
    blobs = {}
    for row in cohort:
        mid = row['market_id']
        actions = rows(con, 'SELECT source_leg_id,market_id,role,quote_type,side,order_hash,'
                       'event_ms,observed_at_ms,price,shares FROM read_parquet(?) '
                       'WHERE market_id=? ORDER BY event_ms,source_leg_id',
                       [str(cap / 'target_actions.parquet'), mid])
        parents = rows(con, 'SELECT market_id,asset,role,side,quote_type,order_hash,'
                       'first_event_ms,last_event_ms,average_price,shares,fill_legs FROM read_parquet(?) '
                       'WHERE market_id=? ORDER BY first_event_ms,order_hash',
                       [str(cap / 'target_parents.parquet'), mid])
        books = rows(con, 'SELECT source_ms,received_ms,best_bid,best_ask,top5_bids_json,top5_asks_json '
                     'FROM read_parquet(?) WHERE market_id=? ORDER BY received_ms,source_ms',
                     [str(cap / 'book_updates.parquet'), mid])
        for book in books:
            book['bids'] = json.loads(book.pop('top5_bids_json') or '[]')
            book['asks'] = json.loads(book.pop('top5_asks_json') or '[]')
        publics = rows(con, 'SELECT id,sampled_at_ms,timestamp_ns,archived_at_ms,snapshot_json '
                       'FROM read_parquet(?) WHERE market_id=? ORDER BY sampled_at_ms,id',
                       [str(cap / 'public_snapshots.parquet'), mid])
        for p in publics:
            s = json.loads(p.pop('snapshot_json'))
            assert int(s['marketId']) == mid
            clocks = [p['sampled_at_ms'], p['timestamp_ns'], p['archived_at_ms'],
                      s.get('sampledAtMs'), s.get('timestampNs')]
            p['available_ms'] = (max(int(clocks[0]), (int(clocks[1])+999999)//1000000,
                                    int(clocks[2]), int(clocks[3]), (int(clocks[4])+999999)//1000000)
                                 if all(x is not None and int(x)>0 for x in clocks) else None)
            p['features'] = {k: s.get(k) for k in FEATURES}
        assert len(actions) == row['target_actions'] and len(publics) == row['public_snapshots']
        assert len(books) == row['book_updates']
        assert all(len(x) <= 6000 for x in (actions, parents, books, publics))
        tape = source / tapes[mid]['file']
        tape_hash = sha(tape)
        assert tape.stat().st_size == tapes[mid]['bytes'] and tape_hash == tapes[mid]['sha256']
        bundle = dict(market=row, targetActions=actions, targetParents=parents,
                      books=books, public=publics,
                      tape=dict(bytes=tape.stat().st_size, sha256=tape_hash,
                                manifestMatch=True, contentsDecompressed=False),
                      targetObservationOnly=True, originalOrderQuantity=None,
                      ownState=None, originalTerminalState=None, zeroFillOrderUniverse=None)
        raw = json.dumps(bundle, separators=(',', ':'), allow_nan=False).encode()
        assert len(raw) <= 8*1024**2
        blobs[f'input_{mid}.json.gz'] = gzip.compress(raw, mtime=0)
    con.close()
    out.mkdir(parents=True)
    for name, blob in blobs.items():
        (out / name).write_bytes(blob)
    (out / 'source').mkdir()
    code_checks = {}
    frozen = ROOT / '.lan_worker_v1/hft244_pair_core_minimal_20260910_v1'
    for name in CODE:
        p = ROOT / 'tools' / name
        assert p.stat().st_size < 200000
        shutil.copy2(p, out / 'source' / name)
        old = frozen / name
        code_checks[name] = dict(sha256=sha(p), bytes=p.stat().st_size,
            frozenMinimalBundleMatch=(sha(old)==sha(p)) if old.exists() else None)
    assert code_checks[CODE[0]]['sha256'] == '75ca35073983175905c3bf97c6968ff41d567208d250fbe8db0691fbefbb9607'
    runner = ROOT / 'tools/run_minimal_student_preflight_worker_v1.py'
    shutil.copy2(runner, out / runner.name)
    research = ROOT / 'data/research/r4_v0/p0_provenance_v1'
    prereg = research / 'MINIMAL_STUDENT_DATA_CONTROL_PREFLIGHT_SMOKE3_PREREG_V1_20260910.md'
    shutil.copy2(prereg, out / 'PREREG.md')
    drift = research / 'TARGET_RECENT_MAKER_SHARES_RETURN_V2_20260910_1855.md'
    shutil.copy2(drift, out / 'TARGET_SIZE_DRIFT_SOURCE.md')
    files = {p.relative_to(out).as_posix(): dict(bytes=p.stat().st_size, sha256=sha(p))
             for p in out.rglob('*') if p.is_file()}
    manifest = dict(version='MINIMAL_STUDENT_PREFLIGHT_SMOKE3_V1', markets=list(MIDS),
        cohort='CONSUMED_BTC5M_BENCHMARK50_CHRONOLOGICAL_FIRST3',
        sourceManifestSha256=sha(source/'manifest.json'), sourceMetadata=metadata,
        codeChecks=code_checks, files=files, publicFeatureKeys=list(FEATURES),
        expectedRecentDrift=dict(asOfTaipei='2026-09-10 18:55',
            BTCObservedMakerCumulativeMean=33.6086627994, BTCObservedModes=[30,55],
            currentOriginalRequestedQty='UNIDENTIFIED', recentTrainingRowsUsed=0),
        limits=dict(HFT=0, training=0, live=0, fresh=0, maxThreads=4),
        preparationSeconds=time.monotonic()-start)
    (out / 'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=out.relative_to(ROOT).as_posix(), markets=list(MIDS),
        bytes=sum(x['bytes'] for x in files.values()), manifestSha256=sha(out/'MANIFEST.json'),
        preparationSeconds=manifest['preparationSeconds'])),flush=True)


if __name__ == '__main__':
    main()
