"""Reuse finite Active sizing with an explicit strong-pending scenario offset."""
from copy import deepcopy
from roles_runtime import roles

MODE='CURRENT_ONLY'


def ready(work,submissions,owner):
    return len(submissions)==1 and work is not None and work['id']==submissions[0]['work_id'] and owner is not None and owner.state=='TERMINAL'


def decide(base,state,operations,ask,depth,work,crossing,step=.01,tick=.01):
    assert MODE in ('CURRENT_ONLY','PENDING_BURDEN')
    cash=state['pending_cash'][roles.strong]+sum(o['qty']*o['price'] for o in operations if o['kind']=='NEW' and o['side']==roles.strong)
    adjusted=deepcopy(work)
    if MODE=='PENDING_BURDEN':adjusted['anchor_floor']+=cash
    out=base(state,operations,ask,depth,adjusted,True,crossing,step,tick,continuation=False)
    out.update(service_mode=MODE,base_anchor=work['anchor_floor'],strong_pending_burden=cash,
        scenario_weak_payoff=out['projected_down_from_pending_down']-cash,
        pending_strong_qty_credited_to_net_capacity=False)
    return out


def aggregate_receipts(receipts):
    if not receipts or any(r is None for r in receipts):return None
    return dict(state='TERMINAL' if all(r['state']=='TERMINAL' for r in receipts) else 'NONTERMINAL',
        filled=sum(r['filled'] for r in receipts))
