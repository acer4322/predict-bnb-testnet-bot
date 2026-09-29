"""Local component and saved-state examination; never a native rollout."""
import json
import math
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from itertools import product

import btc5m_demand_slippage_v1 as candidate
import btc5m_pending_repair_pair_v1 as previous

STEM = 'BTC5M_DEMAND_SLIPPAGE_V1_20260913'


def dump(tag, value):
    (previous.R/(STEM+'_'+tag+'.json')).write_text(
        json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def exhaustive(book, need, capacity, mode):
    """Independent exhaustive .01-quantity oracle, not the greedy algorithm."""
    bps = {'BEST_ASK':0,'FIXED_10_PERCENT':1000,'FIXED_15_PERCENT':1500}.get(mode)
    cap = .99 if bps is None else math.floor((book[0][0]*(1+bps/10000)+1e-10)/.01)*.01
    levels = [(p,q) for p,q in book if p <= cap+1e-9]
    max_units = math.floor((min(capacity,sum(q for _,q in levels))+1e-8)*100)
    best, cost, last = 0.,0.,None
    for units in range(1,max_units+1):
        q, remaining, total, deepest = units/100,units/100,0.,None
        for p,size in levels:
            take = min(remaining,size)
            if take > 1e-9:
                total += p*take
                deepest = p
                remaining -= take
        if q-total <= need+1e-8:
            best,cost,last=q,total,deepest
    return best,cost,(last if mode=='DEMAND_DEPTH' else cap)


def components():
    counts=Counter()
    for start,depths,need,capacity,mode in product(
            (.07,.4,.8),((.25,.5,1.),(1.,2.,3.),(2.,.1,.1)),
            (.02,.2,.75,2.,4.),(.2,1.5,5.),candidate.MODES):
        book=[(round(start+i*.01,2),q) for i,q in enumerate(depths)]
        actual=candidate.solve(book,need,capacity,mode)
        q,cost,limit=exhaustive(book,need,capacity,mode)
        assert abs(actual['quantity']-q)<1e-8,(book,need,capacity,mode,actual,q)
        if q>0:
            assert abs(actual['expected_cost']-cost)<1e-8
            assert abs(actual['price_limit']-limit)<1e-8
            assert abs(actual['quantity']-actual['expected_cost']-actual['expected_weak_lift'])<1e-8
            assert actual['worst_reserved_cost']+1e-8>=actual['expected_cost']
            assert actual['expected_weak_lift']<=need+1e-8
        counts['exhaustive_depth_quantity_cost_cases']+=1
    for side,state_name,can in product(('UP','DOWN'),('SUBMITTED','CANCEL_PENDING','UNKNOWN','TERMINAL'),(False,True)):
        other='DOWN' if side=='UP' else 'UP'
        terminal=state_name=='TERMINAL'
        state=dict(inv={side:100.,other:250.},payoff={side:-50.,other:100.},
            pending_qty={side:10.,other:0. if terminal else 15.},
            pending_cash={side:.7,other:0. if terminal else 13.8},
            owners=[dict(key='opposite',side=other,limit=.92,qty=15.,state=state_name)])
        ops=[dict(kind='CANCEL',key='opposite'),dict(kind='NEW',key='same_plan',side=other,price=.92,qty=15.)]
        original=deepcopy((state,ops))
        ctx=candidate.context(state,ops,side,10.,True)
        assert ctx['capacity']==140. and ctx['weak_pending_qty']==10.
        decision=candidate.solve([[.07,20],[.08,200]],ctx['need'],ctx['capacity'])
        assert decision['price_limit']==.08
        control=candidate.coordination(decision,ctx,state,ops,{'opposite':can})
        assert not control['active_new_allowed'] and control['suppress_new']==['same_plan']
        assert ('opposite' in control['conflicts'])==(not terminal)
        assert ('opposite' in control['cancel'])==(state_name=='SUBMITTED' and can)
        assert original==(state,ops)
        counts['side_owner_terminal_coordination_cases']+=1
    book=[[.07,30],[.08,30],[.09,200]]
    last_q,last_limit=0.,0.
    for need in (1,10,30,50,100,200):
        actual=candidate.solve(book,need,250)
        assert actual['quantity']>=last_q and actual['price_limit']>=last_limit
        last_q,last_limit=actual['quantity'],actual['price_limit']
        counts['demand_monotonicity_cases']+=1
    for book in ([[.071,20]],[[.07,-1]],[[1.,20]],[[float('nan'),20]]):
        try:
            candidate.solve(book,10,100)
        except ValueError:
            counts['invalid_book_rejections']+=1
        else:
            raise AssertionError('invalid input accepted')
    return dict(status='PASS',checks=dict(counts),total=sum(counts.values()),
        native_jobs=0,interpretation='Synthetic local component cases, not markets or learned model fits')


def main():
    protocol=previous.read(previous.R/(STEM+'_PROTOCOL.json'))
    assert protocol['current_phase_max_native_jobs']==0
    component=components();dump('COMPONENT',component)
    package=previous.package('current');manifest=previous.read(package/'manifest.json')
    assert all(previous.sha(package/k)==v for k,v in manifest['files'].items())
    folder=previous.R/'lan_worker_returns'/previous.job('current')['job_id']
    trace_path=folder/'clock_trace.json.gz'
    trace=previous.read(trace_path)
    public_path=package/'inputs/public_1977248.json.gz'
    public=previous.read(public_path)
    books={direction['index']:b for direction,b in zip(trace['direction_rows'],public['books'])}
    indices=[i['index'] for i in trace['intent'] if i['t']>trace['coordination_submissions'][1]['t']]
    assert len(indices)==len(trace['renewal_rows'])==415
    points=[];counts=Counter();actual_continuation=None
    for index,row in zip(indices,trace['renewal_rows']):
        work=row['status']['work']
        if work is None:
            continue
        book=books[index]
        assert book['received_ms']==row['t'] and book['best_ask']==row['active_ask']
        for pending in (False,True):
            ctx=candidate.context(row['state'],row['original_operations'],'UP',work['anchor_floor'],pending)
            results={}
            for mode in candidate.MODES:
                decision=candidate.solve(book['asks'],ctx['need'],ctx['capacity'],mode)
                # A saved monetary snapshot does not expose cancellable transport
                # flags. Never assume existing owners can be cancelled now.
                coord=candidate.coordination(decision,ctx,row['state'],row['original_operations'],{})
                results[mode]=dict(quote=decision,coordination=coord)
                counts[('PENDING' if pending else 'CURRENT')+'_'+mode+'_'+coord['status']]+=1
            point=dict(index=index,t=row['t'],scenario='PENDING_BURDEN' if pending else 'CURRENT_ONLY',
                work_id=work['id'],reference=work['anchor_floor'],need=ctx['need'],capacity=ctx['capacity'],
                current_pending_strong_cash=ctx['strong_pending_cash'],best_ask=book['best_ask'],
                previous_service_state=row['previous_service_state'],
                original_decision=row['decision'],original_submitted=row['submitted'],modes=results)
            points.append(point)
            if index==1044 and not pending:
                actual_continuation=point
    assert actual_continuation is not None
    baseline=actual_continuation['original_decision']
    adaptive=actual_continuation['modes']['DEMAND_DEPTH']
    assert adaptive['coordination']['status']=='READY'
    assert adaptive['quote']['price_limit']==baseline['active_ask']==.07
    assert adaptive['quote']['quantity']==baseline['quantity']==87.79
    pending_point=next(p for p in points if p['index']==1044 and p['scenario']=='PENDING_BURDEN')
    assert pending_point['modes']['DEMAND_DEPTH']['quote']['price_limit']==.09
    assert pending_point['modes']['DEMAND_DEPTH']['coordination']['status']=='WAIT_TERMINAL_AND_RECOMPUTE'
    # New last-service selector only: previous four Active decisions unchanged.
    # At its only executed invocation it emits the exact same order, so a fresh
    # native job would replay V42 rather than test a new executable difference.
    parity=dict(scope='Replace only final renewed continuation price/size selector; keep first four Active and all gates/lifecycle unchanged',
        active_service_key='UP_701',source_index=1044,price=.07,quantity=87.79,
        same_action=True,following_policy_state_unchanged=True,
        conclusion='NO_DISTINCT_NATIVE_CANDIDATE_FOR_CURRENT_ONLY_ENTRY')
    result=dict(status='LOCAL_EXAM_COMPLETE_NATIVE_NOT_RUN',component=component,
        parent_job=previous.job('current')['job_id'],saved_state_rows=415,
        examined_work_state_scenarios=len(points),solver_evaluations=len(points)*len(candidate.MODES),
        counts=dict(counts),actual_continuation=actual_continuation,pending_continuation=pending_point,
        candidate_prefix_parity=parity,
        first_renewed_service=[p for p in points if p['index']==1040],
        native_jobs=0,model_fits=0,parameter_search=0,worker_connections=0,plots=0,
        frozen_policy_changed=False,
        decision='Demand-sized visible-depth pricing implemented and locally verified; exact current continuation is unchanged, while pending-burden deeper proposal conflicts. No duplicate native.',
        next_scope='Finite unfinished work service after partial fills and canonical terminal, with depth-based pricing. Conflict coordination must wait for receipts and recompute demand; visible-demand pricing alone is not a latency buffer.',
        limitations=['All later saved states are baseline-conditional snapshots, not trajectories produced by this candidate.',
            'Pending burden is a scenario, not completed expenditure; cancelling it changes future demand.',
            'Local coordination is only a proposal, not a native cancellation/lifecycle implementation.',
            'Displayed depth can disappear before exchange arrival; no learned execution probability or invented depth haircut.',
            'Expected cost differs from worst price reservation; native zero fee remains a local assumption.'],
        source_sha256={str(p.relative_to(previous.ROOT)):previous.sha(p) for p in
            (package/'manifest.json',trace_path,public_path,previous.ROOT/'tools/btc5m_demand_slippage_v1.py')})
    dump('SNAPSHOTS',dict(status='DIAGNOSTIC_NOT_ROLLOUT',rows=points))
    dump('RESULT',result)
    print(json.dumps(dict(status=result['status'],component=component,
        evaluations=result['solver_evaluations'],current=actual_continuation['modes'],
        pending=pending_point['modes'],native_jobs=0),ensure_ascii=False))


if __name__=='__main__':
    main()
