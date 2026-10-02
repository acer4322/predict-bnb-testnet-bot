"""Export public-only HFT fixtures without importing or executing a native engine.

The original converter functions are compiled unchanged, with NumPy and the
original event dtype/constants. Only imports/top-level engine loading are
omitted. Sanitized match sort keys preserve equal-hash groups and lexical order.
Every full corrected event-array byte and local wakeup time must match before
writing a fixture. Existing native clocks are copied, never regenerated here.
"""
from __future__ import annotations

import argparse
import ast
import __future__
import bisect
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import lzma
from pathlib import Path
import shutil
import statistics
import tempfile
from typing import Any
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, obj):
    data = json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == '.gz': data = gzip.compress(data, mtime=0)
    if path.suffix == '.xz': data = lzma.compress(data)
    path.write_bytes(data)


def pure_converter(source, types):
    """Use the frozen Python conversion definitions, with no Rust/native import."""
    ns = dict(np=np, Path=Path, Any=Any, datetime=datetime, timezone=timezone,
              bisect=bisect, defaultdict=defaultdict, json=json, lzma=lzma, statistics=statistics,
              ROOT=ROOT, ARCHIVE_DIR=ROOT, OUT_DIR=ROOT,
              EVENT_ORDER_IMPL='frozen_python_definitions_no_native_import')
    names = {'DEPTH_EVENT', 'DEPTH_SNAPSHOT_EVENT', 'TRADE_EVENT', 'EXCH_EVENT',
             'LOCAL_EVENT', 'BUY_EVENT', 'SELL_EVENT', 'event_dtype'}
    nodes = [n for n in ast.parse(types.read_text(encoding='utf8')).body
             if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(types), 'exec',
                 flags=__future__.annotations.compiler_flag), ns)
    if source.name == 'hftbacktest_execution_tape_feed_v1.py':
        # Exact historical feed V1, including its original event_row and match
        # normalization definitions. This loads no native module or network code.
        ex = SimpleNamespace(**{k: ns[k] for k in names})
        deps = {}
        for filename, selected in (
            ('hftbacktest_execution_shift_audit_v0.py', {'event_row'}),
            ('hftbacktest_true_match_calibration_v0.py', {'iso_ms','wei','normalize_match'}),
        ):
            path = source.parent/filename
            nodes = [n for n in ast.parse(path.read_text(encoding='utf8')).body
                     if isinstance(n, ast.FunctionDef) and n.name in selected]
            exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec',
                         flags=__future__.annotations.compiler_flag),ns)
            deps[filename] = sha(path.read_bytes())
        ex.event_row = ns['event_row']
        ns['ex'] = ex
        ns['tm'] = SimpleNamespace(normalize_match=ns['normalize_match'])
        ns['_normalize_match'] = ns['normalize_match']
        ns['load_archive'] = lambda p: json.loads(lzma.decompress(p.read_bytes()).decode('utf8'))
        ns['_dependency_sha256'] = deps
    nodes = [n for n in ast.parse(source.read_text(encoding='utf8')).body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec',
                 flags=__future__.annotations.compiler_flag), ns)
    return ns


def sanitize_tape(original):
    rows = original.get('matches') or []
    # A sort key only: replacing each distinct raw string by its lexical rank
    # preserves ties, duplicate records, price/quantity ordering and stable order.
    ranks = {h: i for i, h in enumerate(sorted({str(r.get('transactionHash') or '') for r in rows}))}
    matches = []
    for r in rows:
        taker = r.get('taker') if isinstance(r.get('taker'), dict) else {}
        outcome = taker.get('outcome') if isinstance(taker.get('outcome'), dict) else {}
        matches.append(dict(executedAt=r.get('executedAt'), priceExecuted=r.get('priceExecuted'),
                            amountFilled=r.get('amountFilled'),
                            taker=dict(outcome=dict(name=outcome.get('name')), quoteType=taker.get('quoteType')),
                            transactionHash='ANONYMOUS_SORT_%08d' % ranks[str(r.get('transactionHash') or '')]))
    allowed_market = ('market_id', 'title', 'decimal_precision', 'first_seen_ms', 'window_end_ms', 'status')
    return dict(version=original['version'], marketId=original['marketId'],
                market={k: v for k, v in original['market'].items() if k in allowed_market},
                schema={**original['schema'], 'executionMeta': '[sourceMs,receivedMs,orderCount,redactedLastOrderSettled,redactedSettlementsPending]'},
                updates=original['updates'],
                executionMeta=[list(r[:3])+[None, None] for r in original.get('executionMeta', [])], matches=matches,
                anonymization='transactionHash values are anonymous lexical sort ranks, not transaction identifiers; maker/account/order fields omitted; executionMeta private order dictionaries redacted. Converter event-array and wakeup parity checked.')


