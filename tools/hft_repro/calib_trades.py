import sys, json, gzip, math
sys.path.insert(0, sys.argv[1] + '/strategy/runtime_scratch_CG1AT_2671717'); sys.path.insert(0, sys.argv[1] + '/strategy/runtime_scratch_CG1AT_2671717/src')
sys.path.insert(0, sys.argv[3])
import tools.hftbacktest_execution_shift_audit_v0 as ex
from synth_events import build, load_books
tot_r = tot_s = 0.; 
for mid in (2671717, 2671719, 2671768):
    fx = f'{sys.argv[1]}/fixtures/{mid}'; mk, bk = load_books(f'{fx}/public_original_{mid}.json.gz'); ws = int(mk['window_start_ms'])
    arr, inf = build(bk, ex, alpha=1.0)
    real = [r for r in json.load(gzip.open(f'{fx}/observed_market_trades.json.gz'))['records'] if ws <= r['tsMs'] < ws + 300_000]
    rq = sum(r['qty'] for r in real); sq = sum(q for _, _, q, _ in inf)
    # per-10s bins correlation
    B = lambda ts: int((ts - ws) // 10_000)
    rb = [0.] * 30; sb = [0.] * 30
    for r in real: rb[min(29, B(r['tsMs']))] += r['qty']
    for ts, p, q, s in inf: sb[min(29, max(0, B(ts)))] += q
    mr, ms = sum(rb) / 30, sum(sb) / 30; cov = sum((a - mr) * (b - ms) for a, b in zip(rb, sb)); vr = sum((a - mr) ** 2 for a in rb); vs = sum((b - ms) ** 2 for b in sb)
    print(mid, 'real trades %d qty %.0f | inferred events %d qty(alpha=1) %.0f | ratio real/inferred %.2f | corr(10s bins) %.2f | depth events %d' % (len(real), rq, len(inf), sq, rq / sq if sq else float('nan'), cov / math.sqrt(vr * vs) if vr * vs else float('nan'), len(arr) - len(inf)))
    tot_r += rq; tot_s += sq
print('pooled ratio real/inferred(alpha=1): %.3f' % (tot_r / tot_s))
