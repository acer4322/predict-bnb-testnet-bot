"""Read-only V31 causal policy, complete receipt accounting and path contrast."""
import hashlib
import bisect
import collections
import inspect
import json
import math
import types
from prepare_btc5m_held_amplitude_v1 import *
import verify_btc5m_unbudgeted_opportunity_v1 as unbudgeted
import verify_btc5m_active_repair_opportunity_v1 as previous
import verify_btc5m_whole_oracle_repair_v1 as automatic
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary
from hft244_pair_route_legality_v1 import crossing_owners

END=START+300000


def accounting_auditor():
    source=inspect.getsource(unbudgeted.auditor)
    marker="    module=load('verified_unbudgeted_decide',PACKAGE/'active_opportunity.py')"
    extra='''    source=once(source,"assert len(active) == result['active_native_submits'] == (tag=='active')",
        "assert len(active) == result['active_native_submits'] == len(trace['opportunity_submissions'])+len(trace['coordination_submissions']) <= 2")
    source=once(source,"assert op['qty'] == selected['quantity'] and op['qty'] != 15.","assert op['qty'] == selected['quantity']")
'''
    source=once(source,marker,extra+marker)
    ns=dict(unbudgeted.__dict__,PACKAGE=PACKAGE,folder=lambda tag:RET/JOB)
    exec(compile(source,'held_amplitude_mixed_route_audit','exec'),ns)
    return ns['auditor']()


def automatic_audit(n,nt):
    # Existing work-item audit, with the authorized 15/15 ticket/minimum.
    source=inspect.getsource(automatic.audit_automatic)
    source=source.replace('30.', '15.').replace('18.', '15.').replace('<18 ', '<15 ')
    ns=dict(automatic.__dict__);exec(compile(source,'fixed15_automatic_work_audit','exec'),ns)
    out=ns['audit_automatic']('repeat',n,nt)
    gate=load('v31_capacity',PACKAGE/'demand_gate.py')
    for row in nt['demand_rows']:
        capacity=gate.economic_capacity(row['state'],row['original_desired'],row['eligibility']['price'],15.,.01)
        for k,v in capacity.items():previous.same_value(v,row['eligibility'][k])
    return out


