"""Mirror economic decisions and physical routing on existing V32 observations; no native."""
import math
import sys
from prepare_btc5m_transfer_structural_v1 import *
from hft244_pair_route_legality_v1 import crossing_owners


def swap(x):
    if isinstance(x,str):
        if x in ('UP','DOWN'): return 'DOWN' if x=='UP' else 'UP'
        if x.startswith(('UP_','DOWN_')):
            a,b=x.split('_',1);return swap(a)+'_'+b
        return x
    if isinstance(x,dict):return {swap(k):swap(v) for k,v in x.items()}
    if isinstance(x,list):return [swap(v) for v in x]
    return x


def same(a,b):
    if isinstance(a,dict):
        assert a.keys()==b.keys(),(a.keys(),b.keys())
        for k in a:same(a[k],b[k])
    elif isinstance(a,list):
        assert len(a)==len(b)
        for x,y in zip(a,b):same(x,y)
    elif isinstance(a,(float,int)) and not isinstance(a,bool):
        assert math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-7),(a,b)
    else:assert a==b,(a,b)


def main():
    sys.path.insert(0,str(PACKAGE))
    import roles_runtime
    roles=roles_runtime.roles
    new={n:load('new_'+n,PACKAGE/(n+'.py')) for n in ('demand_gate','parallel_gate','active_opportunity','coordination','commitment_repair')}
    old={n:load('old_'+n,PARENT/(n+'.py')) for n in new}
    tr=read(R/'lan_worker_returns/fixed15-core-loop-2026085-addition-growth-20260913-v1/clock_trace.json.gz')
    checked=0
    for row in tr['demand_rows']:
        state=row['state'];desired=row['original_desired'];price=row['eligibility']['price']
        roles.configure('KNOWN_FINAL_DIRECTION','UP')
        base=old['demand_gate'].economic_capacity(state,desired,price,15.,.01)
        same(base,new['demand_gate'].economic_capacity(state,desired,price,15.,.01))
        roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
        same(swap(base),new['demand_gate'].economic_capacity(swap(state),swap(desired),price,15.,.01))
        for side in ('UP','DOWN'):
            base=old['parallel_gate'].capacity(state,side,price,15.,'PARALLEL_QUANTITY')
            roles.configure('KNOWN_FINAL_DIRECTION','UP')
            same(list(base),list(new['parallel_gate'].capacity(state,side,price,15.,'PARALLEL_QUANTITY')))
            roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
            same(swap(list(base)),list(new['parallel_gate'].capacity(swap(state),swap(side),price,15.,'PARALLEL_QUANTITY')))
        checked+=1
    # Original self-tests include legal versus crossed pending, same-plan cancel and variable Active.
    mirrors={}
    for side in ('UP','DOWN'):
        roles.configure('KNOWN_FINAL_DIRECTION',side)
        for name,module in new.items():
            if name in ('active_opportunity','coordination','commitment_repair'):module.self_test(crossing_owners)
            else:module.self_test()
        mirrors[side]='PASS'
    # Physical native receipt mapping never uses role orientation.
    raw=dict(sequence=1,order_id=7,side=-1,price=.91)
    for side in ('UP','DOWN'):
        roles.configure('KNOWN_FINAL_DIRECTION',side)
        record=new['demand_gate'].full_receipts({1:raw},{'DOWN_7':dict(n=7)})[0]
        assert record['key']=='DOWN_7' and abs(record['contractPrice']-.09)<1e-9
    # Pending-cancel state and physical ids are read-only on selection.
    inv=dict(UP=0.,DOWN=15.)
    roles.configure('NO_DIRECTION',None)
    f=dict(t=1,index=1,own_view=dict(inv=inv),pending_qty=dict(UP=15.,DOWN=0.))
    roles.observe(f);assert roles.side=='DOWN' and f['own_view']['inv'] is inv
    assert roles.pid(roles.strong)==2 and roles.pid(roles.weak)==1
    out=dict(status='PASS',baseline_state_rows=checked,pure_capacity_comparisons=checked*6,
        mirrored_module_suites=mirrors,raw_native_side_independent=True,physical_parent_mapping=True,
        pending_and_own_objects_not_mutated=True,native_jobs=0)
    dump('COMPONENT',out);print(json.dumps(out))


if __name__=='__main__':main()
