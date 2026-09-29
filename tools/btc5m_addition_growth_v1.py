"""Scale only post-repair directional demand growth; no repair cash budget."""
EPS=1e-8


def factor(inv,cost,strong):
    weak='DOWN' if strong=='UP' else 'UP'
    gain=max(0.,inv[strong]-cost);loss=max(0.,cost-inv[weak])
    return 1. if loss<=EPS else gain/(gain+loss)


class AdditionGrowth:
    def __init__(self,strong='UP'):
        assert strong in ('UP','DOWN')
        self.strong=strong;self.anchor=None;self.rows=[]

    def update(self,t,inv,cost,desired):
        strong=self.strong;weak='DOWN' if strong=='UP' else 'UP'
        if self.anchor is None and inv[weak]>EPS and inv[strong]>inv[weak]+EPS and inv[weak]-cost<-EPS:
            self.anchor=dict(t=int(t),inv=dict(inv),cost=float(cost),original_strong_desired=desired[strong])
        weight=factor(inv,cost,strong);effective=dict(desired)
        if self.anchor is not None:
            initial=self.anchor['original_strong_desired']
            effective[strong]=min(desired[strong],initial+weight*max(0.,desired[strong]-initial))
        assert effective[weak]==desired[weak] and 0<=effective[strong]<=desired[strong]+EPS
        row=dict(t=int(t),strong=strong,inv=dict(inv),cost=float(cost),original_desired=dict(desired),
            effective_desired=effective,weight=weight,anchor=self.anchor)
        self.rows.append(row);return dict(effective)

    def apply(self,frame,desired):
        own=frame['own_view']
        return self.update(frame['t'],own['inv'],own['cost'],desired)


def instrument(source,replace):
    marker='self.intent=_ExposureIntent(_MODE)'
    source=replace(source,marker,marker+';self.addition_growth=_AdditionGrowth()')
    marker='    self.clock_observations.append(dict('
    return replace(source,marker,'    desired=self.addition_growth.apply(f,desired)\n'+marker)


def self_test():
    a=AdditionGrowth();d=dict(UP=200.,DOWN=100.)
    assert a.update(1,dict(UP=0.,DOWN=0.),0.,d)==d and a.anchor is None
    assert a.update(2,dict(UP=100.,DOWN=0.),80.,d)==d and a.anchor is None
    s=dict(UP=100.,DOWN=40.)
    assert a.update(3,s,80.,d)==d and a.anchor['t']==3
    assert abs(a.update(4,s,80.,dict(UP=500.,DOWN=300.))['UP']-300.)<EPS
    assert a.update(5,dict(UP=100.,DOWN=50.),81.,dict(UP=500.,DOWN=300.))['UP']>300.
    assert a.update(6,dict(UP=110.,DOWN=40.),88.,dict(UP=500.,DOWN=300.))['UP']<300.
    assert a.update(7,dict(UP=100.,DOWN=90.),80.,dict(UP=500.,DOWN=300.))['UP']==500.
    assert a.update(8,s,110.,dict(UP=500.,DOWN=300.))['UP']==200.
    assert a.update(9,s,80.,dict(UP=150.,DOWN=300.))['UP']==150.
    b=AdditionGrowth('DOWN')
    for row in a.rows:
        swapped=lambda x:dict(UP=x['DOWN'],DOWN=x['UP'])
        result=b.update(row['t'],swapped(row['inv']),row['cost'],swapped(row['original_desired']))
        assert result==swapped(row['effective_desired'])
    for cut in (1,3,5,len(a.rows)):
        c=AdditionGrowth()
        for row in a.rows[:cut]:assert c.update(row['t'],row['inv'],row['cost'],row['original_desired'])==row['effective_desired']
    # No repeated-frame accumulation and no previous repair-gain cash account.
    c=AdditionGrowth();c.update(3,s,80.,d)
    result=c.update(4,s,80.,dict(UP=500.,DOWN=300.))
    assert c.update(4,s,80.,dict(UP=500.,DOWN=300.))==result
    return dict(status='PASS',side_symmetry=True,prefix_invariant=True,finite_bootstrap=True,
        current_loss_response=True,repair_side_desired_unchanged=True,no_gain_bank=True,no_time_trigger=True)


if __name__=='__main__':print(self_test())
