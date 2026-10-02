"""Regime switch driven by the trailing FLIP RATE (observable without labels: class by the fixed rule 'F mid<=.40 after 12 s'): for market k use the mean flip indicator of the K preceding true-labelled markets (id order;
ids have gaps, disclosed).  If trailing flip rate >= THETA -> UNDERDOG harvest (buy the non-favourite while favourite mid >= .75, 15 sh / 2 s, cap 300, stop 290, taker) else FAV (band .55-.70, buy-freeze at .40, cap 300).
Grid (pre-declared, 6): K {20,40} x THETA {.50,.60,.70}.  Walk-forward: first 60% discovery (rule: maximise discovery mean PnL), test on the last 40%.  Reports overall mean with market-bootstrap CI, class means, the EACH margin, and
the share of markets in UNDERDOG mode.  No fees."""
import sys, random, statistics as S
sys.argv = sys.argv[:3]
import fav_improve_wf as F
from fav_regime_blocks import underdog
P, M, ids, CL = F.P, F.M, F.ids, F.CL
fav = {m: F.run(m, .55, .70, 'FREEZE', .40, 300.) for m in ids}; ud = {m: (underdog(m) or 0.) for m in ids}; flip = {m: 1. if CL[m] != 'NO_FLIP' else 0. for m in ids}
rng = random.Random(7)
def policy(K, th):
    pn = {}; mode = {}
    for i, m in enumerate(ids):
        if i < K: continue
        tr = S.fmean(flip[ids[j]] for j in range(i - K, i)); mode[m] = tr >= th; pn[m] = ud[m] if mode[m] else fav[m]
    return pn, mode
def st(pn, sub):
    sub = [m for m in sub if m in pn]; g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}
    return sub, mn, S.fmean(pn[m] for m in sub), min(mn['NO_FLIP'], mn['FALSE_FLIP']) + min(0., mn['TRUE_FLIP'])
def show(tag, K, th, sub):
    pn, mode = policy(K, th); s, mn, ov, mg = st(pn, sub); bs = sorted(S.fmean(rng.choice([pn[m] for m in s]) for _ in s) for _ in range(300))
    print('%-6s K=%2d theta=%.2f | n=%3d UNDERDOG mode %3.0f%% | noflip %6.1f false %6.1f true %6.1f | overall %5.1f [%5.1f,%5.1f] | EACH margin %6.1f' % (tag, K, th, len(s), 100 * sum(mode[m] for m in s) / len(s), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], ov, bs[8], bs[291], mg))
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; CFG = [(K, th) for K in (20, 40) for th in (.5, .6, .7)]
res = sorted(CFG, key=lambda c: -st(policy(*c)[0], D)[2]); print('discovery best:'); [print('  K=%d theta=%.2f disc overall %.1f' % (*c, st(policy(*c)[0], D)[2])) for c in res[:3]]
print('TEST'); [show('top%d' % (i + 1), *c, T) for i, c in enumerate(res[:3])]
print('pure strategies on the same test markets (n=%d)' % len([m for m in T])); print('  FAV only mean %.1f | UNDERDOG only mean %.1f' % (S.fmean(fav[m] for m in T), S.fmean(ud[m] for m in T)))
print('ALL (after K)'); show('top1', *res[0], ids)
