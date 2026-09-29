"""Full local reconstruction of the collected V43 native decisions and receipts."""
from collections import Counter
from copy import deepcopy
import inspect
import json
import sys
from types import SimpleNamespace

import btc5m_demand_price_coordination_experiment_v1 as d
from verify_btc5m_transfer_components_v1 import same


def main():
    w=d.worker();job=d.job()
    audit=d.read(w.artifact(job,'AUDIT'));assert audit['execution_status']=='PASS'
    folder=d.R/'lan_worker_returns'/d.JOB
    native=d.read(folder/'result.json');tr=d.read(folder/'clock_trace.json.gz')
    bt=d.read(d.R/'lan_worker_returns'/d.prior.job('pending')['job_id']/'clock_trace.json.gz')
    # Unchanged older coordination, commitment, and Passive maintenance rules.
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.STEM=d.STEM;cv.PACKAGE=d.PACKAGE;cv.jobs=lambda:[None,job];cv.dump=d.dump
    code=d.once(inspect.getsource(cv.main),'episode,confirmed,crossing_owners)',"episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    ns=dict(cv.__dict__);exec(compile(code,'V43_INHERITED_CONTINUATION_AUDIT','exec'),ns);ns['main']()
    sys.path.insert(0,str(d.PACKAGE))
    from roles_runtime import roles
    import coordination as coord
    import pending_service as service
    import depth_slippage as depth
    import growth_hold as hold
    from renewed_work import WorkMemory
    from hft244_pair_route_legality_v1 import crossing_owners
    roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    owners={o['key']:o for o in audit['receipt_accounting']['orders']}
    baseaudit=d.read(d.prior.worker('pending').artifact(d.prior.job('pending'),'AUDIT'))
    # prior.worker mutates shared dispatcher globals; restore before saving.
    w=d.worker()
    baseowners={o['key']:o for o in baseaudit['receipt_accounting']['orders']}
    first_four=tr['opportunity_submissions']+tr['coordination_submissions'][:3]
    for o in first_four:same(owners[o['key']],baseowners[o['key']])
    same(tr['opportunity_submissions'],bt['opportunity_submissions'])
    same(tr['coordination_submissions'][:3],bt['coordination_submissions'][:3])
    same(tr['coordination_rows'],bt['coordination_rows'])
    same(tr['coordination_episode'],bt['coordination_episode'])
    plans={r['index']:p for r,p in zip(tr['direction_rows'],tr['plans'])}
    public=d.read(d.PACKAGE/'inputs/public_1977248.json.gz')
    books={r['index']:b for r,b in zip(tr['direction_rows'],public['books'])}
    selected=[i for i in tr['intent'] if i['t']>tr['coordination_submissions'][1]['t']]
    assert len(selected)==len(tr['renewal_rows'])
    memory=WorkMemory();issued=[];adaptive_rows=[];unreconstructed_deep_totals=[]
    for intent,row in zip(selected,tr['renewal_rows']):
        index=intent['index'];t=row['t'];assert intent['t']==t
        state=row['state'];book=books[index]
        for old,status in zip(tr['coordination_submissions'][:2],row['old_service_states']):
            assert (status=='TERMINAL')==(t>=owners[old['key']]['terminal_observed_t'])
        work=memory.advance(state,t,tr['coordination_episode'],row['old_service_states'],coord.detect)
        same(work,row['status']);same(book['best_ask'],row['active_ask'])
        same(book['asks'][0][1] if book['asks'] else 0.,row['visible_depth'])
        continuation=bool(issued);same(continuation,row['continuation'])
        prior=None
        if issued:
            prior=SimpleNamespace(state=row['previous_service_state'])
            assert (prior.state=='TERMINAL')==(t>=owners[issued[0]['key']]['terminal_observed_t'])
        ready=service.ready(work['work'],issued,prior) if continuation else True
        ops=deepcopy(row['original_operations'])
        if work['work'] is not None and len(issued)<2 and ready:
            if continuation:
                legacy=service.decide(coord.decide,state,ops,row['active_ask'],row['visible_depth'],work['work'],crossing_owners)
                ops,decision,diag=depth.replan(state,ops,'UP',work['work']['anchor_floor'],book['asks'],row['cancellable'],legacy)
                observed=deepcopy(row['adaptive']);expected=deepcopy(diag)
                # Native book retains deeper levels than the saved top-five
                # public snapshot. Reconstruct every consumed level and decision;
                # do not invent an exact total for unused deeper liquidity.
                for a,b in zip(expected['steps'],observed['steps']):
                    local_total=a['quote'].pop('inspected_depth',None)
                    native_total=b['quote'].pop('inspected_depth',None)
                    if local_total!=native_total:
                        assert native_total>=local_total
                        assert a['quote']['deepest_used']<=book['asks'][-1][0]
                        assert a['quote']['remaining_need_on_displayed_fill']<.01*(1-a['quote']['deepest_used'])+1e-8 or a['quote']['quantity']>=a['quote']['capacity']-.01000001
                        unreconstructed_deep_totals.append(dict(index=index,reported_native_total=native_total,saved_top_five_total=local_total,exact_unused_deep_total='UNKNOWN'))
                same(expected,observed)
                adaptive_rows.append(dict(index=index,t=t,legacy=legacy,decision=decision,diagnostic=diag))
                for op in ops:
                    if op.get('reason')=='FINITE_REPAIR_PRICE_CONFLICT':
                        assert row['cancellable'][op['key']]
                        own=next(o for o in state['owners'] if o['key']==op['key'])
                        assert own['state'] not in ('TERMINAL','CANCEL_PENDING','UNKNOWN')
            else:
                decision=coord.decide(state,ops,row['active_ask'],row['visible_depth'],work['work'],True,crossing_owners)
            assert row['decision']['reason']!='RESOURCE_OWNER_LIMIT'
            same(decision,row['decision']);same(decision['eligible'],row['submitted'])
            if decision['eligible']:
                role='ACTIVE_RENEWED_FINITE_CONTINUATION' if continuation else 'ACTIVE_RENEWED_FINITE_REPAIR'
                op=next(o for o in plans[index]['operations'] if o.get('role')==role)
                same(op['qty'],decision['quantity']);same(op['price'],decision.get('execution_limit',row['active_ask']))
                assert op['price']>=row['active_ask']-1e-8
                ops.append(op);issued.append(dict(work_id=work['work']['id'],anchor=work['work']['anchor_floor'],t=t,**op))
        else:
            assert row['decision'] is None and not row['submitted']
        same(ops,plans[index]['operations'])
    same(issued,tr['renewal_submissions']);same(memory.events,tr['renewal_events']);same(memory.work,tr['renewal_work'])
    assert 1<=len(issued)<=2
    # Reconstruct growth release from actual service receipts and strong pending.
    born={o['key']:index for index,p in plans.items() for o in p['operations'] if o['kind']=='NEW'}
    armed=tr['growth_hold_arm'];released=False;events=[dict(kind='ARM',**armed)]
    for row,g,intent,dm in zip(tr['growth_hold_rows'],tr['addition_growth_rows'],tr['intent'],tr['demand_rows']):
        assert row['t']==intent['t']==g['t'] and row['index']==intent['index']
        ar=armed if row['index']>armed['index'] else None;same(ar,row['arm'])
        same(row['confirmed_weak_payoff'],g['inv']['UP']-g['cost'])
        same(row['strong_pending_cash'],dm['state']['pending_cash']['DOWN'])
        past=[s for s in issued if born[s['key']]<row['index']]
        same([r['key'] for r in row['service_receipts']],[s['key'] for s in past])
        for receipt in row['service_receipts']:
            owner=owners[receipt['key']]
            assert (receipt['state']=='TERMINAL')==(row['t']>=owner['terminal_observed_t'])
            q=sum(f['fill_increment'] for e in native['atomic_responsibility_events'] if e['t']<=row['t'] for f in e['fill_rows'] if f['key']==receipt['key'])
            same(q,receipt['filled'])
        aggregate=service.aggregate_receipts(row['service_receipts']);same(aggregate,row['receipt'])
        expected=hold.decide(g,ar,aggregate,row['confirmed_weak_payoff'],released,row['strong_pending_cash'])
        same(expected,row['decision']);same(expected['effective_desired'],row['effective_desired'])
        if expected['released'] and not released:
            events.append(dict(kind='RELEASE',t=row['t'],index=row['index'],reason=expected['reason']))
        released=expected['released']
    same(events,tr['growth_hold_events'])
    first=next(i for i,(a,b) in enumerate(zip(tr['plans'],bt['plans'])) if a!=b)
    cut=tr['plans'][first]['t'];assert first==1044
    same(tr['plans'][:first],bt['plans'][:first])
    same([s for s in tr['states'] if s['t']<=cut],[s for s in bt['states'] if s['t']<=cut])
    service_owners=[owners[s['key']] for s in issued]
    higher=[]
    for index,p in plans.items():
        for op in p['operations']:
            if op.get('role')=='ACTIVE_RENEWED_FINITE_CONTINUATION' and op['price']>books[index]['best_ask']+1e-8:
                higher.append(dict(index=index,ask=books[index]['best_ask'],order=op))
    cancels=[dict(t=p['t'],**op) for p in tr['plans'] for op in p['operations'] if op.get('reason')=='FINITE_REPAIR_PRICE_CONFLICT']
    result=dict(status='COMPLETE',execution='PASS',new_rule_audit='PASS',native_jobs=1,
        native_seconds=audit['native_elapsed_seconds'],frames=audit['frames'],new_orders=audit['submits'],raw_receipts=audit['raw_receipts'],active_orders=audit['active_submits'],
        source_prefix_plans_exact=first,first_four_receipts_exact=True,
        first_difference=dict(index=first,seconds=(cut-1788634800000)/1000,baseline=bt['plans'][first],candidate=tr['plans'][first]),
        renewal_rows=len(tr['renewal_rows']),growth_hold_rows=len(tr['growth_hold_rows']),adaptive_rows=adaptive_rows,
        adaptive_statuses=dict(Counter(r['diagnostic']['status'] for r in adaptive_rows)),
        extra_cancels=cancels,extra_cancel_owners=[owners[o['key']] for o in cancels],
        service_submissions=issued,service_receipts=service_owners,higher_than_ask_orders=higher,
        terminal=audit['terminal'],baseline_terminal=baseaudit['terminal'],
        terminal_delta={k:audit['terminal'][k]-baseaudit['terminal'][k] for k in ('up','down','cost','inventory_up','inventory_down')},
        trajectory=audit['trajectory'],held_goal_gap=max(0.,armed['goal']-audit['terminal']['up']),
        growth_events=events,work_end=tr['renewal_work'],
        all_owners_terminal=audit['all_owners_terminal'],all_pending_zero=audit['all_pending_zero'],
        missing_terminal_clocks=audit['terminal_timing']['missing_owner_observation_clocks'],
        model_fits=0,parameter_search=0,local_native_jobs=0,plots=0,
        unused_deep_depth_totals=unreconstructed_deep_totals,
        selected_depth_and_decisions_reconstructed=True,
        interpretation='Execution of combined demand-price/conflict coordination; compare service realization and actual prices before attributing any gain to slippage. Exact unused deep-book total is not reconstructed; all consumed prices, quantities, costs and executable decisions are verified against saved public depth.',
        source_manifest_sha256=d.sha(d.PACKAGE/'manifest.json'),result_sha256=d.sha(folder/'result.json'),trace_sha256=d.sha(folder/'clock_trace.json.gz'))
    d.dump('RESULT',result);d.dump('NEW_AUDIT',dict(status='PASS',renewal_rows=len(tr['renewal_rows']),growth_hold_rows=len(tr['growth_hold_rows']),adaptive_decisions=len(adaptive_rows),first_four_receipts_exact=True,source_prefix_plans_exact=first))
    d.dump('PROGRESS',dict(status='COMPLETE',submissions=1,native_completed=1,pending_jobs=[]))
    print(json.dumps({k:result[k] for k in ('status','native_seconds','terminal','terminal_delta','adaptive_statuses','service_submissions','service_receipts','higher_than_ask_orders')},ensure_ascii=False))


if __name__=='__main__':
    main()