def export(package, returns, out, source, types, markets):
    ns = pure_converter(source, types)
    reports = []
    for mid in markets:
        dest = out/'fixtures'/str(mid)
        if dest.exists(): raise RuntimeError('fixture already exists: preserve prior export')
        raw_path = package/'base/inputs/tapes'/f'{mid}.json.xz'
        raw = raw_path.read_bytes(); original = json.loads(lzma.decompress(raw))
        clean = sanitize_tape(original)
        with tempfile.TemporaryDirectory(prefix='hft244_public_fixture_') as task_temp:
            temp = Path(task_temp); dump(temp/f'{mid}.json.xz', clean)
            if source.name == 'hftbacktest_execution_tape_feed_v1.py':
                ns['ARCHIVE_DIR'] = raw_path.parent
                original_events, original_times, original_info = ns['build_archive_events'](mid)
                ns['ARCHIVE_DIR'] = temp
                events, times, info = ns['build_archive_events'](mid)
            else:
                original_events, original_times, original_info = ns['build_archive_events'](mid, archive_dir=raw_path.parent)
                events, times, info = ns['build_archive_events'](mid, archive_dir=temp)
            assert events.dtype == original_events.dtype and events.shape == original_events.shape
            assert events.tobytes() == original_events.tobytes(), ('event parity failed', mid)
            assert times == original_times, ('wakeup parity failed', mid)
            dest.mkdir(parents=True)
            shutil.copy2(temp/f'{mid}.json.xz', dest/f'{mid}.json.xz')
        pub_path = package/'base/inputs'/f'public_{mid}.json.gz'
        public = json.loads(gzip.decompress(pub_path.read_bytes()))
        # Original metadata is retained separately; replay pointer targets the
        # sanitized archive. Book/frame values are untouched.
        shutil.copy2(pub_path, dest/f'public_original_{mid}.json.gz')
        public['tape'] = dict(file=f'{mid}.json.xz', sha256=sha((dest/f'{mid}.json.xz').read_bytes()))
        dump(dest/f'public_{mid}.json.gz', public)
        np.savez_compressed(dest/'events.npz', data=events, local_times_ms=np.asarray(times, dtype=np.int64))
        trades = []
        for seq, r in enumerate(clean['matches'], 1):
            norm = ns['_normalize_match'](r)
            if norm is None: continue
            trades.append({k: norm[k] for k in ('executedAt', 'tsMs', 'qty', 'outcome', 'quoteType', 'outcomePrice', 'nativeYesPrice', 'nativeAggressor')} | dict(anonymous_sequence=seq, market_id=mid))
        dump(dest/'observed_market_trades.json.gz', dict(records=trades,
             scope='Observed public market matches; no OUR order/fill linkage. Exact subsecond timing may be unavailable; engine mid-second offsets and mapped receive lag are assumptions.'))
        clocks = []
        for arm in ('CG1AT', 'FULL'):
            src = returns/'arms'/f'c100_{arm}_{mid}'
            if not src.exists(): raise RuntimeError('missing golden path '+str(src))
            gold = dest/'golden'/arm; gold.mkdir(parents=True)
            for name in ('execution_clock.json', 'result.json', 'clock_trace.json.gz', 'AUDIT.json', 'EXECUTION.json', 'PROCESS.json'):
                if not (src/name).exists(): raise RuntimeError('missing golden '+str(src/name))
                shutil.copy2(src/name, gold/name)
            result = json.loads((src/'result.json').read_text(encoding='utf8'))
            audit = json.loads((src/'AUDIT.json').read_text(encoding='utf8'))
            clocks.append(dict(arm=arm, status=result.get('status'), failed_checks=audit.get('failed_checks'),
                               original_execution_clock_sha256=sha((src/'execution_clock.json').read_bytes())))
        report = dict(market_id=mid, original_tape_sha256=sha(raw), sanitized_tape_sha256=sha((dest/f'{mid}.json.xz').read_bytes()),
                      original_public_sha256=sha(pub_path.read_bytes()),
                      events=len(events), event_dtype=events.dtype.descr, event_itemsize=events.dtype.itemsize,
                      event_array_sha256=sha(events.tobytes()), event_array_and_wakeup_parity='PASS',
                      local_wakeups=len(times), original_matches=len(original.get('matches') or []),
                      observed_trade_records=len(trades), source_converter_sha256=sha(source.read_bytes()),
                      converter_dependency_sha256=ns.get('_dependency_sha256', {}),
                      types_source_sha256=sha(types.read_bytes()), golden=clocks,
                      native_executed=False, anonymized=True)
        # Archive filename/path differs after anonymization; retain numeric and
        # semantic clock diagnostics, with no local account or host path strings.
        report['conversion_info'] = {k: v for k, v in info.items() if k not in ('archive', 'path', 'source', 'archivePath')}
        dump(dest/'FIXTURE.json', report); reports.append(report)
        print(json.dumps({'market_id':mid, 'events':len(events), 'event_array_and_wakeup_parity':'PASS', 'golden_paths':len(clocks)}), flush=True)
    dump(out/'FIXTURES.json', dict(records=reports, native_executed=False))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--package',type=Path,required=True);p.add_argument('--returns',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--converter',type=Path,required=True)
    p.add_argument('--types',type=Path,required=True);p.add_argument('--markets',nargs='+',type=int,required=True)
    a=p.parse_args();export(a.package,a.returns,a.out,a.converter,a.types,a.markets)


if __name__=='__main__': main()
