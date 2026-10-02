"""CG3 WL saved-path audit: every ladder order is PASSIVE, one ticket, on the weak side (not the DECIDE side), priced <= PXMAX and
strictly below the weak best bid at birth, in a high-conviction market, after DECIDE, before the first FLIP and before 290 s;
ladder cancels only touch ladder keys; r1: no ladder order is cancelled by the V8 maintenance loop."""
from analyze import read


def audit(arm, job):
    r = read(arm / 'result.json'); tr = read(arm / 'clock_trace.json.gz'); wl = read(arm / 'w_ladder_trace.json.gz'); clock = read(arm / 'execution_clock.json')
    env = job['env_v12']; c = r['v63_confirm']; start = tr['tail_new_rows'][0]['start']
    high = c['m0'] is not None and c['m0'] >= float(env.get('V12G_LOWCONV_TH') or 9)
    births = [(p['t'], o) for p in tr['plans'] for o in p['operations'] if o['kind'] == 'NEW' and o.get('role') == 'CG3_W_LADDER']
    keys = {o['key'] for _, o in births}
    ratio = env.get('V12G_WL_RATIO')
    if not ratio:
        assert not births and not wl['orders']
        return dict(wl_orders=0, wl_filled=0., wl_paid=0.)
    ev = r['v12g']['events']; dec = next(e for e in ev if e['kind'] == 'DECIDE'); fl = [e['t'] for e in ev if e['kind'] == 'FLIP']
    assert keys == {o['key'] for o in wl['orders']}
    ticket = float(env.get('V12G_PASSIVE_TICKET') or 15.); pxmax = float(env.get('V12G_WL_PXMAX') or .45)
    byk = {o['key']: o for o in wl['orders']}
    for t, o in births:
        assert high and o['route'] == 'PASSIVE' and abs(o['qty'] - ticket) < 1e-8 and o['side'] != dec['side'], o
        assert o['price'] <= pxmax + 1e-9 and o['price'] < byk[o['key']]['wbid'] - 1e-9, o
        assert t >= dec['t'] and (not fl or t < fl[0]) and t < start + 290000, (t, fl[:1])
    for p in tr['plans']:
        for o in p['operations']:
            if o['kind'] == 'CANCEL' and str(o.get('reason', '')).startswith('CG3_WL'): assert o['key'] in keys
            if o['kind'] == 'CANCEL' and o['key'] in keys: assert o.get('reason') != 'V8_MAINTENANCE', ('LADDER_MAINTENANCE_CANCEL', o)
    filled = sum(clock['carriers'][k]['filled'] for k in keys if k in clock['carriers'])
    paid = sum(clock['carriers'][k]['payment'] for k in keys if k in clock['carriers'])
    from collections import Counter
    creasons = dict(Counter(o.get('reason') for p in tr['plans'] for o in p['operations'] if o['kind'] == 'CANCEL' and o['key'] in keys))
    return dict(wl_cancel_reasons=creasons, wl_high=high, wl_orders=len(births), wl_cancels=len(wl['cancels']), wl_filled=filled, wl_paid=paid, wl_reasons=wl['reasons'])
