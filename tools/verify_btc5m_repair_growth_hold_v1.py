"""Reconstruct the new hold and reuse complete V40 renewal audits, offline only."""
from collections import Counter
from copy import deepcopy
import inspect
import json

from btc5m_repair_growth_hold_experiment_v1 import *
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs
from btc5m_partial_reexposure_experiment_v1 import keyed_legs
from report_btc5m_success_case_benchmark_v1 import summary
from audit_btc5m_post_exposure_response_v1 import reconstruct
from hft244_pair_route_legality_v1 import crossing_owners


def remaining_work(tr,owner):
    roles,_,_=modules();roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    coord=load('v41_remaining_coord',PACKAGE/'coordination.py')
    reasons=Counter();first=None;eligible=0
    for row in tr['renewal_rows']:
        if row['t']<=owner['terminal_observed_t'] or row['status']['work'] is None:continue
        d=coord.decide(row['state'],row['original_operations'],row['active_ask'],row['visible_depth'],row['status']['work'],True,crossing_owners,continuation=False)
        reasons[d['reason']]+=1;eligible+=d['eligible']
        if d['eligible'] and first is None:
            s=row['state'];q=d['quantity'];price=d['active_ask'];cash=q*price
            first=dict(t=row['t'],seconds=(row['t']-1788634800000)/1000,state=s,decision=d,
                work_id=row['status']['work']['id'],
                conditional_if_proposed_active_fills={k:s['payoff'][k]+(q if k=='UP' else 0.)-cash for k in ('UP','DOWN')},
                conditional_if_proposed_active_and_all_pending_fill={k:s['payoff'][k]+(q if k=='UP' else 0.)-cash+s['pending_qty'][k]-sum(s['pending_cash'].values()) for k in ('UP','DOWN')},
                hypothetical_rounding_goal_gap=d['anchor_floor']-(s['payoff']['UP']+q-cash))
    out=dict(status='PASS',scope='Recorded V41 route-mask diagnosis only. No continuation native, no hypothetical fills counted as executed or summed over rows.',
        eligible_saved_rows=eligible,reason_counts=dict(reasons),first_candidate=first,
        note='The inherited Active need credits weak pending repair but does not add strong pending spending to that service goal. Commitment repair separately considers strong pending, but Passive15 can be price-ineligible. Hypothetical sub-cent rounding gap is not the cause of the actual 191.47 remaining goal gap.')
    dump('REMAINING_WORK_DIAGNOSTIC',out)
    return out


