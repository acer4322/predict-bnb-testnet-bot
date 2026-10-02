"""Independent finite-goal and original-prefix checks on realized native paths."""
from analyze import read,differences


def audit(arm,baseline,mode):
    tr=read(arm/'clock_trace.json.gz');old=read(baseline/'clock_trace.json.gz')
    policy=read(arm/'qualified_work_trace.json.gz');result=read(arm/'result.json')
    clock=read(arm/'execution_clock.json');prior=read(baseline/'execution_clock.json')
    assert policy['mode']==mode
    orders=policy['orders'];work=policy['work'];lookup={o['key']:o for o in orders}
    rows={r.get('new_key'):r for r in policy['rows'] if r.get('new_key')}
    first=min((o['index'] for o in orders),default=None)
    born={o['key']:(i,p['t'],o) for i,p in enumerate(tr['plans']) for o in p['operations'] if o['kind']=='NEW'}
    assert set(lookup)=={k for k,(_,_,o) in born.items() if o.get('role')=='ACTIVE_QUALIFIED_FINITE_CONTINUATION'}
    if work:
        assert work['original_key'] in born
        _,_,birth=born[work['original_key']]
        assert birth['role']=='ACTIVE_QUALIFIED_RATIO_RESTORATION'
        assert abs(work['target']-work['initial_inventory']-work['initial_pending']-work['admitted_new_quantity'])<1e-7
        assert work['status']!='CONFIRMED_TARGET_REACHED' or work['last_inventory']>=work['target']-1e-7
    flips=[e['t'] for e in result['v12g']['events'] if e['kind']=='FLIP']
    for key,o in lookup.items():
        r=rows[key];p=r['planned'];state=r['state'];side=o['side'];other='DOWN' if side=='UP' else 'UP'
        i,t,actual=born[key]
        assert i==o['index']==r['index'] and t==r['t']==o['t']
        assert r['original_count']>=5 and work['born_index']<i
        assert not any(f<=t for f in flips)
        assert state['inv'][side]+p['pending_qty'][side]+o['qty']<=work['target']+1e-7
        assert state['payoff'][other]-p['pending_cash'][side]-o['qty']*o['price']>=r['original_proposal']['retained_G_floor']-1e-7
        assert o['qty']<=r['original_proposal']['quantity']+1e-7 and o['qty']*o['price']>=1-1e-8
        assert r['final_eligible'] and r['eligible']
        assert clock['carriers'][key]['filled']<=o['qty']+1e-7
    if mode=='OFF':assert not orders
    return dict(qualified_mode=mode,qualified_extension_orders=len(orders),first_extension_index=first,
        no_extension_full_parity=first is None,qualified_work_status=work['status'] if work else None,
        qualified_target=work['target'] if work else None,
        qualified_remaining=work['remaining_confirmed'] if work else None,
        extension_requested_qty=sum(o['qty'] for o in orders),
        extension_filled_qty=sum(clock['carriers'][o['key']]['filled'] for o in orders),
        extension_paid=sum(clock['carriers'][o['key']]['payment']+clock['carriers'][o['key']]['fees'] for o in orders))
