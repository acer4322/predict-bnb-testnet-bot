"""Learning-first RESEARCH adapter: no monetary ceiling, finite owned commitments.

This is not a live bankroll model. Every accepted intent and canonical receipt
still has an immutable owner. Virtual funding covers the exact explicit intent;
its size is chosen by the policy, not by a budget or by this ledger.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
import bisect
import json
import math
import statistics

from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Grant, nonnegative, EPS
from tools.minimal_student_training_world_v2 import WorldProfile, TrainingGrantLedger, envelope, passive_ask
from tools.hft244_pair_route_legality_v1 import crossing_owners
from tools.pair_core_asset_route_sizing_v2 import validate_size
from tools.minimal_student_joint_policy_train_v1 import stable_features, sig, softplus


@dataclass(frozen=True)
class OpenFundingProfile(WorldProfile):
    profile_id: str = 'MINIMAL_TRAINING_NONBINDING_FUNDING_V1'
    funding_mode: str = 'VIRTUAL_NONBINDING_RESEARCH'
    capital_cap: None = None
    def validate(self):
        super().validate()
        if self.funding_mode!='VIRTUAL_NONBINDING_RESEARCH' or self.capital_cap is not None:
            raise ValueError('This research profile does not accept an implicit capital cap')


class OpenFundingLedger(TrainingGrantLedger):
    def __init__(self,profile):
        if not isinstance(profile,OpenFundingProfile):raise ValueError('explicit research funding profile required')
        super().__init__(0.,profile)
        self.capital=None  # Undefined/nonbinding, NOT positive infinity or a large substitute.
    def issue(self,g):
        if not isinstance(g,Grant) or type(g.parent_id) is not int or g.parent_id<0 or g.parent_id in self.grants:
            raise ValueError('new unique research ownership identity required')
        if g.side not in ('UP','DOWN') or not g.generation or not g.authority_reference:
            raise ValueError('explicit side/generation/provenance required')
        nonnegative(g.repair_qty,g.add_qty,g.cash_limit)
        if g.add_qty!=0. or g.cash_limit!=0.:
            raise ValueError('No prefunded50/50 or fixed110: begin with zero recorded acquisition claims')
        self.grants[g.parent_id]=g
    def reallocate(self,allocations):
        if allocations:raise ValueError('BUDGET_ALLOCATION_NOT_APPLICABLE_TO_NONBINDING_RESEARCH')
        self.invariants()
    def reserve(self,key,parent_id,route,qty,limit,fee_cap,*,now_ms,market_end_ms):
        # Validate before recording funding so invalid requests cannot create claims.
        if not isinstance(key,str) or not key or key in self.carriers:raise ValueError('carrier identity cannot be rebound')
        if parent_id not in self.grants:raise ValueError('no economic owner')
        if route not in ('PASSIVE','ACTIVE'):raise ValueError('unknown route')
        nonnegative(qty,limit,fee_cap,now_ms,market_end_ms)
        if now_ms>=market_end_ms or qty<=EPS or not 0<limit<1:raise ValueError('invalid order or market end')
        live=[c for c in self.carriers.values() if c.state!='TERMINAL']
        if len(live)>=self.profile.max_live_owners:raise ValueError('RESOURCE_CENSOR_OPEN_OWNER_LIMIT_NOT_HOLD')
        g=self.grants[parent_id];a=self.account(parent_id)
        owners=[dict(key=c.key,side=self.grants[c.parent_id].side,price=c.limit) for c in live]
        if crossing_owners(g.side,limit,owners):raise ValueError('potential own cross including pending')
        required_cash=a['spent']+a['reserved_cash']+qty*limit+fee_cap
        required_add=a['add_filled']+max(0.,a['reserved_qty']+qty-a['repair_remaining'])
        nonnegative(required_cash,required_add)
        # Fields inherited as 'limits' record supported commitments only. They
        # grow from each explicit plan intent, never constrain its monetary size.
        funded=replace(g,cash_limit=max(g.cash_limit,required_cash),add_qty=max(g.add_qty,required_add))
        self.grants[parent_id]=funded
        try:
            super().reserve(key,parent_id,route,qty,limit,fee_cap,now_ms=now_ms,market_end_ms=market_end_ms)
        except Exception:
            self.grants[parent_id]=g
            raise
    def invariants(self):
        assert self.capital is None
        EconomicGrantLedger.invariants(self)
        assert sum(c.state!='TERMINAL' for c in self.carriers.values())<=self.profile.max_live_owners
        for g in self.grants.values():nonnegative(g.add_qty,g.cash_limit)
        return True
    def funding_demand(self):
        accounts=[self.account(pid) for pid in self.grants]
        spent=sum(a['spent'] for a in accounts);pending=sum(a['reserved_cash'] for a in accounts)
        return dict(funding_mode=self.profile.funding_mode,capital_cap=None,
            actual_spent=spent,pending_commitment=pending,current_cash_requirement=spent+pending,
            per_side={g.side:dict(spent=self.account(pid)['spent'],pending=self.account(pid)['reserved_cash'])
                      for pid,g in self.grants.items()})


PARAMETER_NAMES=['exposure_bias','market_mid','depth_imbalance','own_exposure_feedback',
    'phase_exposure','log_desired_gross_shares','gross_phase','log_ticket','ticket_deficit_feedback',
    'quote_depth_bias','quote_inventory_feedback','cancel_tolerance']


def observed_path(source):
    groups={}
    for a in source['targetActions']:
        if a['quote_type']!='BID':raise ValueError('This explicit observed-acquisition scorer does not infer hidden sales')
        if a['side'] not in ('UP','DOWN') or not 0<a['price']<1 or a['shares']<=0:raise ValueError('invalid observed fill')
        groups.setdefault(a['event_ms'],[]).append(a)
    inv={'UP':0.,'DOWN':0.};cost=0.;out=[]
    for t,aa in sorted(groups.items()):
        for a in aa:inv[a['side']]+=a['shares'];cost+=a['price']*a['shares']
        out.append(dict(t=t,inv=dict(inv),cost=cost))
    if not out:raise ValueError('observed training path missing')
    return out


def training_reference(train_sources):
    if set(train_sources)!={2022527,2022538}:raise ValueError('Only the two fixed TRAIN markets may set scaling')
    gross=[p['inv']['UP']+p['inv']['DOWN'] for s in train_sources.values() for p in observed_path(s)]
    qref=float(statistics.median(gross))
    if not math.isfinite(qref) or qref<=0:raise ValueError('positive TRAIN-only share scale required')
    return qref


def initial_parameters(qref):
    return [0.,1.2,.3,-.8,.2,math.log(qref),1.,math.log(30.),.2,-1.2,.5,-.5]


def state_vector(inv,cost,qref):
    if not math.isfinite(qref) or qref<=0:raise ValueError('positive fixed training unit required')
    u,d=inv['UP'],inv['DOWN']
    return [u/qref,d/qref,(u-cost)/qref,(d-cost)/qref]


def path_loss(states,source,terminal,qref):
    times=[s['t'] for s in states]
    if times!=sorted(times):raise ValueError('nonmonotone own state')
    teacher=observed_path(source);terms=[[],[],[],[]];obs=[]
    for point in teacher:
        j=bisect.bisect_right(times,point['t'])-1
        own=states[j] if j>=0 else dict(inv={'UP':0.,'DOWN':0.},cost=0.)
        tv=state_vector(point['inv'],point['cost'],qref);ov=state_vector(own['inv'],own['cost'],qref)
        for k in range(4):terms[k].append((ov[k]-tv[k])**2)
        obs.append(dict(t=point['t'],target_observed_vector=tv,our_vector=ov,teacher_action=None))
    means=[math.fsum(a)/len(a) for a in terms]
    return dict(total_loss=math.fsum(means)/4.,path_mse=math.fsum(means)/4.,coordinate_mse=means,
        coordinate_names=['UP_SHARES','DOWN_SHARES','UP_SETTLEMENT_BRANCH','DOWN_SETTLEMENT_BRANCH'],
        fixed_train_share_unit=qref,utilization_reward_present=False,monetary_cap_in_score=None,
        loss_clipping=False,target_event_batches=len(teacher),
        target_observed_final_inv=teacher[-1]['inv'],target_observed_final_cost=teacher[-1]['cost'],
        terminal_downside_diagnostic=max(0.,terminal['cost']-min(terminal['inv'].values())),
        pending_cash_diagnostic=terminal['pending_cash'],target_original_orders_known=False,observations=obs)


class OpenFundingWholePolicy:
    provenance='TRAINABLE_WHOLE_POLICY_NONBINDING_VIRTUAL_FUNDING_NOT_TARGET_ACTION_LABELS'
    def __init__(self,theta,variant):
        if len(theta)!=12 or not all(math.isfinite(x) for x in theta):raise ValueError('finite12parameter policy required')
        self.theta=list(map(float,theta));self.policy_id='OPEN_FUNDING_WHOLE_POLICY_V1:'+variant
        self.continuation_id=self.policy_id+':COMPLETE_EPISODE';self.calls=0
        self.declines={};self.multi_new_plans=0;self.bidirectional_plans=0
    def decline(self,r):self.declines[r]=self.declines.get(r,0)+1
    def produce(self,f):
        self.calls+=1;ops=[];v=f['own_view'];ledger=f['ledger']
        if ledger.capital is not None:raise ValueError('This learning policy must not silently consume a capped world')
        live={k:c for k,c in ledger.carriers.items() if c.state!='TERMINAL'}
        if f['t']>=f['end']:
            for k,c in live.items():
                if c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):
                    ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='ACTUAL_MARKET_END'))
            return envelope(f,self,ops)
        x=stable_features(f)
        if f['t']<f['start'] or x is None:return envelope(f,self,[])
        w=self.theta;phase=2*x['phase']-1
        exposure=math.tanh(w[0]+w[1]*(2*x['mid']-1)+w[2]*x['depth_imbalance']+w[3]*x['own_net']+w[4]*phase)
        up=(1.+exposure)/2.;share={'UP':up,'DOWN':1.-up}
        gross=math.exp(w[5]+w[6]*phase)
        if not math.isfinite(gross):raise ArithmeticError('NUMERIC_CENSOR_NOT_A_FINANCIAL_LIMIT')
        desired={s:gross*share[s] for s in share};pid={'UP':1,'DOWN':2}
        tick=f['world_profile']['tick'];step=f['world_profile']['quantity_step']
        prices={};tickets={};deficits={};mid={'UP':x['mid'],'DOWN':1.-x['mid']}
        for s in pid:
            a=ledger.account(pid[s]);owned=v['inv'][s]+a['reserved_qty']
            deviation=(owned-desired[s])/(1.+desired[s]);bid=x['up_bid'] if s=='UP' else round(1.-x['up_ask'],10)
            prices[s]=round(math.floor((bid-softplus(w[9]+w[10]*deviation)*tick+1e-10)/tick)*tick,10)
            tickets[s]=math.exp(w[7]+w[8]*abs(deviation))
            if not math.isfinite(tickets[s]):raise ArithmeticError('NUMERIC_CENSOR_NOT_A_FINANCIAL_LIMIT')
            deficits[s]=desired[s]-owned
        draft=deepcopy(ledger)
        for k,c in live.items():
            s=ledger.grants[c.parent_id].side
            stale=abs(c.limit-prices[s])>tick*(1.+softplus(w[11]))+1e-9
            surplus=deficits[s]<-tickets[s]*sig(w[11])
            if (stale or surplus) and c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):
                ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='LEARNED_DESIRED_STATE_OR_QUOTE'))
                draft.request_cancel(k)
        slots=f['world_profile']['max_live_owners']-len(live);n=v['n'];new=[]
        for s in sorted(pid,key=lambda s:(-max(0.,deficits[s])*mid[s],s)):
            if slots<=0:self.decline('RESOURCE_CENSOR');break
            p=prices[s];ask=passive_ask(f['book'],s)
            if not tick<=p<1 or ask is None or p>=ask-1e-10:self.decline('PRICE_OR_POSTONLY_LEGALITY');continue
            # Only policy-desired increment/ticket/venue grid, never cash/price.
            qty=max(0.,min(tickets[s],deficits[s]));qty=round(math.floor((qty+1e-10)/step)*step,8)
            try:validate_size(f['world_profile']['asset'],'PASSIVE',p,qty,quantity_step=step)
            except ValueError:self.decline('POLICY_INCREMENT_BELOW_VENUE_MINIMUM');continue
            key=f'{s}_{n}'
            try:draft.reserve(key,pid[s],'PASSIVE',qty,p,0.,now_ms=f['t'],market_end_ms=f['end'])
            except ValueError as e:self.decline('JOINT_FEASIBILITY:'+str(e));continue
            new.append(dict(kind='NEW',key=key,parent_id=pid[s],side=s,price=p,qty=qty,role='OPEN_FUNDING_JOINT_CONTINUATION'))
            n+=1;slots-=1
        self.multi_new_plans+=len(new)>1;self.bidirectional_plans+=len({a['side'] for a in new})>1
        return envelope(f,self,ops+new)


def self_tests():
    passed=[]
    def test(name,ok):
        if not ok:raise AssertionError(name)
        passed.append(name)
    def ledger():
        l=OpenFundingLedger(OpenFundingProfile())
        for p,s in ((1,'UP'),(2,'DOWN')):l.issue(Grant(p,'RESEARCH',s,0.,0.,0.,'USER_AUTHORIZED_VIRTUAL_FUNDING'))
        return l
    l=ledger();test('capital_cap_is_null_not_infinity',l.capital is None and l.profile.capital_cap is None)
    test('initial_side_financing_and_add_claims_zero',all(g.cash_limit==g.add_qty==0 for g in l.grants.values()))
    for i,q in enumerate((18.,110.,300.,100000.)):
        l.reserve(str(i),1,'PASSIVE',q,.4,0.,now_ms=299999,market_end_ms=300000)
    test('above100_cash_and110_qty_is_not_censored',l.account(1)['reserved_cash']>10000. and l.account(1)['reserved_qty']>100000.)
    l.invariants();test('same_side_no50_cash_ceiling',l.grants[1].cash_limit>40000. and l.grants[2].cash_limit==0.)
    before=l.funding_demand();l.request_cancel('2')
    test('cancel_intent_keeps_cash_claim',l.funding_demand()==before)
    l.confirm_cumulative('2',.5,.2);test('subminimum_partial_fill_kept',l.account(1)['add_filled']==.5)
    before=l.funding_demand();l.confirm_cumulative('2',.5,.2)
    test('duplicate_receipt_no_extra_spend',l.funding_demand()==before)
    l.confirm_terminal('2',filled=.5,payment=.2)
    test('terminal_releases_unfilled_not_actual_spend',l.account(1)['spent']==.2 and l.carriers['2'].reserved_cash==0.)
    test('finite_accounts_serialize',bool(json.dumps(l.funding_demand(),allow_nan=False)))
    for name,call in [('duplicate_owner',lambda:l.reserve('1',1,'PASSIVE',18.,.4,0.,now_ms=1,market_end_ms=300000)),
        ('unknown_owner',lambda:l.reserve('ghost',99,'PASSIVE',18.,.4,0.,now_ms=1,market_end_ms=300000)),
        ('own_cross',lambda:l.reserve('DOWN',2,'PASSIVE',18.,.61,0.,now_ms=1,market_end_ms=300000)),
        ('real_market_end',lambda:l.reserve('late',1,'PASSIVE',18.,.4,0.,now_ms=300000,market_end_ms=300000)),
        ('infinite_request',lambda:l.reserve('inf',1,'PASSIVE',float('inf'),.4,0.,now_ms=1,market_end_ms=300000)),
        ('hidden_budget_reallocation',lambda:l.reallocate([1]))]:
        try:call()
        except ValueError:test(name+'_still_rejected',True)
        else:raise AssertionError(name)
    from tools.minimal_student_training_world_v2 import TrainingPlanGateway,TrainingPlan
    from tools.minimal_student_system_plan_v1 import PlanAction
    g=TrainingPlanGateway(ledger(),asset='BTC',policy_id='P',capabilities=('PASSIVE',))
    plan=TrainingPlan('D',g.snapshot_id(),'P','ALL',(PlanAction('NEW','A',1,'PASSIVE',.5,300.),))
    g.commit(plan,now_ms=200000,market_end_ms=300000);g.record_send('A','SENT',evidence='TEST')
    test('native_gateway_can_authorize150_without_new_cap',g.ledger.account(1)['reserved_cash']==150. and g.ledger.capital is None)
    fake={'targetActions':[dict(event_ms=1,side='UP',shares=30.,price=.4,quote_type='BID'),dict(event_ms=1,side='DOWN',shares=30.,price=.5,quote_type='BID')]}
    exact=[dict(t=1,inv={'UP':30.,'DOWN':30.},cost=27.)];term=dict(inv={'UP':30.,'DOWN':30.},cost=27.,pending_cash=0.)
    test('exact_observed_path_zero_loss',path_loss(exact,fake,term,30.)['total_loss']==0.)
    worse=deepcopy(exact);worse[0]['cost']=54.
    test('spending_without_more_shares_not_rewarded',path_loss(worse,fake,term,30.)['total_loss']>0.)
    test('no_trade_not_perfect_imitation',path_loss([],fake,term,30.)['total_loss']>0.)
    v=state_vector({'UP':3000.,'DOWN':1000.},300.,30.)
    test('loss_values_not_clipped_at1',max(v)>1.)
    test('no100_in_score_units',path_loss(exact,fake,term,30.)['monetary_cap_in_score'] is None)
    return dict(passed=len(passed),names=passed)
