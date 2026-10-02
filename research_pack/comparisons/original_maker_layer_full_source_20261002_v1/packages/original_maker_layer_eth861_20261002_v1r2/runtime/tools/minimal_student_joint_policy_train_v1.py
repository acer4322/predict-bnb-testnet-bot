"""One coupled executable passive policy, intended for whole-episode optimization.
No Target data access. No hidden180s/TTL/Pair veto. Not a full4-channel policy.
"""
from copy import deepcopy
import math
from tools.minimal_student_training_world_v2 import (
    BudgetAllocation, envelope, passive_ask,
)
from tools.pair_core_asset_route_sizing_v2 import validate_size

INITIAL=[0.,1.2,.3,-.8,.2,-.2,.4,math.log(30.),.2,-1.2,.5,-.5]
PARAMETER_NAMES=['exposure_bias','market_mid','depth_imbalance','own_exposure_feedback',
    'phase_exposure','utilization_bias','utilization_phase','log_ticket','ticket_deficit_feedback',
    'quote_depth_bias','quote_inventory_feedback','cancel_tolerance']


def sig(x):
    z=math.exp(-abs(x));return 1/(1+z) if x>=0 else z/(1+z)


def softplus(x):return max(x,0)+math.log1p(math.exp(-abs(x)))


def stable_features(f):
    book=f['book'];inv=f['own_view']['inv'];cost=float(f['own_view']['cost'])
    bid=max(book['bids']) if book['bids'] else None
    ask=min(book['asks']) if book['asks'] else None
    gross=float(inv['UP']+inv['DOWN'])
    phase=(f['t']-f['start'])/(f['end']-f['start'])
    if bid is None or ask is None:return None
    bidq=float(book['bids'][bid]);askq=float(book['asks'][ask])
    return dict(phase=max(0.,min(1.,phase)),mid=(bid+ask)/2.,up_bid=bid,up_ask=ask,
        depth_imbalance=(bidq-askq)/max(1.,bidq+askq),
        own_net=(inv['UP']-inv['DOWN'])/(1.+gross),own_gross=gross,cost=cost,
        up_qty=float(inv['UP']),down_qty=float(inv['DOWN']),
        floor=min(inv.values())-cost,upside=max(inv.values())-cost)


