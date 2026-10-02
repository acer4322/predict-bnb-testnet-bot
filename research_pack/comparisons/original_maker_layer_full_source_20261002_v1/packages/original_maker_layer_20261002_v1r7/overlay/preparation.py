"""Cancellable commitment projection: hypothetical for planning, never released OWN."""
from copy import deepcopy

def projection(state,strong,cancellable):
    hypothetical=deepcopy(state);removed=[]
    kept=[]
    for owner in state['owners']:
        risky=owner['side']==strong and (1.-3.*float(owner['limit']))<0
        can_complete=bool(cancellable.get(owner['key'],False)) or owner['state']=='CANCEL_PENDING'
        if risky and can_complete:
            removed.append(dict(owner));q=float(owner['qty']);p=float(owner['limit'])
            hypothetical['pending_qty'][strong]-=q;hypothetical['pending_cash'][strong]-=q*p
        else:kept.append(dict(owner))
    hypothetical['owners']=kept
    for k in ['pending_qty','pending_cash']:
        if abs(hypothetical[k][strong])<1e-7:hypothetical[k][strong]=0.
        assert hypothetical[k][strong]>=0.
    return hypothetical,removed

def self_test():
    n=0
    for s,w in [('UP','DOWN'),('DOWN','UP')]:
        state={'inv':{s:300.,w:100.},'payoff':{s:150.,w:-50.},'pending_qty':{s:45.,w:15.},'pending_cash':{s:30.,w:3.},'owners':[
            {'key':'a','side':s,'qty':15.,'limit':.8,'state':'SUBMITTED'},
            {'key':'b','side':s,'qty':15.,'limit':.8,'state':'CANCEL_PENDING'},
            {'key':'c','side':s,'qty':15.,'limit':.4,'state':'UNKNOWN'},
            {'key':'d','side':w,'qty':15.,'limit':.2,'state':'SUBMITTED'}]}
        before=deepcopy(state);p,removed=projection(state,s,{'a':True,'b':False,'c':False})
        assert state==before;n+=1
        assert {x['key'] for x in removed}=={'a','b'};n+=1
        assert p['pending_qty'][s]==15. and p['pending_cash'][s]==6.;n+=1
        assert p['pending_qty'][w]==state['pending_qty'][w] and p['inv']==state['inv'];n+=1
        assert next(x for x in p['owners'] if x['key']=='c')['state']=='UNKNOWN';n+=1
        # The original CANCEL_PENDING cost still exists outside the hypothetical projection.
        assert state['pending_cash'][s]==30.;n+=1
    return {'status':'PASS','checks':n,'projection_only':True,'does_not_release_canonical_reservations':True,'unknown_not_assumed_cancellable':True}

if __name__=='__main__':
    import json
    print(json.dumps(self_test()))
