"""Read-only: how much does POSTFLIP_RISK_FLOOR block orders after the first FLIP, and what does it lock in?

Per path (a collected CG1AT/V58 result directory) it reads:
  risk_floor_trace.json.gz   decisions/frames of the post-FLIP reserved-worst-floor guard
  result.json                v12g events (DECIDE/FLIP), v63_confirm m0, terminal_worst, final inventory/cost
  clock_trace.json.gz        (optional) plans/intent rows, for orders born after the first FLIP and V8 unserved deficit
Settlement labels are OFFLINE ONLY and optional (--labels): they are used to split no-flip / false-flip / true-flip and to
score the path; nothing here feeds back into any strategy.

Usage:
  python tools/risk_floor_lock_stats.py ROOT [--filter CG1AT] [--labels labels.json] [--out stats.json] [--limit N]
ROOT is searched recursively for directories containing risk_floor_trace.json.gz.
labels.json: {"<market_id>": "UP"|"DOWN"} or {"<market_id>": {"winner": "UP"|"DOWN"}} (tolerant loader).
"""
import argparse, collections, gzip, json, statistics, sys, time
from pathlib import Path

TICKET = 15.0


def jload(p):
    p = Path(p)
    if p.suffix == '.gz':
        with gzip.open(p, 'rb') as f:
            return json.loads(f.read())
    return json.loads(p.read_text(encoding='utf-8'))


def load_labels(path):
    if not path:
        return {}
    raw = jload(path)
    if isinstance(raw, dict) and 'labels' in raw and isinstance(raw['labels'], (dict, list)):
        raw = raw['labels']
    out = {}
    items = raw.items() if isinstance(raw, dict) else [(r.get('market_id'), r) for r in raw if isinstance(r, dict)]
    for k, v in items:
        if isinstance(v, dict):
            v = v.get('winner') or v.get('winning_side') or v.get('outcome') or v.get('result')
        if isinstance(v, str) and v.upper() in ('UP', 'DOWN'):
            try:
                out[int(k)] = v.upper()
            except (TypeError, ValueError):
                pass
    return out


def analyse(d, labels):
    res = jload(d / 'result.json')
    rf = jload(d / 'risk_floor_trace.json.gz')
    mid = int(res.get('market_id') or 0)
    ev = (res.get('v12g') or {}).get('events') or []
    decide = next((e for e in ev if e['kind'] == 'DECIDE'), None)
    flips = [e for e in ev if e['kind'] == 'FLIP']
    conf = res.get('v63_confirm') or {}
    m0 = conf.get('m0')
    th = conf.get('lowconv_th')
    low = bool(conf.get('lowmode_freeze') and m0 is not None and th is not None and m0 < th)
    row = dict(market_id=mid, path=str(d), status=res.get('status'), decide_side=decide and decide['side'], m0=m0, low_conviction=low,
               n_flips=len(flips), final_side=(res.get('v12g') or {}).get('final_side'), terminal_worst=res.get('terminal_worst'),
               floor_mode=rf.get('mode'))
    inv = res.get('final_inventory') or {}
    cost = res.get('final_cost')
    win = labels.get(mid)
    row['winner'] = win
    row['pnl_winner'] = (float(inv[win]) - float(cost)) if win and cost is not None and win in inv else None
    if win and decide:
        row['group'] = 'NO_FLIP' if not flips else ('FALSE_FLIP' if win == decide['side'] else 'TRUE_FLIP')
    else:
        row['group'] = ('NO_FLIP' if not flips else 'FLIP') if decide else 'NO_DECIDE'
    if low:
        row['group'] = 'LOWCONV_' + row['group']
    decs = rf.get('decisions') or []
    t_first = flips[0]['t'] if flips else None
    row['first_flip_t'] = t_first
    row['floor_at_first_flip'] = (rf.get('initial') or {}).get('floor')
    row['final_floor'] = rf.get('floor')
    row['terminal_worst_minus_floor'] = (float(res['terminal_worst']) - float(rf['floor'])
                                        if rf.get('floor') is not None and res.get('terminal_worst') is not None else None)
    n = len(decs)
    blocked = [x for x in decs if not x['allowed']]
    row['decisions'] = n
    row['blocked'] = len(blocked)
    row['blocked_share'] = (len(blocked) / n) if n else None
    row['blocked_by_origin'] = dict(collections.Counter('%s/%s' % (x['origin'], x['route']) for x in blocked))
    if blocked:
        av = [float(x['available']) for x in blocked if x.get('available') is not None]
        row['median_headroom_when_blocked'] = statistics.median(av) if av else None
        row['first_block_t'] = min(x['t'] for x in blocked)
        row['first_block_after_flip_s'] = (row['first_block_t'] - t_first) / 1000. if t_first else None
    # clock_trace: orders after first flip, V8 unserved deficit while blocked
    ctp = d / 'clock_trace.json.gz'
    if ctp.exists():
        ct = jload(ctp)
        start = (ct.get('tail_new_rows') or [{}])[0].get('start')
        news = [(pl['t'], o) for pl in ct.get('plans', []) for o in pl['operations'] if o['kind'] == 'NEW']
        row['new_total'] = len(news)
        if t_first:
            post = [(t, o) for t, o in news if t >= t_first]
            row['new_after_first_flip'] = len(post)
            row['new_after_first_flip_active'] = sum(1 for t, o in post if o.get('route') == 'ACTIVE')
        if blocked:
            tb = row['first_block_t']
            row['new_after_first_block'] = sum(1 for t, o in news if t >= tb)
            it = [x for x in ct.get('intent', []) if x['t'] >= tb and x.get('desired') and x.get('inv')]
            if it:
                defs = [sum(max(0., float(x['desired'][s]) - float(x['inv'][s]) - float((x.get('reserved_qty') or {}).get(s, 0.))) for s in ('UP', 'DOWN')) for x in it]
                row['v8_unserved_deficit_mean'] = statistics.fmean(defs)
                row['v8_unserved_deficit_max'] = max(defs)
            st = [x for x in ct.get('states', []) if x['t'] >= tb]
            if st:
                a, b = st[0]['inv'], st[-1]['inv']
                row['inv_change_after_first_block'] = {s: float(b[s]) - float(a[s]) for s in ('UP', 'DOWN')}
        row['start_ms'] = start
    return row