class JointWholePolicy:
    provenance='WHOLE_EPISODE_LEARNED_PARAMETERS_NOT_PRIVATE_TARGET_ACTION_LABELS'
    def __init__(self,theta,variant):
        if len(theta)!=12 or not all(math.isfinite(x) for x in theta):raise ValueError('12finite coupled parameters required')
        self.theta=list(map(float,theta));self.policy_id='WHOLE_EPISODE_POLICY_V1:'+variant
        self.continuation_id=self.policy_id+':COMPLETE_EPISODE';self.calls=0
        self.declines={};self.bidirectional_plans=0;self.multi_new_plans=0;self.last_features=None
    def decline(self,reason):self.declines[reason]=self.declines.get(reason,0)+1
    def produce(self,f):
        self.calls+=1;ops=[];v=f['own_view'];live={k:c for k,c in f['ledger'].carriers.items() if c.state!='TERMINAL'}
        x=stable_features(f);self.last_features=x
        # True market closure is termination, not an arbitrary trading cutoff.
        if f['t']>=f['end']:
            for k,c in live.items():
                if c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):
                    ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='ACTUAL_MARKET_CLOSED'))
            return envelope(f,self,ops)
        if f['t']<f['start'] or x is None:return envelope(f,self,[])
        w=self.theta
        exposure=math.tanh(w[0]+w[1]*(2*x['mid']-1)+w[2]*x['depth_imbalance']+
                           w[3]*x['own_net']+w[4]*(2*x['phase']-1))
        proportion={'UP':.5+.45*exposure,'DOWN':.5-.45*exposure}
        use=sig(w[5]+w[6]*(2*x['phase']-1))
        mid={'UP':x['mid'],'DOWN':1-x['mid']}
        pid={'UP':1,'DOWN':2};tick=f['world_profile']['tick'];step=f['world_profile']['quantity_step']
        capital=f['ledger'].capital
        desired={s:capital*use*proportion[s]/max(tick,mid[s]) for s in pid}
        accounts={s:f['ledger'].account(pid[s]) for s in pid}
        claim={s:accounts[s]['spent']+accounts[s]['reserved_cash'] for s in pid}
        free=max(0.,capital-sum(claim.values()))
        allocation=[]
        for s in pid:
            # Explicit plan-level allocations retain all existing/acquired claims.
            g=f['ledger'].grants[pid[s]]
            qty_authority=max(g.add_qty,accounts[s]['add_filled']+accounts[s]['reserved_qty'],desired[s])
            allocation.append(BudgetAllocation(pid[s],claim[s]+free*proportion[s],qty_authority,
                self.policy_id+':EXPLICIT_UNCOMMITTED_CAPITAL_ONLY'))
        draft=deepcopy(f['ledger']);draft.reallocate(allocation)
        prices={};tickets={};deficits={}
        for s in pid:
            p_ask=passive_ask(f['book'],s)
            p_bid=x['up_bid'] if s=='UP' else round(1-x['up_ask'],10)
            owned=float(v['inv'][s])+accounts[s]['reserved_qty']
            deviation=(owned-desired[s])/(1.+desired[s])
            depth=softplus(w[9]+w[10]*deviation)
            p=round(math.floor((p_bid-depth*tick+1e-10)/tick)*tick,10)
            prices[s]=p
            deficits[s]=desired[s]-owned
            tickets[s]=math.exp(max(-6.,min(6.,w[7]+w[8]*abs(deviation))))
        # Cancellation and new placements are planned together, never release
        # cancelled capacity until native terminal evidence arrives.
        for k,c in live.items():
            s=draft.grants[c.parent_id].side
            mismatch=abs(c.limit-prices[s])>tick*(1.+softplus(w[11]))+1e-9
            too_much=deficits[s]<-tickets[s]*sig(w[11])
            if (mismatch or too_much) and c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):
                ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='COUPLED_DESIRED_STATE_OR_QUOTE_CHANGE'))
                draft.request_cancel(k)
        n=v['n'];slots=f['world_profile']['max_live_owners']-len(live);new=[]
        # Deterministic economic priority of the *whole* plan; both sides may act.
        sides=sorted(pid,key=lambda s:(-max(0.,deficits[s])*mid[s],s))
        for s in sides:
            if slots<=0:self.decline('RESOURCE_CENSOR');break
            p=prices[s];ask=passive_ask(f['book'],s)
            if not tick<=p<1 or ask is None or p>=ask-1e-10:
                self.decline('PRICE_OR_POSTONLY_LEGALITY');continue
            a=draft.account(pid[s]);g=draft.grants[pid[s]]
            cash=max(0.,g.cash_limit-a['spent']-a['reserved_cash'])
            qty=max(0.,min(tickets[s],deficits[s],cash/p,
                          a['add_remaining']+a['repair_remaining']-a['reserved_qty']))
            qty=round(math.floor((qty+1e-10)/step)*step,8)
            try:validate_size(f['world_profile']['asset'],'PASSIVE',p,qty,quantity_step=step)
            except ValueError:self.decline('MODEL_DESIRED_INCREMENT_BELOW_LEGAL_MINIMUM');continue
            key=f'{s}_{n}'
            try:draft.reserve(key,pid[s],'PASSIVE',qty,p,0.,now_ms=f['t'],market_end_ms=f['end'])
            except ValueError as exc:self.decline('JOINT_FEASIBILITY:'+str(exc));continue
            new.append(dict(kind='NEW',key=key,parent_id=pid[s],side=s,price=p,qty=qty,
                            role='JOINT_DESIRED_PORTFOLIO_CONTINUATION'))
            n+=1;slots-=1
        self.multi_new_plans+=len(new)>1
        self.bidirectional_plans+=len({o['side'] for o in new})>1
        ops.extend(new)
        return envelope(f,self,ops,allocation)
