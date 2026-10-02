"""Regime kill-switch (pre-declared): trade the FREEZE harvest (dec12, band .55-.70) on market k only if the mean PnL of the previous W markets that WERE simulated (paper PnL, always computed)
is > 0; otherwise stay out (PnL 0).  W in {30, 60, 100}.  Evaluated over all markets after the first W with the fixed criteria; reference = always on over the same markets."""
import sys, random, statistics as S
sys.argv = sys.argv[:3]
import entry_timing_wf as E
from toy_graduation import ci_mean
c = (12, (.55, .70), 'none', 'FREEZE'); pn = {m: E.run(m, c[0], *c[1], c[2], c[3]) for m in E.ids}; ids = E.ids; rng = random.Random(2)
for W in (30, 60, 100):
    on = []; out = []; ref = []
    for i in range(W, len(ids)):
        m = ids[i]; paper = S.fmean(pn[ids[j]] for j in range(i - W, i)); on.append(paper > 0); ref.append(pn[m]); out.append(pn[m] if paper > 0 else 0.)
    lo, hi = ci_mean(out, rng, 1000); rl, rh = ci_mean(ref, rng, 1000); h = len(out) // 2
    print('W=%3d: switch ON %3.0f%% of markets | overall %6.1f [%6.1f,%6.1f] (always-on %6.1f [%6.1f,%6.1f]) | 1st half %6.1f 2nd half %6.1f | worst %.0f' % (W, 100 * sum(on) / len(on), S.fmean(out), lo, hi, S.fmean(ref), rl, rh, S.fmean(out[:h]), S.fmean(out[h:]), min(out)))
