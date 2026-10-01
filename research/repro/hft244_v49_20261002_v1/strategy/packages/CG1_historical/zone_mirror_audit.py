"""V65 saved-path audit of zone-mirror orders (independent of the runtime state)."""
from pathlib import Path
from analyze import read
ROLE = 'V65_ZONE_MIRROR'


def audit(arm, job):
    tr = read(arm / 'clock_trace.json.gz'); zm = read(arm / 'zone_mirror_trace.json.gz'); r = read(arm / 'result.json'); clock = read(arm / 'execution_clock.json')
    on = job['env_v12'].get('V12G_ZONE_MIRROR') == 'ON'
    births = [(p['t'], o) for p in tr['plans'] for o in p['operations'] if o['kind'] == 'NEW' and o.get('role') == ROLE]
    assert zm['enabled'] == on and r['v65_zone_mirror']['enabled'] == on
    if not on:
        assert not births and not zm['orders']; return dict(zone_mirror=False)
    assert {o['key'] for _, o in births} == {o['key'] for o in zm['orders']}
    ev = r['v12g']['events']; dec = next(e for e in ev if e['kind'] == 'DECIDE'); fl = [e['t'] for e in ev if e['kind'] == 'FLIP']
    start = tr['tail_new_rows'][0]['start']
    for t, o in births:
        assert dec['t'] <= t < start + 290000 and (not fl or t < fl[0]) and o['route'] == zm['route'] and o['qty'] * o['price'] >= 1 - 1e-8
        assert o['side'] != dec['side'], 'MIRROR_MUST_BUY_THE_OTHER_SIDE'
        rec = next(x for x in zm['orders'] if x['key'] == o['key'])
        assert zm['lo'] - 1e-8 <= rec['cm'] <= zm['hi'] + 1e-8 and zm['alpha'] * rec['zoneF'] - rec['zoneW'] >= o['qty'] - 1e-6
    assert zm['alpha'] == float(job['env_v12'].get('V12G_ZONE_ALPHA') or 1.0) and zm['route'] == job['env_v12'].get('V12G_ZONE_ROUTE', 'ACTIVE')
    filled = sum(clock['carriers'][o['key']]['filled'] for _, o in births); paid = sum(clock['carriers'][o['key']]['payment'] for _, o in births)
    return dict(zone_mirror=True, zone_route=zm['route'], zone_alpha=zm['alpha'], zone_orders=len(births), zone_mirror_filled=filled, zone_mirror_paid=paid,
                zone_F=zm['zoneF'], zone_W=zm['zoneW'], zone_first_s=(births[0][0] - start) / 1000 if births else None)
