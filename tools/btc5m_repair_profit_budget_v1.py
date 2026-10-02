"""Diagnostic cash budget for weak-side orders, with confirmed profit only."""
import math


def budget(inv,paid,pending_down_cash,price,retention,step):
    gross=inv['UP']-paid['UP']
    available=max(0.,(1-retention)*gross-paid['DOWN']-pending_down_cash)
    cap=max(0.,round(math.floor((available/price+1e-10)/step)*step,8))
    return dict(confirmed_up_gross=gross,paid=dict(paid),pending_down_cash=pending_down_cash,
                retention=retention,available_cash=available,quantity_cap=cap)


def make_capacity(base):
    class ProfitBudget(base):
        def __init__(self,selection,mode,episode_mode,retention):
            super().__init__(selection,mode,episode_mode)
            assert retention in (0.,.5)
            self.retention=retention;self.budget_rows=[]

        def cap(self,frame,side,price,qty,draft):
            original=super().cap(frame,side,price,qty,draft)
            if side!='DOWN':return original
            paid={s:math.fsum(float(c.payment)+float(c.fees) for c in draft.carriers.values()
                             if draft.grants[c.parent_id].side==s) for s in ('UP','DOWN')}
            pending=math.fsum(max(0.,float(c.qty)-float(c.filled))*float(c.limit)
                            for c in draft.carriers.values() if c.state!='TERMINAL' and draft.grants[c.parent_id].side=='DOWN')
            inv={s:math.fsum(float(c.filled) for c in draft.carriers.values()
                            if draft.grants[c.parent_id].side==s) for s in ('UP','DOWN')}
            info=budget(inv,paid,pending,price,self.retention,float(frame['world_profile']['quantity_step']))
            admitted=min(original,info['quantity_cap']) if self.retention else original
            self.budget_rows.append(dict(t=int(frame['t']),side=side,price=price,requested=qty,
                                        old_quantity_cap=original,admitted=admitted,inv=inv,**info))
            return admitted
    return ProfitBudget


def self_test():
    inv=dict(UP=100.,DOWN=20.); paid=dict(UP=60.,DOWN=10.)
    # Half of confirmed40 gross is20; paid10 plus still-reserved10 uses it all.
    assert budget(inv,paid,10.,.5,.5,.01)['quantity_cap']==0
    # Only a terminal release creates room; unfilled UP carries create no credit.
    assert budget(inv,paid,0.,.5,.5,.01)['quantity_cap']==20
    assert budget(inv,dict(UP=60.,DOWN=25.),0.,.5,.5,.01)['quantity_cap']==0
    assert budget(dict(UP=0.,DOWN=0.),dict(UP=0.,DOWN=0.),0.,.5,.5,.01)['quantity_cap']==0


if __name__=='__main__':self_test();print('PASS')
