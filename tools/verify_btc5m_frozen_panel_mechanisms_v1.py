"""Side-aware replay of recorded V43/V44 decisions on the frozen V45 cohort."""
from copy import deepcopy
import inspect
import sys
from types import SimpleNamespace

import btc5m_frozen_new_market_panel_v1 as d
from verify_btc5m_transfer_components_v1 import same


def audit(index):
    job=d.jobs()[index];package=d.PACKAGES[job['version']];w=d.worker(job['version'],job)
    basic=d.read(w.artifact(job,'AUDIT'));assert basic['execution_status']=='PASS'
    folder=d.R/'lan_worker_returns'/job['job_id'];n=d.read(folder/'result.json');tr=d.read(folder/'clock_trace.json.gz')
    sys.path.insert(0,str(package))
    from roles_runtime import roles
    import coordination as coord
    import pending_service as service
    import depth_slippage as depth
    import growth_hold as hold
    from renewed_work import WorkMemory
    from hft244_pair_route_legality_v1 import crossing_owners
    direction={r['t']:r['side'] for r in tr['direction_rows']}
    def set_role(t):roles.configure('KNOWN_FINAL_DIRECTION',direction[t] or 'UP')
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.STEM=d.STEM;cv.PACKAGE=package;cv.jobs=lambda:[None,job];cv.dump=lambda tag,x:w.save(job,tag,x)
    code=d.once(inspect.getsource(cv.main),'episode,confirmed,crossing_owners)',"episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    for key in ('commitment_repair_rows','coordination_rows','commitment_maintenance_rows','maintenance_scope_rows'):
        marker=f"        for row in tr['{key}']:"
        code=d.once(code,marker,marker+"\n            set_role(row['t'])")
    ns=dict(cv.__dict__,set_role=set_role);exec(compile(code,'V45_SIDE_AWARE_INHERITED_AUDIT','exec'),ns);ns['main']()
    owners={o['key']:o for o in basic['receipt_accounting']['orders']}
    public=d.read(package/f"inputs/public_{job['market']}.json.gz")
    start=public['market']['window_start_ms'];end=public['market']['window_end_ms']
    plans={r['index']:p for r,p in zip(tr['direction_rows'],tr['plans'])}
    books={r['index']:b for r,b in zip(tr['direction_rows'],public['books'])}
    if job['version']=='V44':
        from tail_new import blocked
        for row in tr['tail_new_rows']:
            assert row['t']==plans[row['index']]['t'] and row['start']==start and row['end']==end
            assert row['strong']==direction[row['t']]
            assert row['blocked']==blocked(row['t'],start,end,row['side'],row['strong'],row['route'])
            assert row['qty']*row['price']>=1-1e-8
        for p in tr['plans']:
            if 3*(p['t']-start)>=2*(end-start):
                assert all(o.get('side')!=direction[p['t']] for o in p['operations'] if o['kind']=='NEW')
    else:assert 'tail_new_rows' not in tr
    unknown_clock_checks=[]
    def terminal(key,t,status):
        clock=owners[key]['terminal_observed_t']
        if clock is None:
            unknown_clock_checks.append(dict(key=key,t=t,status=status))
        else:assert (status=='TERMINAL')==(t>=clock)
    old=tr['coordination_submissions'][:2]
    selected=[i for i in tr['intent'] if i['t']>old[1]['t']] if len(old)==2 else []
    assert len(selected)==len(tr['renewal_rows'])
    memory=WorkMemory();issued=[];adaptive_count=0
    for intent,row in zip(selected,tr['renewal_rows']):
        index=intent['index'];t=row['t'];assert intent['t']==t;set_role(t)
        state=row['state'];book=books[index];weak=roles.weak
        asks=book['asks'] if weak=='UP' else sorted([[round(1-p,10),q] for p,q in book['bids']])
        ask=book['best_ask'] if weak=='UP' else round(1-book['best_bid'],10) if book['best_bid'] is not None else None
        same(state['inv'],intent['inv']);same(state['cost'],intent['cost']);same(ask,row['active_ask'])
        same(asks[0][1] if asks else 0.,row['visible_depth'])
        for sub,status in zip(old,row['old_service_states']):terminal(sub['key'],t,status)
        status=memory.advance(state,t,tr['coordination_episode'],row['old_service_states'],coord.detect);same(status,row['status'])
        continuation=bool(issued);same(continuation,row['continuation']);prior=None
        if issued:
            prior=SimpleNamespace(state=row['previous_service_state']);terminal(issued[0]['key'],t,prior.state)
        ready=service.ready(status['work'],issued,prior) if continuation else True
        ops=deepcopy(row['original_operations'])
        if status['work'] is not None and len(issued)<2 and ready:
            if continuation:
                legacy=service.decide(coord.decide,state,ops,ask,row['visible_depth'],status['work'],crossing_owners)
                actual_asks=row['actor_asks'];same(actual_asks[:len(asks)],asks)
                ops,decision,diag=depth.replan(state,ops,weak,status['work']['anchor_floor'],actual_asks,row['cancellable'],legacy)
                same(diag,row['adaptive']);adaptive_count+=1
                for op in ops:
                    if op.get('reason')=='FINITE_REPAIR_PRICE_CONFLICT':
                        assert row['cancellable'][op['key']]
                        owner=next(o for o in state['owners'] if o['key']==op['key'])
                        assert owner['state'] not in ('TERMINAL','CANCEL_PENDING','UNKNOWN')
            else:decision=coord.decide(state,ops,ask,row['visible_depth'],status['work'],True,crossing_owners)
            assert row['decision']['reason']!='RESOURCE_OWNER_LIMIT'
            same(decision,row['decision']);same(decision['eligible'],row['submitted'])
            if decision['eligible']:
                role='ACTIVE_RENEWED_FINITE_CONTINUATION' if continuation else 'ACTIVE_RENEWED_FINITE_REPAIR'
                op=next(o for o in plans[index]['operations'] if o.get('role')==role)
                assert op['side']==weak and op['parent_id']==roles.pid(weak)
                same(op['qty'],decision['quantity']);same(op['price'],decision.get('execution_limit',ask))
                ops.append(op);issued.append(dict(work_id=status['work']['id'],anchor=status['work']['anchor_floor'],t=t,**op))
        else:assert row['decision'] is None and not row['submitted']
        same(ops,plans[index]['operations'])
    same(issued,tr['renewal_submissions']);same(memory.events,tr['renewal_events']);same(memory.work,tr['renewal_work'])
    assert len(issued)<=2
    born={o['key']:index for index,p in plans.items() for o in p['operations'] if o['kind']=='NEW'}
    armed=tr['growth_hold_arm'];released=False;events=[dict(kind='ARM',**armed)] if armed else []
    for row,g,intent,dm in zip(tr['growth_hold_rows'],tr['addition_growth_rows'],tr['intent'],tr['demand_rows']):
        assert row['t']==intent['t']==g['t'] and row['index']==intent['index'];set_role(row['t'])
        ar=armed if armed and row['index']>armed['index'] else None;same(ar,row['arm'])
        same(row['confirmed_weak_payoff'],g['inv'][roles.weak]-g['cost'])
        same(row['strong_pending_cash'],dm['state']['pending_cash'][roles.strong])
        past=[s for s in issued if born[s['key']]<row['index']]
        same([r['key'] for r in row['service_receipts']],[s['key'] for s in past])
        for receipt in row['service_receipts']:
            terminal(receipt['key'],row['t'],receipt['state'])
            q=sum(f['fill_increment'] for e in n['atomic_responsibility_events'] if e['t']<=row['t'] for f in e['fill_rows'] if f['key']==receipt['key'])
            same(q,receipt['filled'])
        aggregate=service.aggregate_receipts(row['service_receipts']);same(aggregate,row['receipt'])
        expected=hold.decide(g,ar,aggregate,row['confirmed_weak_payoff'],released,row['strong_pending_cash'])
        same(expected,row['decision']);same(expected['effective_desired'],row['effective_desired'])
        if expected['released'] and not released:events.append(dict(kind='RELEASE',t=row['t'],index=row['index'],reason=expected['reason']))
        released=expected['released']
    same(events,tr['growth_hold_events'])
    result=dict(status='PASS',job_id=job['job_id'],renewal_rows=len(tr['renewal_rows']),renewed_services=len(issued),growth_hold_rows=len(tr['growth_hold_rows']),
        adaptive_decisions=adaptive_count,tail_screening_rows=len(tr.get('tail_new_rows',[])),
        tail_vetoes=sum(r['blocked'] for r in tr.get('tail_new_rows',[])),unknown_exact_terminal_clock_checks=unknown_clock_checks,
        direction=n['clock_smoke']['selected_direction'],source_model=d.sha(package/'manifest.json'),local_native_jobs=0)
    w.save(job,'MECHANISM_AUDIT',result)
    return result
