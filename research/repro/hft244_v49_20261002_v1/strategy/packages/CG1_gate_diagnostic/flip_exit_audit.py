"""V60 saved-path audit of the flip exit: no re-entry, only flatten orders after the exit starts, no NEW after 290 s."""
from analyze import read

ROLE = 'FLIP_EXIT_FLATTEN'


def audit(arm, job):
    tr = read(arm / 'clock_trace.json.gz'); fx = read(arm / 'flip_exit_trace.json.gz'); r = read(arm / 'result.json')
    clock = read(arm / 'execution_clock.json')
    mode = job['env_v12']['V12G_FLIP_EXIT']
    assert fx['mode'] == mode and r['v60_flip_exit']['mode'] == mode
    news = [(p['t'], o) for p in tr['plans'] for o in p['operations'] if o['kind'] == 'NEW']
    exits = [(t, o) for t, o in news if o.get('role') == ROLE]
    if mode == 'OFF':
        assert not exits and not fx['rows']
        return dict(flip_exit_mode='OFF')
    start = tr['tail_new_rows'][0]['start']
    flips = [e['t'] for e in r['v12g']['events'] if e['kind'] == 'FLIP']
    if not fx['rows']:
        assert not exits
        return dict(flip_exit_mode='ON', flip_exit_started=False, first_flip_s=(flips[0] - start) / 1000. if flips else None)
    t0 = fx['rows'][0]['t']
    assert flips and t0 >= flips[0]
    assert all(o.get('role') == ROLE for t, o in news if t >= t0), 'REENTRY_AFTER_EXIT'
    assert all(t < t0 for t, o in news if o.get('role') != ROLE)
    assert all(t < start + 290000 and o['route'] == 'ACTIVE' and o['qty'] * o['price'] >= 1 - 1e-8 for t, o in exits)
    assert {o['key'] for _, o in exits} == set(fx['keys'])
    filled = sum(clock['carriers'][o['key']]['filled'] for _, o in exits)
    paid = sum(clock['carriers'][o['key']]['payment'] + clock['carriers'][o['key']]['fees'] for _, o in exits)
    inv = r['final_inventory']; cost = r['final_cost']
    return dict(flip_exit_mode='ON', flip_exit_started=True, first_flip_s=(flips[0] - start) / 1000., exit_start_s=(t0 - start) / 1000.,
                exit_orders=len(exits), exit_filled_qty=filled, exit_paid=paid, final_imbalance=abs(inv['UP'] - inv['DOWN']),
                final_imbalance_cash_at_risk=abs((inv['UP'] - cost) - (inv['DOWN'] - cost)))