def inherited(n,nt):
    source=read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    books={r['received_ms']:r for r in source['books']};demand={r['t']:r for r in nt['demand_rows']}
    plans={r['t']:r['operations'] for r in nt['plans']};terminal={}
    for r in nt['demand_owner_rows']:
        if r['state']=='TERMINAL' and r['filled']>1e-8:terminal.setdefault(r['key'],r['t'])
    all_terminal={}
    for r in nt['demand_owner_rows']:
        if r['state']=='TERMINAL':all_terminal.setdefault(r['key'],r['t'])
    firsts=nt['opportunity_submissions'];seconds=nt['coordination_submissions']
    assert len(firsts)<=1 and len(seconds)<=1 and (not seconds or firsts)
    first=firsts[0]['key'] if firsts else None;second=seconds[0]['key'] if seconds else None
    confirmed=lambda t:t>=terminal.get(first,float('inf'))
    coord=load('v31_coordination',PACKAGE/'coordination.py');assert coord.MODE=='RESTORE'
    prev=None;seen=False;episode=None
    for row in nt['coordination_rows']:
        t=row['t'];s=row['state'];book=books[t];assert book['source_ms']<=t and s==demand[t]['state']
        if episode is None:episode=coord.detect(prev,s,confirmed(t),seen,t)
        if confirmed(t) and prev and s['inv']['DOWN']>prev['inv']['DOWN']+1e-8:seen=True
        prev={k:s[k] for k in ('inv','cost','payoff')}
        expected=coord.decide(s,row['original_operations'],round(1-book['best_bid'],10),dict(book['bids'])[book['best_bid']],episode,confirmed(t),crossing_owners)
        for k,v in expected.items():previous.same_value(v,row[k])
        if row['eligible']:
            assert seconds and seconds[0]['t']==t
            assert plans[t]==row['original_operations']+[{k:v for k,v in seconds[0].items() if k!='t'}]
        else:assert plans[t]==row['original_operations']
    assert episode==nt['coordination_episode']==n['coordination_episode']
    assert sum(r['eligible'] for r in nt['coordination_rows'])==len(seconds)
    repair=load('v31_repair',PACKAGE/'commitment_repair.py');assert repair.CONCURRENT and repair.LEGAL_QUOTE
    submissions=nt['commitment_repair_submissions'];births={r['key']:r for r in submissions}
    for row in nt['commitment_repair_rows']:
        t=row['t'];book=books[t];assert book['source_ms']<=t and row['state']==demand[t]['state']
        outstanding=[s['key'] for s in submissions if s['t']<t and all_terminal.get(s['key'],float('inf'))>t]
        assert outstanding==row['outstanding']
        expected=repair.decide(row['state'],row['original_operations'],demand[t]['eligibility']['price'],round(1-book['best_bid'],10),confirmed(t),outstanding,crossing_owners)
        for k,v in expected.items():previous.same_value(v,row[k])
        actual=[o for o in plans[t] if not (o['kind']=='NEW' and o['key']==second)]
        if row['eligible']:
            sub=next(s for s in submissions if s['t']==t)
            assert actual==row['original_operations']+[{k:v for k,v in sub.items() if k!='t'}]
        else:assert actual==row['original_operations']
    assert sum(r['eligible'] for r in nt['commitment_repair_rows'])==len(submissions)
    maintenance={(r['t'],r['key']):r for r in nt['demand_maintenance_rows']}
    for row in nt['commitment_maintenance_rows']:
        t=row['t'];quote=repair.legal_quote(row['quote']['raw_price'],round(1-books[t]['best_bid'],10))
        previous.same_value(quote,row['quote'])
        assert row['new_stale']==(abs(row['limit']-quote['price'])>row['threshold']+1e-9)
        observed=maintenance[(t,row['key'])]
        assert observed['stale']==row['new_stale'] and observed['current_passive_price']==quote['price']
    scope=load('v31_scope',PACKAGE/'maintenance_scope.py');assert scope.MODE=='LEGAL'
    gate=terminal.get(second,float('inf'));owners={r['key']:r for r in nt['demand_final']['all_final_carriers']}
    scoped={(r['t'],r['key']):r for r in nt['maintenance_scope_rows']}
    assert scoped.keys()=={(t,k) for (t,k),r in maintenance.items() if t>=gate and r['side']=='DOWN' and owners[k]['route']=='PASSIVE'}
    threshold=.01*(1+math.log1p(math.exp(n['theta'][11])))
    for (t,key),row in scoped.items():
        assert row['active_key']==second and row['active_state']=='TERMINAL'
        previous.close(row['active_filled'],owners[second]['filled'])
        previous.close(row['threshold'],threshold)
        assert row['is_extra']==(key in births and births[key]['t']<t)
        previous.close(row['quote']['raw_price'],demand[t]['eligibility']['price'])
        expected=scope.decide(row['quote']['raw_price'],round(1-books[t]['best_bid'],10),row['limit'],threshold,row['is_extra'],repair.legal_quote)
        for k,v in expected.items():previous.same_value(v,row[k])
        observed=maintenance[(t,key)];assert observed['stale']==row['stale'] and observed['owner_state']==row['owner_state']
        previous.close(observed['current_passive_price'],row['price'])
    for (t,key),row in maintenance.items():
        must_cancel=(row['stale'] or row['surplus']) and row['cancellable'] and row['owner_state']!='CANCEL_PENDING'
        if must_cancel:assert any(o['kind']=='CANCEL' and o['key']==key for o in plans[t])
    return dict(status='PASS',coordination_rows=len(nt['coordination_rows']),commitment_rows=len(nt['commitment_repair_rows']),
        maintenance_rows=len(maintenance),scoped_rows=len(scoped),original_active=firsts,additional_active=seconds)


