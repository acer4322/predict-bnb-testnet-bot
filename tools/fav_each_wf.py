"""FAV improvement under the CORRECTED standard (user, 2026-10-02: markets are not double counted, class means cannot be added):
  pass iff NO_FLIP mean > 0, FALSE_FLIP mean > 0, and the loss class (TRUE_FLIP) has |mean| < min(NO_FLIP mean, FALSE_FLIP mean).  margin = min(noflip, false) - |true|  (>0 passes).
Idea tested: lower average entry price -> smaller loss per share when F loses, larger gain per share when F wins.  Grid (pre-declared, 20): band {(.50,.58),(.50,.62),(.52,.60),(.50,.66),(.55,.65)} x
FREEZE threshold {.40,.45} x cap {150,300} (fill rules as fav_improve_wf).  Walk-forward: first 60% discovery; rule: maximise discovery margin subject to NO_FLIP>0 and FALSE_FLIP>0; selected + top-3 on the last 40% with
market-bootstrap CI of the margin, plus the frequency-weighted overall mean (expectation) for every row."""
import sys, random, statistics as S
sys.argv = sys.argv[:3]
import fav_improve_wf as F
ids, CL, run = F.ids, F.CL, F.run
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; rng = random.Random(71)
CFG = [(b[0], b[1], 'FREEZE', th, c) for b in ((.50, .58), (.50, .62), (.52, .60), (.50, .66), (.55, .65)) for th in (.40, .45) for c in (150., 300.)]
PN = {c: {m: run(m, *c) for m in ids} for c in CFG}
def stats(c, sub):
    g = {k: [PN[c][m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}; n = sum(len(v) for v in g.values())
    exp = sum(mn[k] * len(g[k]) / n for k in g); return mn, mn['TRUE_FLIP'], min(mn['NO_FLIP'], mn['FALSE_FLIP']) + mn['TRUE_FLIP'] if mn['TRUE_FLIP'] < 0 else min(mn['NO_FLIP'], mn['FALSE_FLIP']), exp
def label(c): return 'band %.2f-%.2f FREEZE@%.2f cap%d' % (c[0], c[1], c[3], c[4])
res = []
for c in CFG:
    mn, tr, mg, ex = stats(c, D); res.append((c, mn, mg, ex))
ok = sorted([r for r in res if r[1]['NO_FLIP'] > 0 and r[1]['FALSE_FLIP'] > 0], key=lambda r: -r[2]); print('discovery: %d/%d configs have noflip>0 and false>0; EACH margin>0 for %d' % (len(ok), len(CFG), sum(r[2] > 0 for r in ok)))
for r in sorted(res, key=lambda r: -r[2])[:5]: print('  disc %-34s noflip %6.1f false %6.1f true %6.1f | margin %6.1f | expectation %5.1f' % (label(r[0]), r[1]['NO_FLIP'], r[1]['FALSE_FLIP'], r[1]['TRUE_FLIP'], r[2], r[3]))
def show(tag, c, sub):
    mn, tr, mg, ex = stats(c, sub); bs = sorted(stats(c, [rng.choice(sub) for _ in sub])[2] for _ in range(300))
    print('%-8s %-34s noflip %6.1f false %6.1f true %6.1f | margin %6.1f [%6.1f,%6.1f] | expectation %5.1f | %s' % (tag, label(c), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], mg, bs[8], bs[291], ex, 'EACH PASS' if mg > 0 and mn['NO_FLIP'] > 0 and mn['FALSE_FLIP'] > 0 else 'fail'))
print('\nTEST (last 40%%, n=%d)' % len(T))
for i, r in enumerate(sorted(res, key=lambda r: -r[2])[:4]): show('top%d' % (i + 1), r[0], T)
show('ref', (.55, .70, 'FREEZE', .40, 300.), T) if (.55, .70, 'FREEZE', .40, 300.) in PN else None
print('\nALL 519:'); [show('top%d' % (i + 1), r[0], ids) for i, r in enumerate(sorted(res, key=lambda r: -r[2])[:3])]
