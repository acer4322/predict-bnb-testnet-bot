"""Offline V42 pure decisions, lifecycle, paired economics and common prefix."""
import argparse
import math
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace

from btc5m_pending_repair_pair_v1 import *
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs
from btc5m_partial_reexposure_experiment_v1 import keyed_legs
from report_btc5m_success_case_benchmark_v1 import summary
from audit_btc5m_post_exposure_response_v1 import reconstruct


def inspect_arm(arm):
    w=worker(arm);j=job(arm);pck=package(arm);folder=R/'lan_worker_returns'/j['job_id']
    au=read(w.artifact(j,'AUDIT'));assert au['execution_status']=='PASS'
    assert read(w.artifact(j,'CONTINUATION_AUDIT'))['status']=='PASS' and read(w.artifact(j,'POSTCHECK'))['status']=='PASS'
    n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz')
    bt=read(R/'lan_worker_returns'/BASEJOB/'clock_trace.json.gz')
    roles,coord,service,hold=modules(arm);roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    memory=load('v42_work_'+arm,pck/'renewed_work.py').WorkMemory()
    old=tr['coordination_submissions'][:2];subs=tr['renewal_submissions'];owners={o['key']:o for o in au['receipt_accounting']['orders']}
    same(old,bt['coordination_submissions'][:2]);same(tr['opportunity_submissions'],bt['opportunity_submissions'])
    same(tr['coordination_rows'],bt['coordination_rows']);same(tr['coordination_episode'],bt['coordination_episode'])
    same(subs[0],bt['renewal_submissions'][0])
    assert len(subs)==2 and n['active_native_submits']==5 and subs[0]['work_id']==subs[1]['work_id']
    plans={d['index']:p for d,p in zip(tr['direction_rows'],tr['plans'])}
    books={d['index']:b for d,b in zip(tr['direction_rows'],read(pck/'inputs/public_1977248.json.gz')['books'])}
    born={o['key']:i for i,p in plans.items() for o in p['operations'] if o['kind']=='NEW'}
    selected=[(i,d) for i,d in zip(tr['intent'],tr['demand_rows']) if i['t']>old[1]['t']]
    assert len(selected)==len(tr['renewal_rows'])
    issued=[];counts=Counter()
    for row,(intent,demand) in zip(tr['renewal_rows'],selected):
        index=intent['index'];t=row['t'];assert t==intent['t'];same(row['state'],demand['state'])
        for o,status in zip(old,row['old_service_states']):
            assert (status=='TERMINAL')==(t>=owners[o['key']]['terminal_observed_t'])
        z=memory.advance(row['state'],t,tr['coordination_episode'],row['old_service_states'],coord.detect);same(z,row['status'])
        is_cont=bool(issued);assert row['continuation']==is_cont
        prior=None
        if issued:
            prior=SimpleNamespace(state=row['previous_service_state'])
            assert (prior.state=='TERMINAL')==(t>=owners[issued[0]['key']]['terminal_observed_t'])
        else:assert row['previous_service_state'] is None
        ready=service.ready(z['work'],issued,prior) if is_cont else True
        ops=deepcopy(row['original_operations']);book=books[index]
        same(row['active_ask'],book['best_ask']);same(row['visible_depth'],book['asks'][0][1] if book['asks'] else 0.)
        if z['work'] is not None and len(issued)<2 and ready:
            if is_cont:d=service.decide(coord.decide,row['state'],ops,row['active_ask'],row['visible_depth'],z['work'],crossing_owners)
            else:d=coord.decide(row['state'],ops,row['active_ask'],row['visible_depth'],z['work'],True,crossing_owners,continuation=False)
            assert row['decision']['reason']!='RESOURCE_OWNER_LIMIT';same(d,row['decision']);counts[d['reason']]+=1
            assert row['submitted']==d['eligible']
            if d['eligible']:
                wanted='ACTIVE_RENEWED_FINITE_CONTINUATION' if is_cont else 'ACTIVE_RENEWED_FINITE_REPAIR'
                op=next(o for o in plans[index]['operations'] if o.get('role')==wanted)
                same(op['qty'],d['quantity']);same(op['price'],row['active_ask'])
                assert op['side']=='UP' and op['parent_id']==1
                issued.append(dict(work_id=z['work']['id'],anchor=z['work']['anchor_floor'],t=t,**op));ops.append(op)
        else:assert row['decision'] is None and not row['submitted']
        same(ops,plans[index]['operations'])
    same(issued,subs);same(memory.events,tr['renewal_events']);same(memory.work,tr['renewal_work'])
    armed=tr['growth_hold_arm'];at=next(g for i,g in zip(tr['intent'],tr['addition_growth_rows']) if i['index']==armed['index'])
    same(armed,dict(key=subs[0]['key'],work_id=subs[0]['work_id'],goal=subs[0]['anchor'],ceiling=at['weight'],t=subs[0]['t'],index=born[subs[0]['key']]))
    hold_events=[dict(kind='ARM',**armed)];released=False;hold_counts=Counter();suppressed_release=[]
    for row,g,intent,demand in zip(tr['growth_hold_rows'],tr['addition_growth_rows'],tr['intent'],tr['demand_rows']):
        assert row['t']==intent['t']==g['t'] and row['index']==intent['index']
        ar=armed if row['index']>armed['index'] else None;same(row['arm'],ar)
        same(row['confirmed_weak_payoff'],g['inv']['UP']-g['cost']);same(row['input_desired'],g['effective_desired'])
        same(row['strong_pending_cash'],demand['state']['pending_cash']['DOWN'])
        past=[s for s in subs if born[s['key']]<row['index']]
        assert [r['key'] for r in row['service_receipts']]==[s['key'] for s in past]
        for r in row['service_receipts']:
            o=owners[r['key']];assert o['terminal_observed_t'] is not None
            assert (r['state']=='TERMINAL')==(row['t']>=o['terminal_observed_t'])
            q=sum(f['fill_increment'] for e in n['atomic_responsibility_events'] if e['t']<=row['t'] for f in e['fill_rows'] if f['key']==r['key'])
            same(q,r['filled'])
        aggregate=service.aggregate_receipts(row['service_receipts']);same(aggregate,row['receipt'])
        d=hold.decide(g,ar,aggregate,row['confirmed_weak_payoff'],released,row['strong_pending_cash'])
        same(d,row['decision']);same(d['effective_desired'],row['effective_desired'])
        if d['released'] and not released:hold_events.append(dict(kind='RELEASE',t=row['t'],index=row['index'],reason=d['reason']))
        if ar and not released and aggregate and aggregate['state']=='TERMINAL' and aggregate['filled']>0 and row['confirmed_weak_payoff']>=ar['goal'] and row['confirmed_weak_payoff']-row['strong_pending_cash']<ar['goal']:
            suppressed_release.append(dict(t=row['t'],index=row['index'],confirmed_payoff=row['confirmed_weak_payoff'],strong_pending_cash=row['strong_pending_cash']))
        released=d['released'];hold_counts[d['reason']]+=1
    same(hold_events,tr['growth_hold_events'])
    first=next(i for i,(a,b) in enumerate(zip(tr['plans'],bt['plans'])) if a!=b);cut=tr['plans'][first]['t']
    same(tr['plans'][:first],bt['plans'][:first]);same([s for s in tr['states'] if s['t']<=cut],[s for s in bt['states'] if s['t']<=cut])
    assert cut==subs[1]['t']
    same([{k:v for k,v in o.items()} for o in tr['plans'][first]['operations'] if o.get('key')!=subs[1]['key']],bt['plans'][first]['operations'])
    baseowners={o['key']:o for o in read(BASEAUDIT)['receipt_accounting']['orders']}
    first_four=tr['opportunity_submissions']+tr['coordination_submissions'][:3]
    for o in first_four:same(owners[o['key']],baseowners[o['key']])
    legs=canonical_legs(n,tr);sm=summary(reconstruct([dict(x,t=x['t']-1788634800000) for x in legs]),'DOWN')
    news={o['key']:dict(t=p['t'],**o) for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW'}
    kl=keyed_legs(n,tr,news);after=[x for x in kl if x['t']>cut]
    flow={s:dict(qty=sum(x['qty'] for x in after if x['side']==s),cash=sum(x['cash'] for x in after if x['side']==s)) for s in ('UP','DOWN')}
    op=owners[subs[1]['key']]
    out=dict(status='PASS',arm=arm,job_id=j['job_id'],native_seconds=au['native_elapsed_seconds'],renewal_rows=len(tr['renewal_rows']),hold_rows=len(tr['growth_hold_rows']),
        all_pure_decisions_reconstructed=True,first_four_receipts_exact=True,reason_counts=dict(counts),hold_reasons=dict(hold_counts),
        growth_release=hold_events,prevented_pending_release=suppressed_release,
        first_plan_difference=dict(index=first,source_index=tr['direction_rows'][first]['index'],t=cut,seconds=(cut-1788634800000)/1000,baseline=bt['plans'][first],candidate=tr['plans'][first]),
        confirmed_prefix_exact=True,new_service=op,service_submission=subs[1],summary=sm,work_events=tr['renewal_events'],work_end=tr['renewal_work'],
        held_original_goal_gap=max(0.,armed['goal']-sm['terminal']['up']),post_service_flow=flow,
        all_owners_terminal=au['all_owners_terminal'],all_pending_zero=au['all_pending_zero'],missing_terminal_clocks=au['terminal_timing']['missing_owner_observation_clocks'],
        result_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'))
    adump(arm,'NEW_AUDIT',out)
    print(json.dumps(dict(status='PASS',arm=arm,terminal=sm['terminal'],service=op,hold_events=hold_events,prevented_release_rows=len(suppressed_release),first_difference_seconds=out['first_plan_difference']['seconds']),allow_nan=False))
    return out


def report():
    audits={a:read(R/(astem(a)+'_NEW_AUDIT.json')) for a in ARMS};assert all(a['status']=='PASS' for a in audits.values())
    traces={a:read(R/'lan_worker_returns'/job(a)['job_id']/'clock_trace.json.gz') for a in ARMS}
    a,b=traces['current'],traces['pending'];first=next(i for i,(p,q) in enumerate(zip(a['plans'],b['plans'])) if p!=q)
    cut=a['plans'][first]['t'];same(a['plans'][:first],b['plans'][:first]);same([s for s in a['states'] if s['t']<=cut],[s for s in b['states'] if s['t']<=cut])
    pa=a['plans'][first]['operations'];pb=b['plans'][first]['operations']
    assert len(pa)==len(pb)
    differences=[]
    for x,y in zip(pa,pb):
        if x!=y:
            assert x['role']==y['role']=='ACTIVE_RENEWED_FINITE_CONTINUATION'
            same({k:v for k,v in x.items() if k!='qty'},{k:v for k,v in y.items() if k!='qty'})
            differences.append(dict(key=x['key'],current_qty=x['qty'],pending_qty=y['qty'],price=x['price']))
    assert len(differences)==1
    # A larger NEW request is not an economic fill. Compare realized paths separately.
    realized={};maintenance={}
    for arm,trace in traces.items():
        result=read(R/'lan_worker_returns'/job(arm)['job_id']/'result.json')
        realized[arm]=canonical_legs(result,trace)
        owner=audits[arm]['new_service'];key=owner['key'];cancel=owner['cancel_times'][0]
        row=next(r for r in trace['demand_maintenance_rows'] if r['key']==key and r['t']==cancel)
        idx=next(i for i,r in enumerate(trace['intent']) if r['t']==cancel)
        state=trace['demand_rows'][idx]['state'];desired=trace['intent'][idx]['desired']['UP']
        w=result['theta'];owned=state['inv']['UP']+state['pending_qty']['UP']
        threshold=.01*(1+math.log1p(math.exp(w[11])))
        surplus_threshold=-math.exp(w[7])/(1+math.exp(-w[11]))
        assert row['stale']==(abs(owner['limit']-row['current_passive_price'])>threshold+1e-9)
        assert row['surplus']==(desired-owned<surplus_threshold)
        assert row['cancellable'] and row['stale'] and row['surplus']
        rr=next(r for r in trace['renewal_rows'] if r['t']==cancel)
        assert rr['status']['work'] is not None
        assert not any(r.get('key')==key for r in trace['maintenance_scope_rows'])
        maintenance[arm]=dict(owner=owner,cancel_after_submit_ms=cancel-owner['born_t'],
            maintenance=row,confirmed_payoff=state['payoff'],finite_work_id=rr['status']['work']['id'],finite_goal=rr['status']['work']['anchor_floor'],
            raw_price_distance=abs(owner['limit']-row['current_passive_price']),stale_threshold=threshold,
            weak_desired=desired,weak_confirmed_plus_reserved=owned,desired_minus_owned=desired-owned,surplus_threshold=surplus_threshold,
            owner_progress=[r for r in trace['demand_owner_rows'] if r['key']==key],
            raw_receipts=[r for r in trace['demand_final']['full_raw_receipts'] if r['key']==key],
            remaining_unfilled=owner['qty']-owner['filled'],new_notional=owner['qty']*owner['limit'],
            passive_maintenance_scope_applied=False,executed_below_one_dollar_is_partial_fill=owner['payment']<1,
            limit='Both stale and surplus predicates are true; this run does not isolate which alone determines eventual fills. No retained-order counterfactual has run.')
    same(realized['current'],realized['pending']);same(a['states'],b['states'])
    differing_plans=sum(p!=q for p,q in zip(a['plans'],b['plans']))
    assert differing_plans==1
    diag=dict(status='PASS',arms=maintenance,all_realized_legs_equal=True,all_confirmed_states_equal=True,only_one_plan_differs=True,
        historical_boundary='V14 already demonstrated surplus cancellation for a Passive repair; V25/V27 corrected Passive stale scope. Reuse those lessons. This is the concrete renewed Active remainder, excluded by owner.route != PASSIVE from that scope and the old terminal-residual candidate path.',
        next_scope='Study how this unfinished finite work owns its Active remainder through partial fill, cancellation and canonical terminal. Compare justified maintenance/replace decisions, not unconditional KEEP and not a larger requested size sweep.')
    dump('MAINTENANCE_DIAGNOSTIC',diag)
    delta={k:audits['pending']['summary']['terminal'][k]-audits['current']['summary']['terminal'][k] for k in ('up','down','cost')}
    fd={s:{k:audits['pending']['post_service_flow'][s][k]-audits['current']['post_service_flow'][s][k] for k in ('qty','cash')} for s in ('UP','DOWN')}
    for s in fd:same(fd[s]['qty']-sum(x['cash'] for x in fd.values()),delta[s.lower()])
    baseline=read(R/'BTC5M_REPAIR_GROWTH_HOLD_V1_20260913_RESULT.json')['summary']['V41']
    out=dict(status='COMPLETE',execution='PASS',native_jobs=2,native_seconds=sum(x['native_seconds'] for x in audits.values()),
        model_fits=0,parameter_search=0,local_native_jobs=0,figures=0,arms=audits,baseline=baseline,
        paired_first_difference=dict(index=first,t=cut,seconds=(cut-1788634800000)/1000,changes=differences,other_operations_same=True,confirmed_prefix_same=True),
        pending_minus_current=delta,flow_difference=fd,
        vs_v41={a:{k:x['summary']['terminal'][k]-baseline['terminal'][k] for k in ('up','down','cost')} for a,x in audits.items()},
        manifests={a:sha(package(a)/'manifest.json') for a in ARMS},
        all_realized_legs_equal=True,all_confirmed_states_equal=True,only_one_plan_differs=True,
        maintenance_diagnostic=diag,decision='NO_INCREMENTAL_SIZING_EFFECT_ON_THIS_EXECUTION_PATH',
        learning_status='PARTIAL_REPAIR_EXECUTED_UNFINISHED_REMAINDER_NOT_SERVICED',
        common_pending_release_guard_native_exercised=False,
        boundary='A/B isolates pending-burden continuation sizing. Versus V41 combines continuation authority and common pending-aware growth release. No positive guard-release condition occurred, so its preventive effect is component-tested only. Same consumed market and direction bit; not learned Target or unseen graduation.')
    dump('RESULT',out);dump('PROGRESS',dict(status='COMPLETE',native_submissions=2,native_jobs_completed=2,pending_jobs=[]))
    print(json.dumps(dict(status='COMPLETE',native_seconds=out['native_seconds'],delta=delta,flow_difference=fd,vs_v41=out['vs_v41']),allow_nan=False))
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('audit','report'));p.add_argument('--arm',choices=ARMS,default='current');a=p.parse_args()
    inspect_arm(a.arm) if a.action=='audit' else report()
