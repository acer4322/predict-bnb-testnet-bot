"""Offline-only fixed ETH classes: received-book strict past at official +12s.

The public-book split uses favourite mid<=.40 between +12s (inclusive) and
+290s (exclusive), as real_replay_stop.classify. No outcome is sent to the
strategy. BTC retains the frozen A classifications including its two warnings.
"""
import ast
import bisect
import hashlib
import json
import lzma
from collections import Counter
from pathlib import Path

P = Path(__file__).resolve().parent
A = P / 'stage/original_maker_layer_eth861_20261002_v1r1'
tree = ast.parse((A / 'runtime/tools/run_eth_dagger60_smoke_v1.py').read_text())
ns = {'EPS': 1e-9}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                             and n.name in ('apply', 'quotes')], type_ignores=[]),
             'pure_original_book_functions', 'exec'), ns)
inputs = json.loads((A / 'INPUTS.json').read_bytes())['ETH']
rows = []
for rec in inputs:
    mid = rec['market_id']
    meta = json.loads((A / 'META' / f'{mid}.json').read_bytes())
    path = A / 'base/inputs/tapes' / f'{mid}.json.xz'
    tape = json.loads(lzma.decompress(path.read_bytes()))
    book = {'bids': {}, 'asks': {}}
    states = []
    for u in sorted(tape['updates'], key=lambda u: (int(u[1]), int(u[0]))):
        ns['apply'](book, u)
        q = ns['quotes'](book)
        if q and 0 < q['UP']['bid'] < q['UP']['ask'] < 1:
            states.append((int(u[1]), int(u[0]), (q['UP']['bid'] + q['UP']['ask']) / 2))
    st = int(meta['window_start_ms'])
    cutoff = st + 12000
    i = bisect.bisect_right([s[0] for s in states], cutoff) - 1
    fav = ('UP' if states[i][2] >= .5 else 'DOWN') if i >= 0 else None
    flip = next((s for s in states if cutoff <= s[0] < st + 290000
                 and (s[2] if fav == 'UP' else 1 - s[2]) <= .4), None) if fav else None
    winner = rec['winner']
    cls = ('UNKNOWN' if fav is None or winner is None else
           'NO_FLIP' if flip is None else 'FALSE_FLIP' if winner == fav else 'TRUE_FLIP')
    rows.append(dict(market_id=mid, cls=cls, winner=winner, favourite=fav,
                     favourite_book_received_ms=states[i][0] if i >= 0 else None,
                     favourite_book_source_ms=states[i][1] if i >= 0 else None,
                     favourite_up_mid=states[i][2] if i >= 0 else None,
                     first_flip_received_ms=flip[0] if flip else None,
                     source_tape_sha256=rec['source_tape_sha256'],
                     sanitized_public_tape_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     coverage_warning=not meta['full_window_boundaries_covered_received']))
    if len(rows) % 200 == 0:
        print(json.dumps({'classified': len(rows), 'native_executed': 0}), flush=True)
assert len(rows) == 861 and len({r['market_id'] for r in rows}) == 861
out = dict(method='ETH strict-past received public book at official +12s; F mid<=.40 in [12,290) seconds; official winner offline only; no missing/quality/outcome selection',
           records=rows, counts=dict(Counter(r['cls'] for r in rows)), native_executed=0,
           BTC_classification='frozen cloud A185 unchanged; two late-book fallbacks retained')
(P / 'ETH_CLASSIFICATION.json').write_text(json.dumps(out, indent=2, allow_nan=False))
print(json.dumps({'status': 'CLASSIFIED_ALL', 'markets': len(rows), 'counts': out['counts'], 'native_executed': 0}))
