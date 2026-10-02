"""Is the Target profitable at all, and what does it cost?  105-market export (buys only, fee-free PnL).  Bootstrap CI of PnL/market, PnL per $ traded, volume per market,
estimated taker fees under the local (UNVERIFIED) fee model fee/share = rate*min(p,1-p) with rate 2%, and the maker share; split halves.
usage: python tools/target_economics.py target_fills.json.gz"""
import sys, gzip, json, collections, random, statistics as S
rows = json.loads(gzip.open(sys.argv[1]).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
rng = random.Random(2); ids = sorted(by)
def ci(x): b = sorted(S.fmean(rng.choice(x) for _ in x) for _ in range(4000)); return b[100], b[3899]
def summ(sub, tag):
    pnl = [by[m][0]['net_pnl_usdt'] for m in sub]; vol = [sum(r['shares'] * r['price'] for r in by[m]) for m in sub]
    tf = [sum(r['shares'] * .02 * min(r['price'], 1 - r['price']) for r in by[m] if r['role'] == 'TAKER') for m in sub]
    mk = sum(r['shares'] for m in sub for r in by[m] if r['role'] == 'MAKER') / sum(r['shares'] for m in sub for r in by[m])
    lo, hi = ci(pnl); net = [a - b for a, b in zip(pnl, tf)]; lo2, hi2 = ci(net)
    print('%-12s n=%3d PnL/mkt %+.1f CI[%+.1f,%+.1f] | $ traded/mkt %.0f | PnL per $ %+.4f | maker share %.0f%% | est. taker fee/mkt %.1f -> net %+.1f CI[%+.1f,%+.1f]' % (tag, len(sub), S.fmean(pnl), lo, hi, S.fmean(vol), sum(pnl) / sum(vol), 100 * mk, S.fmean(tf), S.fmean(net), lo2, hi2))
summ(ids, 'ALL'); summ(ids[:len(ids) // 2], 'first half'); summ(ids[len(ids) // 2:], 'second half')
