"""Read-only full mechanism and trajectory attribution for the V44 native job."""
from collections import Counter
from copy import deepcopy
import inspect
import json
import sys
from types import SimpleNamespace

import btc5m_tail_acquisition_experiment_v1 as d
from verify_btc5m_transfer_components_v1 import same
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs


def direction_metrics(states,start,end,strong):
    weak='DOWN' if strong=='UP' else 'UP'
    previous=dict(inv=dict(UP=0.,DOWN=0.),cost=0.);t0=start
    out=dict(selected_direction=strong,weak_side=weak,opposite_net_seconds=0.,weak_loss_area_currency_seconds=0.)
    for row in [s for s in states if start<s['t']<=end]+[dict(t=end)]:
        duration=(row['t']-t0)/1000.;assert duration>=0
        if previous['inv'][strong]<previous['inv'][weak]-1e-8:out['opposite_net_seconds']+=duration
        out['weak_loss_area_currency_seconds']+=max(0.,previous['cost']-previous['inv'][weak])*duration
        if 'inv' in row:previous=row
        t0=row['t']
    out['terminal_selected_minus_opposite_shares']=previous['inv'][strong]-previous['inv'][weak]
    out['terminal_direction_retained']=out['terminal_selected_minus_opposite_shares']>0
    return out


