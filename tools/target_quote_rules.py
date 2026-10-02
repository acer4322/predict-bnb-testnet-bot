"""Target's passive (maker) quoting rules from the V2.1 inferred lifecycles (old era; precise ms; one row per parent order WITH fills; placement inferred, confidence given).
Distributions: placement price vs own-side best bid at placement (join / improve / deeper), resting time, filled qty, post_action (what it did after the fill), side price level, and whether
placement offset depends on side price level or on recent fills.   usage: python tools/target_quote_rules.py target_lifecycle_features.json.gz [min_conf]"""
import sys, gzip, json, collections, statistics as S
d = json.load(gzip.open(sys.argv[1])); mc = float(sys.argv[2]) if len(sys.argv) > 2 else .0
d = [r for r in d if (r.get('confidence') or 0) >= mc]; print('rows', len(d), 'markets', len({r['market_id'] for r in d}), 'min confidence', mc)
off = collections.Counter(); w = collections.Counter()
for r in d:
    o = round((r['best_bid_at_placement'] - r['price']) * 100) if r['best_bid_at_placement'] is not None else None
    k = 'NA' if o is None else ('improve %d' % -o if o < 0 else 'join (at bid)' if o == 0 else 'deeper %d' % o if o <= 5 else 'deeper >5')
    off[k] += 1; w[k] += r['filled_qty']
tot = sum(w.values()); print('placement vs best bid (share of filled qty):', {k: '%.0f%%' % (100 * v / tot) for k, v in w.most_common(10)})
rt = sorted(r['resting_ms'] for r in d if r['resting_ms'] is not None); print('resting ms q10/50/90: %d %d %d' % (rt[len(rt) // 10], rt[len(rt) // 2], rt[9 * len(rt) // 10]))
fq = sorted(r['filled_qty'] for r in d); print('filled qty per parent q10/50/90: %.1f %.1f %.1f' % (fq[len(fq) // 10], fq[len(fq) // 2], fq[9 * len(fq) // 10]))
pa = collections.Counter(r['post_action'] for r in d); print('post_action:', dict(pa.most_common(12)))
lv = collections.defaultdict(lambda: collections.Counter())
for r in d:
    if r['best_bid_at_placement'] is None: continue
    o = round((r['best_bid_at_placement'] - r['price']) * 100); b = min(int(r['price'] * 10), 9); lv[b]['n'] += 1; lv[b]['join'] += o == 0; lv[b]['deep'] += o > 0; lv[b]['imp'] += o < 0; lv[b]['q'] += r['filled_qty']
print('by own-side price decile: n | join% | deeper% | improve% | filled qty')
for b in sorted(lv): c = lv[b]; print('  %.1f-%.1f %5d | %3.0f%% | %3.0f%% | %3.0f%% | %.0f' % (b / 10, b / 10 + .1, c['n'], 100 * c['join'] / c['n'], 100 * c['deep'] / c['n'], 100 * c['imp'] / c['n'], c['q']))
mv = collections.Counter()
for r in d:
    if r['best_bid_at_placement'] is None or r['best_bid_at_fill'] is None: continue
    x = round((r['best_bid_at_fill'] - r['best_bid_at_placement']) * 100); mv['bid fell' if x < 0 else 'bid unchanged' if x == 0 else 'bid rose'] += r['filled_qty']
t = sum(mv.values()); print('own-side best bid from placement to fill (share of filled qty):', {k: '%.0f%%' % (100 * v / t) for k, v in mv.items()})
mk = [(r['mid_side_at_fill_plus5s'] - r['price'], r['filled_qty']) for r in d if r.get('mid_side_at_fill_plus5s') is not None]
print('+5 s markout (mid - price), qty-weighted: %+.4f' % (sum(a * b for a, b in mk) / sum(b for _, b in mk)))
