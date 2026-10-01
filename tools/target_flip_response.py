"""How does the TARGET respond when the decided favourite flips?  (read-only; uses the sanitised target export + public books, buys only: the export has no sells)
  python tools/target_flip_response.py target_fills.json.gz PUBLIC_ROOT [--labels LABELS.json]
Per market: favourite F = side with mid>=.5 at t=12 s; flip time = first t>=12 s with F mid <= .4; class from the export's winner:
NO_FLIP / FALSE_FLIP (F wins) / TRUE_FLIP (F loses).  For each class and time-since-flip window (no-flip markets use a pseudo-flip at the median real flip
time so windows are comparable) it prints Target's shares bought on F vs on the opposite side O, maker share, average price, and the per-class PnL (export, fee-free)."""
import argparse, collections, gzip, json, statistics as S
import real_replay_stop as rr

WIN = [(-1e9, -60), (-60, -20), (-20, 0), (0, 10), (10, 30), (30, 60), (60, 120), (120, 1e9)]

def wl(lo, hi): return ('<%d' % hi) if lo < -1e8 else ('>=%d' % lo) if hi > 1e8 else '%d..%d' % (lo, hi)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('export'); ap.add_argument('public_root'); a = ap.parse_args()
    rows = json.loads(gzip.open(a.export).read()); by = collections.defaultdict(list); win = {}; pnl = {}
    for r in rows: by[int(r['market_id'])].append(r); win[int(r['market_id'])] = r['winner']; pnl[int(r['market_id'])] = r['net_pnl_usdt']
    M = {m: v for m, v in rr.load(a.public_root, {}).items() if m in by}; info = {}
    for m, mk in M.items():
        b = rr.at(mk, 12.); mid = (b['best_bid'] + b['best_ask']) / 2; F = 'UP' if mid >= .5 else 'DOWN'; ft = None
        for t, x in zip(mk['ts'], mk['bs']):
            if t < 12: continue
            xm = (x['best_bid'] + x['best_ask']) / 2
            if (xm if F == 'UP' else 1 - xm) <= .4: ft = t; break
        w = win[m]; info[m] = (F, ft, 'NO_FLIP' if ft is None else 'FALSE_FLIP' if w == F else 'TRUE_FLIP')
    med = S.median([v[1] for v in info.values() if v[1] is not None]); cls = collections.Counter(v[2] for v in info.values())
    print('markets %d  classes %s  median flip time %.0f s' % (len(M), dict(cls), med))
    print('\nclass        n |  Target PnL/market (export, fee-free)')
    for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
        xs = [pnl[m] for m in M if info[m][2] == k]
        if xs: print('%-11s %3d | %8.1f  (min %.1f)' % (k, len(xs), S.fmean(xs), min(xs)))
    for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
        ms = [m for m in M if info[m][2] == k]
        if not ms: continue
        print('\n=== %s (%d markets): shares bought per market per window; F=decided favourite, O=other side ===' % (k, len(ms)))
        print('%-10s | %8s %8s | %8s %8s | %7s %7s | O/F' % ('since flip', 'F sh', 'O sh', 'F px', 'O px', 'F mkr%', 'O mkr%'))
        for lo, hi in WIN:
            acc = {s: [0., 0., 0.] for s in 'FO'}
            for m in ms:
                F, ft, _ = info[m]; st = int(rr.jl(next(iter(__import__('pathlib').Path(a.public_root).rglob('public_%d.json.gz' % m))))['market']['window_start_ms']) if False else None
            # window start from the loaded book: first book time is relative; use event_ms vs market window start via public file
            for m in ms:
                F, ft, _ = info[m]; t0 = ft if ft is not None else med; ws = start_ms(a.public_root, m)
                for r in by[m]:
                    t = (int(r['event_ms']) - ws) / 1000. - t0
                    if lo <= t < hi:
                        s = 'F' if r['side'] == F else 'O'; sh = float(r['shares']); acc[s][0] += sh; acc[s][1] += sh * float(r['price']); acc[s][2] += sh * (r['role'] == 'MAKER')
            f = lambda s, i: acc[s][i]
            print('%-10s | %8.1f %8.1f | %8.3f %8.3f | %6.0f%% %6.0f%% | %s' % (wl(lo, hi), f('F', 0) / len(ms), f('O', 0) / len(ms), f('F', 1) / max(1e-9, f('F', 0)), f('O', 1) / max(1e-9, f('O', 0)),
                  100 * f('F', 2) / max(1e-9, f('F', 0)), 100 * f('O', 2) / max(1e-9, f('O', 0)), '%.2f' % (f('O', 0) / f('F', 0)) if f('F', 0) else 'NA'))

_ws = {}
def start_ms(root, m):
    if m not in _ws:
        from pathlib import Path
        _ws[m] = int(rr.jl(next(p for p in Path(root).rglob('public_%d.json.gz' % m) if 'parity' not in str(p)))['market']['window_start_ms'])
    return _ws[m]

if __name__ == '__main__': main()