def main():
    w=d.worker();job=d.job();audit=d.read(w.artifact(job,'AUDIT'))
    assert audit['execution_status']=='PASS'
    folder=d.R/'lan_worker_returns'/d.JOB
    n=d.read(folder/'result.json');tr=d.read(folder/'clock_trace.json.gz')
    oldfolder=d.R/'lan_worker_returns'/d.PARENT_JOB['job_id']
    bn=d.read(oldfolder/'result.json');bt=d.read(oldfolder/'clock_trace.json.gz')
    oldaudit=d.read(d.R/(d.parent.STEM+'_1977248_known_AUDIT.json'))
    # All preexisting commitment, coordination and maintenance decisions.
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.STEM=d.STEM;cv.PACKAGE=d.PACKAGE;cv.jobs=lambda:[None,job];cv.dump=d.dump
    code=d.once(inspect.getsource(cv.main),'episode,confirmed,crossing_owners)',"episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    ns=dict(cv.__dict__);exec(compile(code,'V44_INHERITED_DECISIONS','exec'),ns);ns['main']()
    sys.path.insert(0,str(d.PACKAGE))
    from roles_runtime import roles
    import coordination as coord
    import pending_service as service
    import depth_slippage as depth
    import growth_hold as hold
    from renewed_work import WorkMemory
    from tail_new import blocked
    from hft244_pair_route_legality_v1 import crossing_owners
    roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    owners={o['key']:o for o in audit['receipt_accounting']['orders']}
    public=d.read(d.PACKAGE/'inputs/public_1977248.json.gz')
    start=public['market']['window_start_ms'];end=public['market']['window_end_ms'];cutoff=start+2*(end-start)//3
    plans={r['index']:p for r,p in zip(tr['direction_rows'],tr['plans'])}
    books={r['index']:b for r,b in zip(tr['direction_rows'],public['books'])}
    for row in tr['tail_new_rows']:
        assert row['t']==plans[row['index']]['t'] and row['start']==start and row['end']==end
        assert row['strong']=='DOWN'
        assert row['blocked']==blocked(row['t'],start,end,row['side'],'DOWN',row['route'])
        assert row['qty']*row['price']>=1-1e-8
        if row['blocked']:assert row['route']=='PASSIVE' and row['side']=='DOWN'
    for p in tr['plans']:
        if p['t']>=cutoff:assert all(o.get('side')!='DOWN' for o in p['operations'] if o['kind']=='NEW')
    first=next(i for i,(a,b) in enumerate(zip(tr['plans'],bt['plans'])) if a!=b)
    cut=tr['plans'][first]['t'];assert cut>=cutoff
    same(tr['plans'][:first],bt['plans'][:first])
    same([s for s in tr['states'] if s['t']<=cut],[s for s in bt['states'] if s['t']<=cut])
    first_three=tr['opportunity_submissions']+tr['coordination_submissions'][:2]
    same(first_three,bt['opportunity_submissions']+bt['coordination_submissions'][:2])
    bo={o['key']:o for o in oldaudit['receipt_accounting']['orders']}
    for sub in first_three:same(owners[sub['key']],bo[sub['key']])
    selected=[i for i in tr['intent'] if i['t']>tr['coordination_submissions'][1]['t']]
    assert len(selected)==len(tr['renewal_rows'])
    memory=WorkMemory();issued=[];adaptive_rows=[]
    for intent,row in zip(selected,tr['renewal_rows']):
        index=intent['index'];t=row['t'];assert intent['t']==t
        state=row['state'];book=books[index]
        same(state['inv'],intent['inv']);same(state['cost'],intent['cost'])
        for old,status in zip(tr['coordination_submissions'][:2],row['old_service_states']):
            assert (status=='TERMINAL')==(t>=owners[old['key']]['terminal_observed_t'])
        status=memory.advance(state,t,tr['coordination_episode'],row['old_service_states'],coord.detect)
        same(status,row['status']);same(book['best_ask'],row['active_ask'])
        same(book['asks'][0][1] if book['asks'] else 0.,row['visible_depth'])
        continuation=bool(issued);same(continuation,row['continuation']);prior=None
        if issued:
            prior=SimpleNamespace(state=row['previous_service_state'])
            assert (prior.state=='TERMINAL')==(t>=owners[issued[0]['key']]['terminal_observed_t'])
        ready=service.ready(status['work'],issued,prior) if continuation else True
        ops=deepcopy(row['original_operations'])
        if status['work'] is not None and len(issued)<2 and ready:
            if continuation:
                legacy=service.decide(coord.decide,state,ops,row['active_ask'],row['visible_depth'],status['work'],crossing_owners)
                asks=row['actor_asks'];same(asks[:len(book['asks'])],book['asks'])
                ops,decision,diag=depth.replan(state,ops,'UP',status['work']['anchor_floor'],asks,row['cancellable'],legacy)
                same(diag,row['adaptive'])
                adaptive_rows.append(dict(index=index,t=t,decision=decision,diagnostic=diag))
                for op in ops:
                    if op.get('reason')=='FINITE_REPAIR_PRICE_CONFLICT':
                        assert row['cancellable'][op['key']]
                        owner=next(o for o in state['owners'] if o['key']==op['key'])
                        assert owner['state'] not in ('TERMINAL','CANCEL_PENDING','UNKNOWN')
            else:
                decision=coord.decide(state,ops,row['active_ask'],row['visible_depth'],status['work'],True,crossing_owners)
            assert row['decision']['reason']!='RESOURCE_OWNER_LIMIT'
            same(decision,row['decision']);same(decision['eligible'],row['submitted'])
            if decision['eligible']:
                role='ACTIVE_RENEWED_FINITE_CONTINUATION' if continuation else 'ACTIVE_RENEWED_FINITE_REPAIR'
                op=next(o for o in plans[index]['operations'] if o.get('role')==role)
                same(op['qty'],decision['quantity']);same(op['price'],decision.get('execution_limit',row['active_ask']))
                ops.append(op);issued.append(dict(work_id=status['work']['id'],anchor=status['work']['anchor_floor'],t=t,**op))
        else:assert row['decision'] is None and not row['submitted']
        same(ops,plans[index]['operations'])
    same(issued,tr['renewal_submissions']);same(memory.events,tr['renewal_events']);same(memory.work,tr['renewal_work'])
    assert len(issued)<=2
    born={o['key']:index for index,p in plans.items() for o in p['operations'] if o['kind']=='NEW'}
    armed=tr['growth_hold_arm'];released=False;events=[dict(kind='ARM',**armed)] if armed else []
    for row,g,intent,dm in zip(tr['growth_hold_rows'],tr['addition_growth_rows'],tr['intent'],tr['demand_rows']):
        assert row['t']==intent['t']==g['t'] and row['index']==intent['index']
        ar=armed if armed and row['index']>armed['index'] else None;same(ar,row['arm'])
        same(row['confirmed_weak_payoff'],g['inv']['UP']-g['cost'])
        same(row['strong_pending_cash'],dm['state']['pending_cash']['DOWN'])
        past=[s for s in issued if born[s['key']]<row['index']]
        same([r['key'] for r in row['service_receipts']],[s['key'] for s in past])
        for receipt in row['service_receipts']:
            owner=owners[receipt['key']]
            assert (receipt['state']=='TERMINAL')==(row['t']>=owner['terminal_observed_t'])
            q=sum(f['fill_increment'] for e in n['atomic_responsibility_events'] if e['t']<=row['t'] for f in e['fill_rows'] if f['key']==receipt['key'])
            same(q,receipt['filled'])
        aggregate=service.aggregate_receipts(row['service_receipts']);same(aggregate,row['receipt'])
        expected=hold.decide(g,ar,aggregate,row['confirmed_weak_payoff'],released,row['strong_pending_cash'])
        same(expected,row['decision']);same(expected['effective_desired'],row['effective_desired'])
        if expected['released'] and not released:events.append(dict(kind='RELEASE',t=row['t'],index=row['index'],reason=expected['reason']))
        released=expected['released']
    same(events,tr['growth_hold_events'])
    # Add owner identity to the established canonical receipt-price attribution.
    code=inspect.getsource(canonical_legs).replace("dict(t=event['t'], side=fill['side']", "dict(key=fill['key'],t=event['t'], side=fill['side']")
    ns=dict(canonical_legs.__globals__);exec(compile(code,'OWNER_CANONICAL_LEGS','exec'),ns)
    legs=ns['canonical_legs'](n,tr);baseline_legs=ns['canonical_legs'](bn,bt)
    windows=[]
    for lo,hi in ((0,200),(200,240),(240,300)):
        records={}
        for label,tt,ll,oo in [('V43',bt,baseline_legs,bo),('V44',tr,legs,owners)]:
            rows=[l for l in ll if start+lo*1000<=l['t']<start+hi*1000]
            pp=[p for p in tt['plans'] if start+lo*1000<=p['t']<start+hi*1000]
            records[label]={s:dict(new=sum(o.get('side')==s for p in pp for o in p['operations'] if o['kind']=='NEW'),
                qty=sum(l['qty'] for l in rows if l['side']==s),cash=sum(l['cash'] for l in rows if l['side']==s),
                pre_cutoff_owner_qty=sum(l['qty'] for l in rows if l['side']==s and oo[l['key']]['born_t']<cutoff),
                pre_cutoff_owner_cash=sum(l['cash'] for l in rows if l['side']==s and oo[l['key']]['born_t']<cutoff)) for s in ('UP','DOWN')}
        windows.append(dict(start=lo,end=hi,records=records))
    taillegs=[l for l in legs if l['t']>=cutoff and l['side']=='DOWN']
    assert all(owners[l['key']]['born_t']<cutoff for l in taillegs)
    old_current_owners=[o for o in tr['demand_rows'] if o['t']>=cutoff][0]['state']['owners']
    result=dict(status='COMPLETE',execution='PASS',new_rule_audit='PASS',native_jobs=1,native_seconds=audit['native_elapsed_seconds'],
        frames=audit['frames'],new_orders=audit['submits'],raw_receipts=audit['raw_receipts'],active_orders=audit['active_submits'],
        prefix_plans_exact=first,first_three_active_receipts_exact=True,
        first_difference=dict(index=first,seconds=(cut-start)/1000,baseline=bt['plans'][first],candidate=tr['plans'][first]),
        tail_cutoff_seconds=(cutoff-start)/1000,screened_proposals=len(tr['tail_new_rows']),vetoed_proposals=sum(r['blocked'] for r in tr['tail_new_rows']),
        veto_count_meaning='Pre-reservation proposals, not guaranteed executable orders or inferred avoided fills.',
        windows=windows,tail_old_owner_fills=taillegs,owners_at_cutoff=old_current_owners,
        terminal=audit['terminal'],baseline_terminal=oldaudit['terminal'],
        terminal_delta={k:audit['terminal'][k]-oldaudit['terminal'][k] for k in ('up','down','cost','inventory_up','inventory_down')},
        trajectory=audit['trajectory'],baseline_trajectory=oldaudit['trajectory'],
        direction_aware=dict(V43=direction_metrics(bt['states'],start,end,'DOWN'),V44=direction_metrics(tr['states'],start,end,'DOWN')),
        legacy_metric_notice='trajectory.reversed_net_seconds and terminal.fixed_up_direction_retained are fixed-UP coordinate metrics inherited from the helper. They do not describe retention of the selected DOWN role; use direction_aware.',
        renewal_rows=len(tr['renewal_rows']),growth_hold_rows=len(tr['growth_hold_rows']),adaptive_rows=adaptive_rows,
        service_submissions=issued,service_receipts=[owners[s['key']] for s in issued],growth_events=events,work_end=tr['renewal_work'],
        all_owners_terminal=audit['all_owners_terminal'],all_pending_zero=audit['all_pending_zero'],missing_terminal_clocks=audit['terminal_timing']['missing_owner_observation_clocks'],
        model_fits=0,parameter_search=0,local_native_jobs=0,plots=0,
        limitation='Consumed market, known final Target net direction, zero fees, unchanged fixed15 versus Target observed larger tickets. This is a NEW ablation, not inferred submitted Target timing or a production policy.',
        source_manifest_sha256=d.sha(d.PACKAGE/'manifest.json'),result_sha256=d.sha(folder/'result.json'),trace_sha256=d.sha(folder/'clock_trace.json.gz'))
    d.dump('RESULT',result)
    d.dump('NEW_AUDIT',dict(status='PASS',renewal_rows=len(tr['renewal_rows']),growth_hold_rows=len(tr['growth_hold_rows']),adaptive_decisions=len(adaptive_rows),tail_screening_rows=len(tr['tail_new_rows']),prefix_plans_exact=first,first_three_active_receipts_exact=True))
    d.dump('PROGRESS',dict(status='COMPLETE',submissions=1,native_completed=1,pending_jobs=[]))
    print(json.dumps({k:result[k] for k in ('status','native_seconds','terminal','terminal_delta','windows','service_submissions')},ensure_ascii=False))


if __name__=='__main__':main()
