"""Transfer direction seam only. Not yet connected to the native manager."""
EPS=1e-8


class DirectionCondition:
    def __init__(self,mode,oracle_side=None):
        assert mode in ('KNOWN_FINAL_DIRECTION','NO_DIRECTION')
        assert (oracle_side in ('UP','DOWN')) if mode=='KNOWN_FINAL_DIRECTION' else oracle_side is None
        self.mode=mode;self.oracle_side=oracle_side;self.side=oracle_side;self.birth=None

    def observe(self,t,inv):
        net=inv['UP']-inv['DOWN']
        if self.mode=='NO_DIRECTION' and self.side is None and abs(net)>EPS:
            self.side='UP' if net>0 else 'DOWN'
            self.birth=dict(t=int(t),inv=dict(inv),source='FIRST_NONZERO_CONFIRMED_OUR_NET')
        return self.side


def self_test():
    for first in ('UP','DOWN'):
        other='DOWN' if first=='UP' else 'UP';a=DirectionCondition('KNOWN_FINAL_DIRECTION',other);b=DirectionCondition('NO_DIRECTION')
        assert a.observe(1,dict(UP=0.,DOWN=0.))==other and b.observe(1,dict(UP=0.,DOWN=0.)) is None
        inv={first:15.,other:0.};assert b.observe(2,inv)==first and a.observe(2,inv)==other
        assert b.observe(3,dict(UP=15.,DOWN=15.))==first
        assert b.observe(4,{first:15.,other:30.})==first
        assert b.birth['t']==2
    for label in ('UP','DOWN'):
        try:DirectionCondition('NO_DIRECTION',label)
        except AssertionError:pass
        else:raise AssertionError('No-direction constructor must reject a Target label')
    prefix=[(1,dict(UP=0.,DOWN=0.)),(2,dict(UP=0.,DOWN=15.)),(3,dict(UP=100.,DOWN=15.))]
    whole=DirectionCondition('NO_DIRECTION');expected=[whole.observe(t,i) for t,i in prefix]
    for cut in range(1,len(prefix)+1):
        c=DirectionCondition('NO_DIRECTION');assert [c.observe(t,i) for t,i in prefix[:cut]]==expected[:cut]
    return dict(status='PASS',mode_contract=True,own_only_direction=True,prefix_invariance=True,side_symmetry=True,
        target_label_rejected_in_no_direction=True,native_integration=False,
        limitation='First OUR net is a frozen causal baseline, not an identified Target direction strategy.')


if __name__=='__main__':print(self_test())