def phases(n,nt):
    legs=previous.canonical_legs(n,nt);out={}
    for start,end in [(0,50),(0,150),(150,300),(0,300)]:
        selected=[r for r in legs if START+start*1000<=r['t']<START+end*1000]
        out[f'{start}_{end}']={s:{route:dict(qty=sum(r['qty'] for r in selected if r['side']==s and r['route']==route),
            cash=sum(r['cash'] for r in selected if r['side']==s and r['route']==route)) for route in ('MAKER','TAKER')} for s in ('UP','DOWN')}
    return out


def bottlenecks(n,nt,checked):
    """Post-first-Active legal opportunities on the actual path, not a replay."""
    module=load('v31_route_observation',PACKAGE/'active_opportunity.py')
    src=read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    books={b['received_ms']:b for b in src['books']};demand={r['t']:r for r in nt['demand_rows']}
    paid=dict(UP=0.,DOWN=0.);series=[(START,dict(paid))]
    for row in previous.reconstruct(previous.canonical_legs(n,nt)):
        for side in paid:paid[side]+=sum(row['flow'][side][r]['cash'] for r in ('MAKER','TAKER'))
        series.append((row['t'],dict(paid)))
    times=[x[0] for x in series];firsts=nt['opportunity_submissions'];terminal=None
    if firsts:terminal=next((r['t'] for r in nt['demand_owner_rows'] if r['key']==firsts[0]['key'] and r['state']=='TERMINAL' and r['filled']>1e-8),None)
    rows=[]
    for row in nt['coordination_rows']:
        t=row['t'];book=books[t]
        if terminal is None or t<terminal:continue
        ops=row['original_operations'];ask=round(1-book['best_bid'],10)
        if any(o['kind']=='NEW' and o['side']=='DOWN' for o in ops):continue
        # Stronger scope than a raw unplaceable quote: no legal Passive15 price below ask.
        if ask>.07+1e-8:continue
        hypothetical=module.decide(row['state'],series[bisect.bisect_right(times,t)-1][1],
            demand[t]['eligibility']['price'],ask,dict(book['bids'])[book['best_bid']],ops,.01,0.,crossing_owners)
        if not hypothetical['eligible']:continue
        assert len(row['state']['owners'])+sum(o['kind']=='NEW' for o in ops)<4096
        assert row['reason']=='WAIT_FOR_CONFIRMED_REEXPOSURE_EPISODE' and not row['eligible']
        minimum=round(math.ceil((1/ask-1e-8)/.01)*.01,8)
        assert minimum<=hypothetical['quantity']+1e-8
        rows.append(dict(t=t,seconds=(t-START)/1000,state=row['state'],own_gate=row['reason'],
            opportunity=hypothetical,minimum_order_quantity=minimum,
            minimum_limit_cost=minimum*ask,minimum_weak_lift_if_filled=minimum*(1-ask),
            scope='Static decision feasibility on an already consumed path. No submission or inferred fills; not a forecast and not an additive saving.'))
    owners={r['key']:r for r in nt['demand_final']['all_final_carriers']}
    overlay=[owners[r['key']] for r in nt['commitment_repair_submissions']]
    hist={k:dict(collections.Counter(r['reason'] for r in nt[k])) for k in ('opportunity_rows','coordination_rows','commitment_repair_rows')}
    return dict(status='PASS',reason_histograms=hist,first_active_terminal_t=terminal,
        later_legal_active_observation_rows=len(rows),first_later_legal_active=rows[0] if rows else None,
        later_legal_active_observations=rows,
        overlay=dict(orders=len(overlay),filled=sum(r['filled'] for r in overlay),cash=sum(r['payment']+r['fees'] for r in overlay),
            positive_fill_owners=sum(r['filled']>1e-8 for r in overlay)),
        both_positive_seconds=checked['trajectory']['both_positive_seconds'],additional_active=len(nt['coordination_submissions']),
        maintenance_scope_rows=len(nt['maintenance_scope_rows']),
        conclusion='Direction retention works, but neither partial repair success nor later legal Active capacity re-arms the inherited second-Active gate. It requires a previous both-positive state; this path never has one. Its dependent all-DOWN maintenance scope consequently never activates.')


