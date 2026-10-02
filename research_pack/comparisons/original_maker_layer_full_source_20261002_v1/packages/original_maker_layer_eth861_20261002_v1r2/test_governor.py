"""Meaningful independent fill-subset and budget/repair lifecycle fixtures."""
import itertools,json
from copy import deepcopy
from pathlib import Path
from governor import choose,required,repair_room,Governor
def state(up,down,cost,owners=()):
    return dict(inv=dict(UP=up,DOWN=down),cost=cost,payoff=dict(UP=up-cost,DOWN=down-cost),
        pending_qty={s:sum(q for t,q,p in owners if t==s) for s in ('UP','DOWN')},
        pending_cash={s:sum(q*p for t,q,p in owners if t==s) for s in ('UP','DOWN')},owners=[])
def run():
    cases=[]
    for d in (-100.,0.,100.):
        owners=[('UP',15.,.2),('UP',30.,.8),('DOWN',20.,.4),('DOWN',10.,.9)]
        s=state(150+d,150,25,owners)
        actual=max(25+sum(q*p*f for (_,q,p),f in zip(owners,fs))+abs(d+sum((1 if t=='UP' else -1)*q*f for (t,q,p),f in zip(owners,fs))) for fs in itertools.product((0.,.25,.5,1.),repeat=4))
        assert abs(actual-required(s))<1e-8
        cases.append('all_partial_fill_combinations_'+str(d))
    for side in ('UP','DOWN'):
        other='DOWN' if side=='UP' else 'UP'
        s=state(0,0,0);s['inv'][other]=100.;s['cost']=200.
        assert required(s)==300.
        q=choose(s,side,.99,100.,'ACTIVE',factor=0.,cap=300.)['quantity']
        assert q==100.,'repair must remain possible at fully allocated envelope'
        assert choose(s,other,.5,15.,'ACTIVE',cap=300.)['quantity']==0.
        pending=deepcopy(s);pending['pending_qty'][side]=100.;pending['pending_cash'][side]=99.
        assert required(pending)==300. and repair_room(pending,side)==0.
        # Partial confirmed repair; the remaining owner stays reserved.
        part=deepcopy(pending);part['inv'][side]=40.;part['cost']+=39.6
        part['pending_qty'][side]=60.;part['pending_cash'][side]=59.4
        assert required(part)<=300.+1e-8
        assert choose(part,other,.5,15.,'ACTIVE',factor=0.,cap=300.)['quantity']==0.
        terminal=deepcopy(part);terminal['pending_qty'][side]=0.;terminal['pending_cash'][side]=0.
        assert choose(terminal,side,.99,60.,'ACTIVE',cap=300.)['quantity']==60.
        assert choose(s,side,.05,1.,'ACTIVE',cap=300.)['quantity']==0.
        cases.extend([side+'_cap_boundary_repair',side+'_partial_cancel_pending',side+'_terminal_zero_fill_recovery',side+'_below_minimum'])
    s=state(100,0,10)
    assert choose(s,'UP',.5,15,'ACTIVE',factor=.5)['quantity']==7.5
    assert choose(s,'UP',.5,15,'PASSIVE',factor=.5)['quantity']==0.
    assert choose(s,'DOWN',.5,15,'PASSIVE',factor=0.)['quantity']==15.
    assert choose(s,'UP',.5,15,'ACTIVE',factor=0.)['quantity']==0.
    cases+=['half_active','half_passive_minimum_not_rounded_up','physical_repair_retained','zero_expansion']
    # Pending on opposite side is never assumed filled to create repair authority.
    s=state(50,50,20,[('UP',100,.5)])
    assert repair_room(s,'DOWN')==0.
    cases.append('unfilled_opposite_not_credit')
    # No observed FLIP -> quantity factor remains 1; check runtime adapter itself.
    import os
    saved=dict(os.environ)
    try:
        os.environ['V12G_POSTFLIP_ADD_MODE']='REPAIR_ONLY';os.environ.pop('V12G_MARKET_CAP',None)
        ctx=type('C',(),dict(frame=dict(t=1,index=1,world_profile=dict(quantity_step=.01))))()
        roles=type('R',(),dict(v12g_events=[]))();g=Governor(ctx,roles)
        assert g.quantity(state(100,0,10),'UP',.5,15,'PASSIVE','fixture')==15.
        roles.v12g_events=[dict(kind='FLIP',t=1)]
        assert g.quantity(state(100,0,10),'UP',.5,15,'PASSIVE','fixture')==0.
    finally:
        for k in ('V12G_POSTFLIP_ADD_MODE','V12G_MARKET_CAP'):
            if k in saved:os.environ[k]=saved[k]
            else:os.environ.pop(k,None)
    cases+=['before_flip_parity','after_observed_flip_only']
    return dict(status='PASS',checks=len(cases),names=cases,native_executed=0,fees='ZERO_FEE_RESEARCH_ONLY')
if __name__=='__main__':
    out=run();(Path(__file__).parent/'LOCAL_TESTS.json').write_text(json.dumps(out,indent=2),encoding='utf8');print(json.dumps(out))