def summarise(rows):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r['group']].append(r)
    groups['ALL'] = rows
    out = {}
    for g, rs in sorted(groups.items()):
        fl = [r for r in rs if r['n_flips']]
        bs = [r['blocked_share'] for r in fl if r.get('blocked_share') is not None]
        pn = [r['pnl_winner'] for r in rs if r.get('pnl_winner') is not None]
        heavy = [r for r in fl if (r.get('blocked_share') or 0) > .5]
        light = [r for r in fl if (r.get('blocked_share') or 0) <= .5]
        pw = lambda xs: statistics.fmean([r['pnl_winner'] for r in xs if r.get('pnl_winner') is not None]) if any(r.get('pnl_winner') is not None for r in xs) else None
        out[g] = dict(
            n=len(rs), n_flipped=len(fl),
            blocked_share_mean=(statistics.fmean(bs) if bs else None),
            blocked_share_median=(statistics.median(bs) if bs else None),
            n_blocked_over_50pct=len(heavy), n_blocked_le_50pct=len(light),
            mean_pnl_all=(statistics.fmean(pn) if pn else None),
            mean_pnl_heavy_blocked=pw(heavy), mean_pnl_light_blocked=pw(light),
            n_terminal_worst_equals_floor=sum(1 for r in fl if r.get('terminal_worst_minus_floor') is not None and abs(r['terminal_worst_minus_floor']) < 1e-6),
            v8_unserved_deficit_mean=(statistics.fmean([r['v8_unserved_deficit_mean'] for r in fl if 'v8_unserved_deficit_mean' in r]) if any('v8_unserved_deficit_mean' in r for r in fl) else None),
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--filter', default='')
    ap.add_argument('--labels', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    labels = load_labels(a.labels) if a.labels else {}
    dirs = sorted({p.parent for p in Path(a.root).rglob('risk_floor_trace.json.gz') if a.filter in str(p.parent)})
    if a.limit:
        dirs = dirs[:a.limit]
    rows, errors = [], []
    for d in dirs:
        try:
            rows.append(analyse(d, labels))
        except Exception as ex:  # keep going; report at the end
            errors.append(dict(path=str(d), error=type(ex).__name__ + ': ' + str(ex)))
    summary = summarise(rows) if rows else {}
    payload = dict(paths=len(dirs), analysed=len(rows), errors=errors, labels_loaded=len(labels), summary=summary, rows=rows,
                   note='read-only offline analysis; settlement labels used only to group/score after replay')
    if a.out:
        Path(a.out).write_text(json.dumps(payload, indent=1, default=str), encoding='utf-8')
    print('paths=%d analysed=%d errors=%d labels=%d elapsed=%.1fs' % (len(dirs), len(rows), len(errors), len(labels), time.time() - t0))
    for g, s in summary.items():
        f = lambda v: 'NA' if v is None else ('%.2f' % v)
        print('%-22s n=%3d flipped=%3d blocked_share mean=%s med=%s | >50%%:%d <=50%%:%d | pnl all=%s heavy=%s light=%s | term==floor:%d | v8_deficit=%s' % (
            g, s['n'], s['n_flipped'], f(s['blocked_share_mean']), f(s['blocked_share_median']), s['n_blocked_over_50pct'], s['n_blocked_le_50pct'],
            f(s['mean_pnl_all']), f(s['mean_pnl_heavy_blocked']), f(s['mean_pnl_light_blocked']), s['n_terminal_worst_equals_floor'], f(s['v8_unserved_deficit_mean'])))
    for e in errors[:10]:
        print('ERROR', e)


if __name__ == '__main__':
    sys.exit(main())
