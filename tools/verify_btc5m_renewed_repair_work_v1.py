"""Full collected renewal-memory audit and economic report; no HFT or fitting."""
from collections import Counter
from copy import deepcopy
import json

from btc5m_renewed_repair_experiment_v1 import *
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs
from report_btc5m_success_case_benchmark_v1 import summary
from audit_btc5m_post_exposure_response_v1 import reconstruct


def main():
    w=worker();j=jobs()[0];folder=R/'lan_worker_returns'/j['job_id']
    au=read(w.artifact(j,'AUDIT'));assert au['execution_status']=='PASS'
    assert read(w.artifact(j,'CONTINUATION_AUDIT'))['status']=='PASS'
    assert read(w.artifact(j,'POSTCHECK'))['status']=='PASS'
    n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz')
    bn=read(R/'lan_worker_returns'/BASEJOB/'result.json');bt=read(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz')
    roles,coord,work=modules();roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    start=read(PACKAGE/'inputs/public_1977248.json.gz')['market']['window_start_ms']
    inp=read(PACKAGE/'inputs/public_1977248.json.gz')
    # Received clocks can repeat. Preserve source-frame order, never join by t alone.
    books={d['index']:b for d,b in zip(tr['direction_rows'],inp['books'])}
    demand=[(i,r) for i,r in zip(tr['intent'],tr['demand_rows']) if r['t']>tr['coordination_submissions'][1]['t']]
    old_services=tr['coordination_submissions'][:2]
    same(old_services,bt['coordination_submissions']);same(tr['opportunity_submissions'],bt['opportunity_submissions'])
    same(tr['coordination_rows'],bt['coordination_rows']);same(tr['coordination_episode'],bt['coordination_episode'])
    owners={o['key']:o for o in au['receipt_accounting']['orders']}
    plans={d['index']:p for d,p in zip(tr['direction_rows'],tr['plans'])}
    memory=work.WorkMemory();submitted=[];reasons=Counter();terminal_checks=0
    assert [r['t'] for r in tr['renewal_rows']]==[r['t'] for r in tr['demand_rows'] if r['t']>old_services[-1]['t']]
    for row,(intent,demand_row) in zip(tr['renewal_rows'],demand):
        t=row['t'];index=intent['index'];same(row['state'],demand_row['state'])
        for op,status in zip(old_services,row['old_service_states']):
            o=owners[op['key']];terminal=o['terminal_observed_t']
            assert terminal is not None
            assert (status=='TERMINAL')==(t>=terminal);terminal_checks+=1
        status=memory.advance(row['state'],t,tr['coordination_episode'],row['old_service_states'],coord.detect)
        same(status,row['status'])
        book=books[index];same(row['active_ask'],book['best_ask'])
        same(row['visible_depth'],book['asks'][0][1] if book['asks'] else 0.)
        expected_ops=deepcopy(row['original_operations'])
        if status['work'] is not None and not submitted:
            decision=coord.decide(row['state'],expected_ops,row['active_ask'],row['visible_depth'],status['work'],True,crossing_owners,continuation=False)
            # No resource rejection occurred in this bounded job; don't infer a cap.
            assert row['decision']['reason']!='RESOURCE_OWNER_LIMIT'
            same(decision,row['decision']);reasons[decision['reason']]+=1
            assert row['submitted']==decision['eligible']
            if decision['eligible']:
                op=next(o for o in plans[index]['operations'] if o.get('role')=='ACTIVE_RENEWED_FINITE_REPAIR')
                same(op['price'],row['active_ask']);same(op['qty'],decision['quantity'])
                assert op['side']==roles.weak and op['parent_id']==roles.pid(roles.weak)
                expected_ops.append(op)
                submitted.append(dict(work_id=status['work']['id'],anchor=status['work']['anchor_floor'],t=t,**op))
        else:
            assert row['decision'] is None and not row['submitted']
        same(expected_ops,plans[index]['operations'])
    same(memory.events,tr['renewal_events']);same(memory.work,tr['renewal_work']);same(submitted,tr['renewal_submissions'])
    assert len(submitted)==1 and n['active_native_submits']==4
    same(tr['coordination_submissions'][2],{k:v for k,v in submitted[0].items() if k not in ('work_id','anchor')})
    first=submitted[0];cut=first['t']
    predicted=read(R/(STEM+'_PREFIX_DIAGNOSTIC.json'))['first_difference']
    assert cut==predicted['t'];same(first['qty'],predicted['decision']['quantity']);same(first['price'],predicted['decision']['active_ask'])
    before=[p for p in tr['plans'] if p['t']<cut];same(before,[p for p in bt['plans'] if p['t']<cut])
    same([s for s in tr['states'] if s['t']<=cut],[s for s in bt['states'] if s['t']<=cut])
    first_plan=next(p for p in tr['plans'] if any(o.get('key')==first['key'] and o['kind']=='NEW' for o in p['operations']))
    same([o for o in first_plan['operations'] if o.get('key')!=first['key']],next(p['operations'] for p in bt['plans'] if p['t']==cut))
    for op in tr['opportunity_submissions']+old_services:
        bo=next(o for o in read(BASEAUDIT)['receipt_accounting']['orders'] if o['key']==op['key'])
        same(owners[op['key']],bo)
    local=dict(status='PASS',renewal_rows=len(tr['renewal_rows']),terminal_clock_checks=terminal_checks,
        pure_memory_reconstructed=True,pure_decisions_reconstructed=True,original_services_and_receipts_exact=True,
        original_prefix_plan_count=len(before),original_state_prefix_exact=True,first_difference_matches_saved_prefix=True,
        duplicate_source_clocks=len(inp['books'])-len({b['received_ms'] for b in inp['books']}),
        duplicate_clocks_preserved_by_source_index=True,
        additional_active_count=len(submitted),reason_counts=dict(reasons),work_events=tr['renewal_events'],
        additional_service=first,additional_receipt=owners[first['key']],work_end=memory.work)
    dump('RENEWAL_AUDIT',local)
    summaries={};all_legs={}
    for name,result,trace in [('baseline',bn,bt),('candidate',n,tr)]:
        legs=canonical_legs(result,trace);all_legs[name]=legs
        summaries[name]=summary(reconstruct([dict(x,t=x['t']-start) for x in legs]),'DOWN')
    target=read(R/'BTC5M_SUCCESS_CASE_BENCHMARK_V1_20260913_TARGET_REFERENCE.json')['targets']['1977248']
    sums=summaries;after={}
    for name,legs in all_legs.items():
        tail=[x for x in legs if x['t']>cut]
        after[name]={side:dict(qty=sum(x['qty'] for x in tail if x['side']==side),cash=sum(x['cash'] for x in tail if x['side']==side)) for side in ('UP','DOWN')}
    before_state=next(r['state'] for r in tr['renewal_rows'] if r['t']==cut)
    for name in after:
        flow=after[name];change={s:flow[s]['qty']-sum(x['cash'] for x in flow.values()) for s in flow}
        for side in change:same(before_state['payoff'][side]+change[side],sums[name]['terminal'][side.lower()])
        after[name].update(delta_payoff=change)
    rec=owners[first['key']]
    service_effect=dict(filled=rec['filled'],requested=rec['qty'],cash=rec['payment'],
        up_lift=rec['filled']-rec['payment'],down_cost=rec['payment'],terminal_state=rec['state'],
        first_canonical_seconds=(rec['first_canonical_t']-start)/1000 if rec['first_canonical_t'] is not None else None,
        terminal_seconds=(rec['terminal_observed_t']-start)/1000 if rec['terminal_observed_t'] is not None else None)
    growth_samples=[]
    for a,b in zip(tr['addition_growth_rows'],bt['addition_growth_rows']):
        assert a['t']==b['t'];same(a['original_desired'],b['original_desired'])
        if a['t']>=cut and abs(a['weight']-b['weight'])>1e-8 and (not growth_samples or a['t']-growth_samples[-1]['t']>=1000):
            growth_samples.append(dict(t=a['t'],seconds=(a['t']-start)/1000,
                baseline_weight=b['weight'],candidate_weight=a['weight'],
                baseline_strong_desired=b['effective_desired']['DOWN'],candidate_strong_desired=a['effective_desired']['DOWN'],
                raw_strong_desired=a['original_desired']['DOWN']))
    extra_strong=after['candidate']['DOWN']['cash']-after['baseline']['DOWN']['cash']
    feedback=dict(status='PASS',all_original_desired_rows_equal=True,growth_samples=growth_samples,
        direct_repair_lift=service_effect['up_lift'],additional_strong_cash=extra_strong,
        downstream_cost_over_direct_repair_lift=extra_strong/service_effect['up_lift'],
        net_weak_change=service_effect['up_lift']-extra_strong,
        direct_service_and_all_downstream_differences_separate=True,
        next_scope='Isolate repair-induced release of strong demand while a finite work remains unresolved, using current OWN state and pending receipts. Preserve concurrent base acquisition and no repair cash budget. Do not increase Active count again before checking this feedback.',
        limitation='One intervention identifies the whole trajectory response. The observed weight increase is a concrete mechanism in that response, but its sole contribution has not been isolated by a held-weight native ablation.')
    same(feedback['net_weak_change'],sums['candidate']['terminal']['up']-sums['baseline']['terminal']['up'])
    dump('ADDITION_FEEDBACK',feedback)
    result=dict(status='COMPLETE',execution='PASS',model_fits=0,parameter_search=0,local_native_jobs=0,
        native_jobs=1,native_seconds=au['native_elapsed_seconds'],job_id=j['job_id'],figures=0,
        summaries=sums,target_reference=target,first_new_service=first,service_effect=service_effect,
        work_events=memory.events,work_end=memory.work,after_first_new_service=after,
        delta={k:sums['candidate']['terminal'][k]-sums['baseline']['terminal'][k] for k in ('up','down','cost')},
        negative_floor_area_change=sums['candidate']['negative_floor_area']-sums['baseline']['negative_floor_area'],
        remaining_goal_gap=max(0.,memory.work['anchor_floor']-sums['candidate']['terminal']['up']) if memory.work else 0.,
        manifest_sha256=sha(PACKAGE/'manifest.json'),baseline_manifest_sha256=sha(BASE/'manifest.json'),
        result_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'),
        all_owners_terminal=au['all_owners_terminal'],all_pending_zero=au['all_pending_zero'],
        missing_terminal_clocks=au['terminal_timing']['missing_owner_observation_clocks'],
        decision='DO_NOT_PROMOTE_RENEWAL_ONLY_CANDIDATE',learning_status='MONETARY_WORK_MEMORY_FUNCTIONAL_BUT_REPAIR_ADDITION_COORDINATION_NOT_LEARNED',
        feedback=feedback,
        interpretation='A new finite work and one service are a bounded intervention, not learned Target policy. Full order fill is separate from monetary goal completion. Additional shares can alter later desired/pending behavior; report total trajectory delta separately from direct service contribution.',
        selection='Previously inspected, outcome-selected development case. Direction bit is final observed net side, not winner. Target weak repair is Maker; this Active workaround does not reproduce route allocation.')
    dump('RESULT',result)
    dump('PROGRESS',dict(status='COMPLETE',native_submissions=1,native_jobs_completed=1,model_fits=0,pending_jobs=[]))
    print(json.dumps(dict(status='PASS',renewal_rows=len(tr['renewal_rows']),events=[{k:v for k,v in e.items() if k not in ('previous','current')} for e in memory.events],
        service=service_effect,terminal={k:v['terminal'] for k,v in sums.items()},delta=result['delta'],
        remaining_goal_gap=result['remaining_goal_gap'],after_first_new_service=after),allow_nan=False))


if __name__=='__main__':main()
