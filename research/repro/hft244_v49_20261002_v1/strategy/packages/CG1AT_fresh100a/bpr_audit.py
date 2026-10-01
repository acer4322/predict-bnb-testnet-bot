"""V59 saved-path audit of bounded partial repair orders; independent of the runtime service object."""
from analyze import read

ROLE = 'ACTIVE_BOUNDED_PARTIAL_REPAIR'


def audit(arm, job):
    tr = read(arm / 'clock_trace.json.gz'); pol = read(arm / 'bpr_trace.json.gz'); r = read(arm / 'result.json')
    clock = read(arm / 'execution_clock.json'); rest = read(arm / 'restoration_trace.json.gz')
    mode = job['env_v12']['V12G_BPR']
    assert pol['mode'] == mode and r['v59_bpr']['mode'] == mode
    births = [(p['t'], o) for p in tr['plans'] for o in p['operations'] if o['kind'] == 'NEW' and o.get('role') == ROLE]
    if mode == 'OFF':
        assert not births and not pol['works'] and not pol['orders'] and not pol['rows']
        return dict(bpr_mode='OFF', bpr_orders=0)
    orders = {o['key']: o for o in pol['orders']}
    assert {o['key'] for _, o in births} == set(orders) and len(births) == len(orders)
    start = tr['tail_new_rows'][0]['start']
    works = {w['work_id']: w for w in pol['works']}
    filled = paid = 0.
    for t, o in births:
        rec = orders[o['key']]; w = works[rec['work_id']]
        assert rec['t'] == t and abs(rec['qty'] - o['qty']) < 1e-9 and abs(rec['price'] - o['price']) < 1e-9 and rec['side'] == o['side'] == w['side']
        assert o['route'] == 'ACTIVE' and o['qty'] * o['price'] >= 1 - 1e-8 and o['price'] <= w['birth_ask'] + 1e-9
        assert t < start + 290000, ('BPR_NEW_AFTER_290', o['key'])
        c = clock['carriers'][o['key']]
        assert c['state'] == 'TERMINAL' and c['filled'] <= o['qty'] + 1e-7
        filled += c['filled']; paid += c['payment'] + c['fees']
    for w in pol['works']:
        f = sum(clock['carriers'][k]['filled'] for k in w['keys'])
        assert f <= w['target'] + 1e-6, ('WORK_OVERFILL', w['work_id'], f, w['target'])
        assert w['birth_row']['reason'] == 'FULL_RESTORATION_NOT_AFFORDABLE'
        assert w['budget_cash'] <= w['birth_row']['worst_G'] - w['birth_row']['retained_G_floor'] + 1e-6
    # Legacy context isolation: the legacy latch/first-active never points at a BPR order.
    for k in ('first', 'first_actual_active'):
        x = rest.get(k)
        assert not (x and x.get('key') in orders), ('LEGACY_CONTEXT_WRITTEN', k)
    assert not any(e.get('key') in orders for e in rest['events'] if isinstance(e, dict))
    return dict(bpr_mode='ON', bpr_works=len(pol['works']), bpr_orders=len(orders),
                bpr_requested_qty=sum(o['qty'] for _, o in births), bpr_filled_qty=filled, bpr_paid=paid,
                bpr_first_order_s=(births[0][0] - start) / 1000. if births else None,
                bpr_statuses=[w['status'] for w in pol['works']],
                bpr_birth_rules=[w['birth_rule'] for w in pol['works']],
                bpr_reasons=_count(x['reason'] for x in pol['rows']))


def _count(xs):
    out = {}
    for x in xs: out[x] = out.get(x, 0) + 1
    return out
