"""Standard, reading R (extended strict reading): every LOSING class's |mean| must be smaller than EVERY positive class's mean (positive classes only; markets are never added across classes).
margin_R = min(positive class means) - max(|negative class means|); pass iff margin_R > 0 and at least one class is positive.  When exactly one class loses this is the user's EACH reading.
Uses the 40-config FAV grid of fav_improve_wf (band x response x cap).  Walk-forward as before (discovery first 60%, rule: maximise discovery margin_R, then test on the last 40% with market-bootstrap CI)."""
import sys, random, statistics as S
sys.argv = sys.argv[:3]
import fav_improve_wf as F
ids, CL, CFG, PN, D, T, label = F.ids, F.CL, F.CFG, F.PN, F.D, F.T, F.label
rng = random.Random(81)
def mR(mn):
    pos = [v for v in mn.values() if v > 0]; neg = [-v for v in mn.values() if v < 0]
    if not pos: return -999.
    return min(pos) - max(neg) if neg else min(pos)
def show(tag, c, sub):
    mn, _ = F.gm(PN[c], sub); bs = sorted(mR(F.gm({m: PN[c][m] for m in sub}, [rng.choice(sub) for _ in sub])[0]) for _ in range(300))
    n = len(sub); w = {k: sum(CL[m] == k for m in sub) / n for k in mn}
    print('%-8s %-38s noflip %6.1f false %6.1f true %6.1f | margin_R %6.1f [%6.1f,%6.1f] | expectation %5.1f | %s' % (tag, label(c), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], mR(mn), bs[8], bs[291], sum(mn[k] * w[k] for k in mn), 'R PASS' if mR(mn) > 0 else 'fail'))
res = sorted([(c, mR(F.gm(PN[c], D)[0])) for c in CFG], key=lambda x: -x[1])
print('discovery: R passes %d/%d' % (sum(m > 0 for _, m in res), len(CFG)))
for c, m in res[:4]: print('  disc %-38s margin_R %6.1f' % (label(c), m))
print('\nTEST (last 40%%, n=%d)' % len(T)); [show('top%d' % (i + 1), c, T) for i, (c, _) in enumerate(res[:4])]
print('\nALL 519'); [show('top%d' % (i + 1), c, ids) for i, (c, _) in enumerate(res[:3])]