def main():
    w=worker();j=jobs()[0];folder=R/'lan_worker_returns'/j['job_id']
    au=read(w.artifact(j,'AUDIT'));assert au['execution_status']=='PASS'
    assert read(w.artifact(j,'CONTINUATION_AUDIT'))['status']=='PASS' and read(w.artifact(j,'POSTCHECK'))['status']=='PASS'
    n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz')
    bn=read(R/'lan_worker_returns'/BASEJOB/'result.json');bt=read(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz')
    roles,growth,hold=modules();roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    sub=tr['renewal_submissions'][0];arm=tr['growth_hold_arm']
    owner=next(o for o in au['receipt_accounting']['orders'] if o['key']==sub['key'])
    assert len(tr['renewal_submissions'])==1 and arm['key']==sub['key'] and arm['goal']==sub['anchor']
    at=next(g for i,g in zip(tr['intent'],tr['addition_growth_rows']) if i['index']==arm['index'])
    assert at['t']==arm['t']==sub['t'];same(arm['ceiling'],at['weight'])
    same(arm,dict(key=sub['key'],work_id=sub['work_id'],goal=sub['anchor'],ceiling=at['weight'],t=sub['t'],index=arm['index']))
    counts=Counter();released=False;events=[dict(kind='ARM',**arm)];first_desired=None
    assert len(tr['growth_hold_rows'])==len(tr['addition_growth_rows'])==len(tr['intent'])
    for row,g,intent in zip(tr['growth_hold_rows'],tr['addition_growth_rows'],tr['intent']):
        assert row['t']==g['t']==intent['t'] and row['index']==intent['index']
        expected_arm=arm if row['index']>arm['index'] else None
        same(row['arm'],expected_arm);same(row['input_desired'],g['effective_desired'])
        same(row['confirmed_weak_payoff'],g['inv']['UP']-g['cost'])
        if expected_arm:
            # This service's canonical fill clocks are distinct from duplicate source timestamps.
            qty=sum(f['fill_increment'] for e in n['atomic_responsibility_events'] if e['t']<=row['t'] for f in e['fill_rows'] if f['key']==sub['key'])
            assert row['receipt'] is not None
            same(row['receipt']['filled'],qty)
            assert (row['receipt']['state']=='TERMINAL')==(row['t']>=owner['terminal_observed_t'])
        else:assert row['receipt'] is None
        d=hold.decide(g,expected_arm,row['receipt'],row['confirmed_weak_payoff'],released)
        same(d,row['decision']);same(d['effective_desired'],row['effective_desired'])
        assert row['effective_desired']['UP']==row['input_desired']['UP']
        if d['released'] and not released:events.append(dict(kind='RELEASE',t=row['t'],index=row['index'],reason=d['reason']))
        released=d['released'];counts[d['reason']]+=1
        if first_desired is None and row['input_desired']!=row['effective_desired']:first_desired=row
    same(events,tr['growth_hold_events'])
    predicted=read(R/(STEM+'_HOLD_PREFIX_DIAGNOSTIC.json'))['first_changed_desired']
    assert first_desired['index']==predicted['index'] and first_desired['t']==predicted['t']
    same(first_desired['input_desired'],predicted['input_desired']);same(first_desired['decision'],predicted['decision'])
    first_index=next(i for i,(a,b) in enumerate(zip(tr['plans'],bt['plans'])) if a!=b)
    cut=tr['plans'][first_index]['t'];start=1788634800000
    same(tr['plans'][:first_index],bt['plans'][:first_index])
    same([s for s in tr['states'] if s['t']<=cut],[s for s in bt['states'] if s['t']<=cut])
    same(tr['renewal_submissions'],bt['renewal_submissions'])
    same(tr['opportunity_submissions'],bt['opportunity_submissions'])
    same(tr['coordination_submissions'][:2],bt['coordination_submissions'][:2])
    assert all(r['side']=='DOWN' for r in tr['direction_rows'])
    # Preserve V40's full monetary-work and route audit, stopping before its V40-specific report.
    import verify_btc5m_renewed_repair_work_v1 as old
    def renewal_modules():
        return roles,load('v41_renew_coord',PACKAGE/'coordination.py'),load('v41_renew_work',PACKAGE/'renewed_work.py')
    code=inspect.getsource(old.main);code=code[:code.index('    summaries={};all_legs={}')]+"    return local\n"
    ns=dict(old.__dict__,STEM=STEM,PACKAGE=PACKAGE,worker=worker,jobs=jobs,modules=renewal_modules,dump=dump)
    exec(compile(code,'V41_REUSE_FULL_RENEWAL_AUDIT','exec'),ns);renew=ns['main']()
    check=dict(status='PASS',rows=len(tr['growth_hold_rows']),reasons=dict(counts),events=events,
        weak_desired_unchanged_at_same_state=True,canonical_receipt_and_terminal_checks=True,source_index_join=True,
        first_desired_difference=first_desired,first_plan_difference=dict(source_index=tr['direction_rows'][first_index]['index'],
            plan_index=first_index,t=cut,seconds=(cut-start)/1000,baseline=bt['plans'][first_index],candidate=tr['plans'][first_index]),
        original_plan_prefix_count=first_index,confirmed_state_prefix_exact=True,renewal_audit=renew['status'])
    dump('HOLD_AUDIT',check)
    sums={};flows={};keylegs={};trace_data={}
    jobs_to_compare={'V39':'fixed15-core-loop-1977248-success-v38-known-20260913-v1','V40':BASEJOB,'V41':j['job_id']}
    for version,job in jobs_to_compare.items():
        root=R/'lan_worker_returns'/job;result=read(root/'result.json');trace=read(root/'clock_trace.json.gz')
        trace_data[version]=(result,trace)
        legs=canonical_legs(result,trace)
        sums[version]=summary(reconstruct([dict(x,t=x['t']-start) for x in legs]),'DOWN')
        news={o['key']:dict(t=p['t'],**o) for p in trace['plans'] for o in p['operations'] if o['kind']=='NEW'}
        kl=keyed_legs(result,trace,news);keylegs[version]=kl
        after=[x for x in kl if x['t']>sub['t']]
        flows[version]={s:dict(qty=sum(x['qty'] for x in after if x['side']==s),cash=sum(x['cash'] for x in after if x['side']==s)) for s in ('UP','DOWN')}
    repair_fields=('t','side','qty','cash','price','route')
    repair_equality={v:[{k:x[k] for k in repair_fields} for x in keylegs['V41'] if x['side']=='UP']==[{k:x[k] for k in repair_fields} for x in keylegs[v] if x['side']=='UP'] for v in ('V39','V40')}
    ba=read(BASEAUDIT);old_owner=next(o for o in ba['receipt_accounting']['orders'] if o['key']==sub['key'])
    service_receipt_exact=owner==old_owner
    deltas={v:{k:sums['V41']['terminal'][k]-sums[v]['terminal'][k] for k in ('up','down','cost')} for v in ('V39','V40')}
    post_delta={s:{k:flows['V41'][s][k]-flows['V40'][s][k] for k in ('qty','cash')} for s in ('UP','DOWN')}
    for side in ('UP','DOWN'):
        same(post_delta[side]['qty']-sum(post_delta[s]['cash'] for s in ('UP','DOWN')),deltas['V40'][side.lower()])
    pressure=[]
    for row,d,g in zip(tr['growth_hold_rows'],tr['demand_rows'],tr['addition_growth_rows']):
        if row['index']<arm['index']:continue
        if pressure and row['t']-pressure[-1]['t']<1000:continue
        state=d['state'];pq=state['pending_qty'];pc=state['pending_cash']
        pressure.append(dict(index=row['index'],t=row['t'],seconds=(row['t']-start)/1000,
            confirmed_payoff=state['payoff'],pending_qty=pq,pending_cash=pc,
            weak_payoff_if_all_pending_fill=state['payoff']['UP']+pq['UP']-sum(pc.values()),
            weak_payoff_if_only_strong_pending_fill=state['payoff']['UP']-pc['DOWN'],
            unheld_weight=g['weight'],applied_weight=row['decision']['applied_weight']))
    residual=remaining_work(tr,owner)
    result=dict(status='COMPLETE',execution='PASS',job_id=j['job_id'],native_jobs=1,native_seconds=au['native_elapsed_seconds'],
        model_fits=0,parameter_search=0,local_native_jobs=0,figures=0,summary=sums,terminal_deltas=deltas,
        extra_service=owner,service_receipt_exact_vs_v40=service_receipt_exact,up_repair_leg_equality=repair_equality,
        first_plan_difference=check['first_plan_difference'],growth_hold_arm=arm,hold_reasons=dict(counts),hold_released=released,
        work_events=tr['renewal_events'],remaining_goal_gap=max(0.,arm['goal']-sums['V41']['terminal']['up']),
        after_extra_service_flow=flows,flow_difference_vs_v40=post_delta,pending_pressure=pressure,
        original_raw_demand_equal_vs_v40=all(a['original_desired']==b['original_desired'] for a,b in zip(tr['addition_growth_rows'],bt['addition_growth_rows'])),
        negative_floor_area_deltas={v:sums['V41']['negative_floor_area']-sums[v]['negative_floor_area'] for v in ('V39','V40')},
        all_owners_terminal=au['all_owners_terminal'],all_pending_zero=au['all_pending_zero'],
        missing_terminal_clocks=au['terminal_timing']['missing_owner_observation_clocks'],manifest_sha256=sha(PACKAGE/'manifest.json'),
        source_result_sha256=sha(folder/'result.json'),source_trace_sha256=sha(folder/'clock_trace.json.gz'),
        decision='KEEP_AS_NEXT_MECHANISM_BASELINE_NOT_GRADUATED',learning_status='GROWTH_RELEASE_EFFECT_IDENTIFIED_REPAIR_WORK_STILL_UNFINISHED',
        remaining_work_diagnostic=residual,
        next_scope='With this growth control held fixed, distinguish residual service capacity from strong pending acquisition burden. Reuse existing finite continuation semantics with explicit dedup; do not merely sweep the Active count or force all negative payoffs to zero. No next package or job yet.',
        boundary='Consumed one-market held-coefficient ablation. Ceiling may also suppress Passive-driven coefficient growth. Runtime uses only confirmed OWN state, work/service memory and current public data. Pending scenarios are accounting diagnostics, not predicted fills. No learned Target formula or generalization claim.')
    dump('RESULT',result);dump('PROGRESS',dict(status='COMPLETE',native_submissions=1,native_jobs_completed=1,pending_jobs=[],model_fits=0))
    print(json.dumps(dict(status='PASS',native_seconds=au['native_elapsed_seconds'],hold_reasons=dict(counts),
        terminal={k:v['terminal'] for k,v in sums.items()},delta=deltas,first_difference=check['first_plan_difference'],
        service_exact=service_receipt_exact,repair_equal=repair_equality,gap=result['remaining_goal_gap']),allow_nan=False))


if __name__=='__main__':main()
