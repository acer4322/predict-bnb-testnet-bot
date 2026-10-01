"""On the 105 markets where Target's export and public books overlap: (1) does the TARGET itself meet the user's graduation definition (class means,
bootstrap CIs, D1/D2/3x rule)? (2) is it complementary to the favourite harvest (FAV, replayed on the same books, labels = export winner)?
Mix  P = a*FAV + b*TARGET_scaled  with TARGET scaled by b (b=0..1 of its observed size); grid a in {1}, b in {0,.1,.2,.3,.5,1}.
DIAGNOSTIC ONLY: Target's PnL is a fee-free fill reconstruction and nobody can copy its fills; this tests complementarity, not a deployable strategy."""
import sys, gzip, json, random, collections, statistics as S
import real_replay_stop as rr

def ci(xs, rng, n=1000): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(n)); return ms[int(.025 * n)], ms[int(.975 * n)]

rows = json.loads(gzip.open(sys.argv[1]).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
M = {m: v for m, v in rr.load(sys.argv[2], {}).items() if m in by}
for m in M: M[m]['win'] = by[m][0]['winner']
ms = sorted(M); cl = {m: rr.classify(M[m]) for m in ms}; T = {m: by[m][0]['net_pnl_usdt'] for m in ms}; F = {m: rr.run(M[m], None, 1.0)[0] for m in ms}
rng = random.Random(21); K = ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')
def report(name, P):
    g = {k: [P[m] for m in ms if cl[m] == k] for k in K}; mn = {k: S.fmean(v) for k, v in g.items()}; rev = g['FALSE_FLIP'] + g['TRUE_FLIP']
    allp = list(P.values()); aw = S.fmean([x for x in allp if x > 0]); lo, hi = ci(allp, rng)
    print('%-22s noflip %7.1f [%6.1f,%6.1f] false %7.1f true %7.1f | overall %6.1f [%6.1f,%6.1f] | G2 %s D1 %s book %7.1f | worst %7.1f = %.2fx avgwin(%.0f)' % (name,
          mn['NO_FLIP'], *ci(g['NO_FLIP'], rng), mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(allp), lo, hi, 'Y' if ci(g['NO_FLIP'], rng)[0] > 0 else '.',
          'Y' if S.fmean(rev) >= -.5 * mn['NO_FLIP'] and mn['NO_FLIP'] > 0 else '.', 4 * mn['NO_FLIP'] + 3 * mn['FALSE_FLIP'] + 3 * mn['TRUE_FLIP'], min(allp), -min(allp) / aw, aw))
print('markets', len(ms), collections.Counter(cl.values()))
report('TARGET (observed)', T); report('FAV (replayed)', F)
print('per-market correlation FAV vs TARGET: %.2f' % (S.correlation([F[m] for m in ms], [T[m] for m in ms])))
for b in (.1, .2, .3, .5, 1.): report('FAV + %.1f*TARGET' % b, {m: F[m] + b * T[m] for m in ms})
