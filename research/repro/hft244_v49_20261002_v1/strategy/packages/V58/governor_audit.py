"""Saved full-path validation; no future labels enter the actor."""
from analyze import read,differences
from governor import required,repair_room
from copy import deepcopy
def audit(arm,baseline,mode,cap,*,compare_preflip=True):
    tr=read(arm/'clock_trace.json.gz');pol=read(arm/'governor_trace.json.gz');r=read(arm/'result.json')
    ticket=float(read(arm/'EXECUTION.json')['env_v12']['V12G_PASSIVE_TICKET'])
    assert pol['passive_ticket']==ticket
    assert pol['mode']==mode and pol['cap']==cap
    old=read(baseline/'clock_trace.json.gz');rs=read(arm/'risk_floor_trace.json.gz')
    assert len(pol['plans'])==len(tr['plans'])==len(rs['plans'])
    flips=[e['t'] for e in r['v12g']['events'] if e['kind']=='FLIP'];ft=flips[0] if flips else None
    post_add_qty=0.;post_repair_qty=0.;retired=0;peak=0.;first60=None
    start=tr['direction_rows'][0]['t']
    for p,t,b in zip(pol['plans'],tr['plans'],rs['plans']):
        assert p['t']==t['t']==b['t'] and p['index']==b['index']
        assert not differences(p['state'],b['state'])
        assert not differences(p['operations'],t['operations'])
        assert p['flipped']==(ft is not None and p['t']>=ft)
        s=deepcopy(p['state']);peak=max(peak,required(s))
        for o in t['operations']:
            if o['kind']=='CANCEL':retired+=int(o.get('reason')=='V52_POSTFLIP_EXPANSION_RETIRE')
            if o['kind']!='NEW':continue
            repair=min(o['qty'],repair_room(s,o['side']))
            if p['flipped']:
                post_repair_qty+=repair;post_add_qty+=o['qty']-repair
                if mode=='REPAIR_ONLY':assert o['qty']-repair<1e-7
            assert o['qty']*o['price']>=1-1e-8
            if o['route']=='PASSIVE':assert abs(o['qty']-ticket)<1e-8
            s['pending_qty'][o['side']]+=o['qty'];s['pending_cash'][o['side']]+=o['qty']*o['price']
            peak=max(peak,required(s))
        assert abs(required(s)-p['funding_after'])<1e-7
        if cap is not None:assert required(s)<=cap+1e-7
    if cap is None and compare_preflip:
        n=next((i for i,p in enumerate(tr['plans']) if ft is not None and p['t']>=ft),len(tr['plans']))
        assert not differences(tr['plans'][:n],old['plans'][:n]),'PRE_FLIP_PLAN_PARITY'
        if ft is None:
            for k in ('plans','states','native_actions'):
                assert not differences(tr[k],old[k]),('NO_FLIP_PARITY',k)
    if cap is not None:assert r['final_cost']<=cap+1e-7
    # Independently map every real receipt and carrier, including canonical cost.
    clock=read(arm/'execution_clock.json')
    ops={int(o['key'].rsplit('_',1)[1]):o for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW'}
    assert {o['key'] for o in ops.values()}==set(clock['carriers'])
    inv=dict(UP=0.,DOWN=0.);cost=0.;fees=0.;agg={k:[0.,0.,0.] for k in clock['carriers']}
    for x in clock['receipts']:
        o=ops[x['order_id']];p=x['price'] if o['side']=='UP' else 1-x['price']
        payment=x['qty']*p;inv[o['side']]+=x['qty'];cost+=payment+x['fee'];fees+=x['fee']
        z=agg[o['key']];z[0]+=x['qty'];z[1]+=payment;z[2]+=x['fee']
    assert fees==0. and abs(cost-r['final_cost'])<1e-7
    assert all(abs(inv[s]-r['final_inventory'][s])<1e-7 for s in inv)
    for k,c in clock['carriers'].items():
        assert c['state']=='TERMINAL'
        assert all(abs(x-c[f])<1e-7 for x,f in zip(agg[k],('filled','payment','fees')))
    assert r['unresolved_owners']==0
    return dict(passive_ticket=ticket,governor_mode=mode,market_cap=cap,governor_decisions=len(pol['rows']),
        governor_reduced=sum(0<x['quantity']<x['proposed_quantity']-1e-8 for x in pol['rows']),
        governor_blocked=sum(x['quantity']==0 for x in pol['rows']),postflip_new_expansion_qty=post_add_qty,
        postflip_new_repair_qty=post_repair_qty,retired_expansion_owners=retired,
        peak_repair_funding_envelope=peak,receipt_ledger_pass=True,fees=fees,
        before_flip_parity=cap is None and compare_preflip,no_flip_full_parity=cap is None and compare_preflip and ft is None)
