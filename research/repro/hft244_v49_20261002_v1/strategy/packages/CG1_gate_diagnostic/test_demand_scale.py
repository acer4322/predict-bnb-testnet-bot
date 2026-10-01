"""Pure lifecycle tests at the real finite-demand boundary, no native import."""
import copy,importlib.util,json,sys
from pathlib import Path
P=Path(__file__).resolve().parent;BASE=P.parent/'v12g_fresh30_generalization_20260927_v45/base'
sys.path.insert(0,str(BASE));sys.path.insert(0,str(P))
from demand_scale import targets,instrument

def run():
    from roles_runtime import roles
    import demand_gate as demand
    roles.configure('NO_DIRECTION',None)
    checks=[]
    for side in (None,'UP','DOWN'):
        x=dict(UP=500.,DOWN=100.)
        for mode in ('OFF','OPEN','POST','BOTH'):
            y=targets(x,side,mode,.2)
            yes=mode=='BOTH' or(mode=='OPEN' and side is None)or(mode=='POST' and side is not None)
            assert y==({s:v*.2 for s,v in x.items()} if yes else x)
            assert x==dict(UP=500.,DOWN=100.)
    checks.append('phase_scale_and_off_identity_without_mutating_inputs')
    for side in ('UP','DOWN'):
        roles.side=side;s,w=roles.strong,roles.weak
        state=dict(inv={s:100.,w:40.},payoff={s:20.,w:-40.},pending_qty={s:0.,w:10.},pending_cash={s:0.,w:3.},owners=[])
        initial=copy.deepcopy(state)
        gate=demand.SingleRepairDemand(dict(t=0,state=dict(owners=[])),'AUTO_REPAIR',lambda f,l:state)
        goal=demand.old.FiniteGoal(state,30.);gate.goal=goal;gate.current_work=dict(id=1)
        original={s:500.,w:100.};scaled=targets(original,side,'POST',.2)
        frame=dict(t=1,end=100,ledger=object(),world_profile=dict(asset='BTC'))
        # Existing goal needs 80 weak shares even though new raw target is 20.
        out=gate.apply(frame,scaled,.3,30.,.01,.31,lambda *a,**k:None,lambda *a:[])
        assert out[w]==80. and goal.target==80. and goal.initial_pending==10.
        assert gate.rows[-1]['progress']['remaining_confirmed']==40.
        assert gate.rows[-1]['progress']['unreserved_need']==30.
        assert state==initial and scaled[w]==20.
        # Cancel intent / repeated frames without new receipts cannot erase debt.
        out2=gate.apply(dict(frame,t=2),scaled,.3,30.,.01,.31,lambda *a,**k:None,lambda *a:[])
        assert out2[w]==80. and goal.target==80. and state==initial
        # Real receipts reduce remaining; the target never follows new raw demand.
        state['inv'][w]=45.;state['pending_qty'][w]=5.;state['pending_cash'][w]=1.5
        progress=goal.update(state,3,100)
        assert progress['remaining_confirmed']==35. and progress['unreserved_need']==30.
        # Pending all required repair prevents another economic ticket.
        state['pending_qty'][w]=55.
        assert not demand.economic_capacity(state,scaled,.3,30.,.01)['economically_eligible']
    checks.append('both_physical_sides_existing_finite_goal_and_pending_retained_after_raw_shrink')
    source="desired={s:gross*share[s] for s in ('UP','DOWN')}\ndesired=self.addition_growth.apply(f,desired)\ndesired=self.demand.apply(f,desired)"
    assert instrument(source).index("__import__('demand_scale')")<instrument(source).index('self.addition_growth')
    for broken in (source.replace('gross*share[s]','0'),source+source):
        try:instrument(broken)
        except AssertionError:pass
        else:raise AssertionError('ambiguous patch accepted')
    roles.configure('NO_DIRECTION',None)
    assert 'hftbacktest' not in sys.modules
    return dict(status='PASS',checks=checks,native=0)

if __name__=='__main__':print(json.dumps(run()))
