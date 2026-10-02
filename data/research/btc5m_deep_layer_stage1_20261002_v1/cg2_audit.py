"""CG2 saved-path audit: insurance orders (A) and no-chase scope (B)."""
from analyze import read


def audit(arm, job):
    r = read(arm / 'result.json'); tr = read(arm / 'clock_trace.json.gz'); ci = read(arm / 'cheap_insurance_trace.json.gz'); clock = read(arm / 'execution_clock.json')
    env = job['env_v12']; c = r['v63_confirm']; start = tr['tail_new_rows'][0]['start']
    high = c['m0'] is not None and c['m0'] >= float(env.get('V12G_LOWCONV_TH') or 9)
    births = [(p['t'], o) for p in tr['plans'] for o in p['operations'] if o['kind'] == 'NEW' and o.get('role') == 'CG2_CHEAP_INSURANCE']
    px = float(env['V12G_INS_PX']) if env.get('V12G_INS_PX') else None
    if px is None: assert not births and not ci['orders']
    else:
        assert {o['key'] for _, o in births} == {o['key'] for o in ci['orders']}
        for t, o in births:
            assert high and o['price'] <= px + 1e-9 and o['route'] == 'ACTIVE' and t < start + 290000 and o['qty'] * o['price'] >= 1 - 1e-8
    nochase = env.get('V12G_NOCHASE_PX')
    nv = c.get('nochase_vetoes', 0) + c.get('nochase_padd_vetoes', 0)
    if not nochase: assert nv == 0
    else: assert high or nv == 0
    return dict(cg2_high=high, cg2_ins_orders=len(births), cg2_ins_filled=sum(clock['carriers'][o['key']]['filled'] for _, o in births),
                cg2_ins_paid=sum(clock['carriers'][o['key']]['payment'] for _, o in births), cg2_nochase_vetoes=nv, cg2_first_action_t=c.get('first_veto_t'))