def verify():
    m=read(PACKAGE/'manifest.json');parent=read(PARENT/'manifest.json')
    assert sha(PARENT/'manifest.json')==m['parent_manifest_sha256']
    assert all(sha(PACKAGE/k)==v for k,v in m['files'].items())
    assert all(sha(PARENT/k)==v and sha(PACKAGE/k)==v for k,v in parent['files'].items() if k!='money_runner.py')
    assert sha(BASE/'result.json')==m['baseline_result_sha256'] and sha(BASE/'clock_trace.json.gz')==m['baseline_trace_sha256']
    b,bt=get(BASE);n,nt=get(RET/JOB);assert n['theta']==b['theta'] and n['capital_cap'] is None
    source_input=read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    assert [p['t'] for p in nt['plans']]==[b['received_ms'] for b in source_input['books']]
    assert n['clock_smoke']['actual_replay_frames']==len(source_input['books'])==1462
    assert n['source_frames']==n['clock_smoke']['runtime_clock_total_frames']==m['fixed_train_total_frames']==1487.
    source=compile_source(PACKAGE);remote='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+PACKAGE.name
    for a,z in [(str(PACKAGE).replace('\\','\\\\'),remote.replace('\\','\\\\')),(str(PACKAGE),remote),(PACKAGE.as_posix(),remote.replace('\\','/'))]:source=source.replace(a,z)
    assert hashlib.sha256(source.encode()).hexdigest()==n['clock_smoke']['transformed_source_sha256']
    runner=load('v31_intent_audit',PACKAGE/'money_runner.py');c=runner.ExposureIntent('ORACLE_UP')
    observations={r['t']:r for r in nt['observations']}
    for row in nt['intent']:
        inv=row['inv'];legacy=math.tanh(n['theta'][3]*((inv['UP']-inv['DOWN'])/(1+sum(inv.values()))))
        previous.close(legacy,row['legacy_exposure'])
        f=dict(t=row['t'],index=row['index'],own_view=dict(inv=inv))
        value=c.apply(legacy,0.,f);previous.close(value,row['applied_exposure'])
        original=observations[row['t']]['desired'];gross=sum(original.values())
        previous.close(original['UP'],gross*(1+value)/2);previous.close(original['DOWN'],gross*(1-value)/2)
    assert len(c.amplitude_rows)==len(nt['exposure_amplitude_rows'])
    for expected,actual in zip(c.amplitude_rows,nt['exposure_amplitude_rows']):previous.same_value(expected,actual)
    previous.same_value(c.amplitude_birth,n['clock_smoke']['amplitude_birth'])
    previous.close(c.held_amplitude,n['clock_smoke']['held_amplitude'])
    bp={r['t']:r['operations'] for r in bt['plans']};np={r['t']:r['operations'] for r in nt['plans']};assert bp.keys()==np.keys()
    forks=[t for t in np if np[t]!=bp[t]];cut=min(forks)
    prefix={k:[r for r in bt[k] if r['t']<cut]==[r for r in nt[k] if r['t']<cut] for k in ('states','plans','native_actions','demand_owner_rows')}
    assert all(prefix.values()),prefix
    before_b=next(r for r in bt['demand_rows'] if r['t']==cut);before_n=next(r for r in nt['demand_rows'] if r['t']==cut)
    assert before_b['state']==before_n['state']
    checked=accounting_auditor()('active',n,nt);works=automatic_audit(n,nt);rules=inherited(n,nt)
    checked['receipt_time_trajectory']=automatic.receipt_time_path(n,nt)
    terminal_times=[r['terminal_observed_t'] for r in checked['receipts']['orders']]
    assert all(t is not None for t in terminal_times)
    timing=dict(actual_book_frames=1462,fixed_training_clock_denominator=1487.,intent_rows=len(nt['intent']),
        all_book_receive_times_match_plans=True,last_canonical_terminal_seconds=(max(terminal_times)-START)/1000,
        note='Book receive-time and canonical owner observations. Last terminal after market end is cleanup observation, not an exact exchange terminal timestamp. No Target event clock is used by the actor.')
    diagnosis=bottlenecks(n,nt,checked);dump('BOTTLENECKS',diagnosis)
    base_geometry=geometry(b['final_inventory'],b['final_cost'])
    delta={k:checked['terminal'][k]-base_geometry[k] for k in ('up','down','cost','up_net')}
    diagnose=types.FunctionType(previous.diagnose.__code__,dict(previous.diagnose.__globals__,STEM=STEM),'v31_flows')
    flow=diagnose(dict(control=(b,bt),active=(n,nt)),delta)
    fd=read(R/(STEM+'_FLOW_DIAGNOSTIC.json'));fd['interpretation']='Held OWN amplitude only. Later OWN state, Active/Passive decisions and execution can differ; not shared cash-budget competition.';dump('FLOW_DIAGNOSTIC',fd)
    post=read(R/(STEM+'_POSTCHECK.json'));assert post['status']=='PASS' and post['native_sha256']==m['native_sha256']
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert n['sizing_installation']==pins and all(sha(ROOT/'tools'/name)==pin['before'] for name,pin in pins.items())
    target_path=R/'BTC5M_POST_EXPOSURE_RESPONSE_V1_20260913_RESULT.json';target=read(target_path)['target']['2026085']
    assert target['source_sha256']==m['oracle_source_sha256']
    out=dict(status='COMPLETE',verification='PASS',new_native_jobs=1,reused_native_controls=1,local_native_jobs=0,model_fits=0,parameter_search=0,
        runtime_eligible=False,manifest_sha256=sha(PACKAGE/'manifest.json'),amplitude_birth=c.amplitude_birth,held_amplitude=c.held_amplitude,
        timing=timing,native_elapsed_seconds=read(R/(STEM+'_COLLECT.json'))['status']['elapsed_seconds'],
        first_plan_fork=dict(seconds=(cut-START)/1000,t=cut,prefix=prefix,same_pre_plan_state=True,baseline=bp[cut],candidate=np[cut],
            baseline_demand=before_b,candidate_demand=before_n),
        baseline=dict(terminal=base_geometry,trajectory=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),bt['states'],START,END),
            post_exposure=previous.analyze(previous.reconstruct(previous.canonical_legs(b,bt)),START,END),phases=phases(b,bt)),
        candidate=dict(checked,phases=phases(n,nt)),terminal_delta=delta,flow_diagnosis=flow,finite_works=works,inherited_rules=rules,
        bottlenecks={k:v for k,v in diagnosis.items() if k!='later_legal_active_observations'},
        target=dict(terminal=target['terminal'],source_sha256=sha(target_path)),worker_postcheck='PASS',
        limitations='Consumed single market with a future-derived direction bit, frozen native fee model and inherited controller; no fitted model, held-out replication or private Target objective identification.')
    dump('RESULT',out);dump('PROGRESS',dict(status='COMPLETE',verification='PASS',job_id=JOB,new_native_jobs=1,model_fits=0,parameter_search=0))
    print(json.dumps(dict(status='PASS',terminal=checked['terminal'],delta=delta,first_fork_seconds=(cut-START)/1000,
        held_amplitude=c.held_amplitude,active_submits=checked['active_submits'],passive_submits=checked['passive_submits'],
        trajectory={k:checked['trajectory'][k] for k in ('both_positive_seconds','negative_floor_area_currency_seconds','minimum_floor','reversed_net_seconds')},
        flow=flow,rules=rules),indent=2))
    return out


if __name__=='__main__':verify()
