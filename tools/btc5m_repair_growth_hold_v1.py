"""A service-conditioned ceiling on growth, not on repair cash or base demand."""
from copy import deepcopy
from roles_runtime import roles

EPS=1e-8


def decide(growth, arm, receipt, weak_payoff, released=False):
    weight=growth['weight'];applied=weight;out=dict(growth['effective_desired'])
    reason='UNARMED' if arm is None else 'RELEASED' if released else 'WORK_UNRESOLVED'
    if arm is not None and not released:
        terminal=receipt is not None and receipt['state']=='TERMINAL'
        if terminal and receipt['filled']<=EPS:
            released=True;reason='ZERO_FILL_TERMINAL'
        elif terminal and weak_payoff>=arm['goal']-EPS:
            released=True;reason='CONFIRMED_WORK_RESTORED_AND_SERVICE_TERMINAL'
        else:
            applied=min(weight,arm['ceiling'])
            initial=growth['anchor']['original_strong_desired'];strong=growth['strong']
            raw=growth['original_desired'][strong]
            out[strong]=min(raw,initial+applied*max(0.,raw-initial))
            reason='GROWTH_CEILING_BINDING' if applied<weight-EPS else 'CURRENT_RISK_ALREADY_TIGHTER'
    assert all(out[s]<=growth['effective_desired'][s]+EPS for s in out)
    weak='DOWN' if growth['strong']=='UP' else 'UP'
    assert out[weak]==growth['effective_desired'][weak]
    return dict(reason=reason,released=released,unheld_weight=weight,applied_weight=applied,
        effective_desired=out,repair_cash_gate=False)


class GrowthHold:
    def __init__(self):
        self.armed=None;self.released=False;self.rows=[];self.events=[]

    def arm(self,frame,producer,work,op):
        assert self.armed is None
        growth=producer.addition_growth.rows[-1]
        assert growth['t']==frame['t'] and growth['anchor'] is not None
        self.armed=dict(key=op['key'],work_id=work['id'],goal=work['anchor_floor'],
            ceiling=growth['weight'],t=int(frame['t']),index=int(frame['index']))
        self.events.append(dict(kind='ARM',**self.armed))

    def apply(self,frame,producer,desired):
        growth=producer.addition_growth.rows[-1]
        assert growth['effective_desired']==desired
        own=frame['own_view'];payoff=own['inv'][roles.weak]-own['cost']
        owner=frame['ledger'].carriers.get(self.armed['key']) if self.armed else None
        receipt=dict(state=owner.state,filled=float(owner.filled)) if owner is not None else None
        decision=decide(growth,self.armed,receipt,payoff,self.released)
        if decision['released'] and not self.released:
            self.events.append(dict(kind='RELEASE',t=int(frame['t']),index=int(frame['index']),reason=decision['reason']))
        self.released=decision['released']
        self.rows.append(dict(t=int(frame['t']),index=int(frame['index']),arm=deepcopy(self.armed),
            receipt=receipt,confirmed_weak_payoff=payoff,input_desired=dict(desired),
            effective_desired=decision['effective_desired'],decision=decision))
        return dict(decision['effective_desired'])


def instrument(source,replace):
    marker='self.addition_growth=_AdditionGrowth()'
    source=replace(source,marker,marker+';self.growth_hold=_GrowthHold()')
    marker='    desired=self.addition_growth.apply(f,desired)'
    return replace(source,marker,marker+'\n    desired=self.growth_hold.apply(f,self,desired)')
