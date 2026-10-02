"""Collected native-only structural transfer audit. No fitting or native execution."""
import argparse
import collections
import math
import sys
from prepare_btc5m_transfer_structural_v1 import *
from run_btc5m_transfer_structural_worker_v1 import jobs, artifact, save
from verify_btc5m_transfer_components_v1 import same
from verify_btc5m_active_repair_opportunity_v1 import receipt_auditor, canonical_legs
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary
from hft244_pair_route_legality_v1 import crossing_owners


def inspect_job(index):
    job=jobs()[index];folder=R/'lan_worker_returns'/job['job_id']
    n=read(folder/'result.json')
    if n['status']!='COMPLETE':
        out=dict(execution_status='FAIL',error=n.get('error'),native_status=n['status'],
                 economic_status='UNKNOWN',job_id=job['job_id'],result_sha256=sha(folder/'result.json'))
        save(job,'AUDIT',out);return out
    tr=read(folder/'clock_trace.json.gz');m=read(PACKAGE/'manifest.json')
    assert all(sha(PACKAGE/k)==v for k,v in m['files'].items())
    assert n['safety_gate']['pass'] and n['execution_accounting_valid'] and n['unresolved_owners']==0
    assert n['theta']==m['theta'] and n['fixed_train_share_unit']==m['fixed_qref']
    assert n['source_frames']==1487. and n['capital_cap'] is None and n['runtime_eligible'] is False
    assert n['target_profile'] is None and n['path_mse'] is None and not n['target_runtime_access']
    assert n['target_direction_input']==(job['mode']!='NO_DIRECTION')
    clock=n['clock_smoke'];assert clock['manifest_sha256']==sha(PACKAGE/'manifest.json')
    raw=gzip.decompress((folder/'clock_trace.json.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==clock['trace_payload_sha256']
    src=compiled(PACKAGE,job['mode']);remote='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+PACKAGE.name
    for a,b in [(str(PACKAGE).replace('\\','\\\\'),remote.replace('\\','\\\\')),(str(PACKAGE),remote),(PACKAGE.as_posix(),remote.replace('\\','/'))]:src=src.replace(a,b)
    assert hashlib.sha256(src.encode()).hexdigest()==clock['transformed_source_sha256'],'generated source hash'
    inp=read(PACKAGE/f'inputs/public_{job["market"]}.json.gz');start=inp['market']['window_start_ms'];end=inp['market']['window_end_ms']
    source_times=[b['received_ms'] for b in inp['books']]
    assert [x['t'] for x in tr['plans'][:len(source_times)]]==source_times
    closure_plans=tr['plans'][len(source_times):]
    assert all(p['t']>=max(end,source_times[-1]) and not any(o['kind']=='NEW' for o in p['operations']) for p in closure_plans)
    assert clock['actual_replay_frames']==len(inp['books'])
    receipt=receipt_auditor()(n,tr)
    missing_terminal_clock=[o['key'] for o in receipt['orders'] if o['terminal_observed_t'] is None]
    assert read(artifact(job,'POSTCHECK'))['status']=='PASS'
    direction={x['index']:x for x in tr['direction_rows']}
    assert len(direction)==len(tr['plans']) and [r['t'] for r in tr['direction_rows']]==[p['t'] for p in tr['plans']]
    side=None if job['mode']=='NO_DIRECTION' else job['mode'].split('_')[1]
    for row in tr['direction_rows']:
        inv=row['inv']
        if side is None and abs(inv['UP']-inv['DOWN'])>1e-8:side='UP' if inv['UP']>inv['DOWN'] else 'DOWN'
        assert row['side']==side
    assert clock['selected_direction']==side
    sys.path.insert(0,str(PACKAGE));import roles_runtime
    roles=roles_runtime.roles
    demand=load('audit_role_demand',PACKAGE/'demand_gate.py')
    growth=load('audit_role_growth',PACKAGE/'addition_growth.py').AdditionGrowth()
    opportunity=load('audit_role_opportunity',PACKAGE/'active_opportunity.py')
    states=collections.defaultdict(list)
    for state in tr['states']:states[state['t']].append(state)
    assert len(tr['intent'])==len(tr['demand_rows'])==len(tr['addition_growth_rows'])==len(tr['observations'])
    demand_rows={i['index']:d for i,d in zip(tr['intent'],tr['demand_rows'])}
    held=None
    for row,d,grow,obs in zip(tr['intent'],tr['demand_rows'],tr['addition_growth_rows'],tr['observations']):
        t=row['t'];assert t==d['t']==grow['t']==obs['t']
        selected=direction[row['index']]['side'];strong=selected or 'UP';weak='DOWN' if strong=='UP' else 'UP'
        roles.configure('KNOWN_FINAL_DIRECTION',strong)
        inv=row['inv'];same(inv,direction[row['index']]['inv']);same(inv,d['state']['inv']);same(row['cost'],d['state']['cost'])
        assert any(s['inv']==inv and abs(s['cost']-row['cost'])<1e-7 for s in states[t])
        legacy=math.tanh(n['theta'][3]*(inv[strong]-inv[weak])/(1+sum(inv.values())))
        same(legacy,row['legacy_exposure'])
        if held is None and abs(inv['UP']-inv['DOWN'])>1e-8:held=abs(legacy)
        amplitude=(held if held is not None else abs(legacy)) if selected else 0.
        same(row['applied_exposure'],amplitude)
        raw=grow['original_desired'];gross=obs['gross']
        same(raw[strong],gross*(1+amplitude)/2);same(raw[weak],gross*(1-amplitude)/2)
        if growth.anchor is None:growth.strong=strong
        effective=growth.update(t,inv,row['cost'],raw)
        same(effective,grow['effective_desired']);same(growth.anchor,grow['anchor'])
        same(effective,obs['desired'])
        same(d['original_desired'],effective);same(d['effective_desired'],row['desired'])
        same(d['effective_desired'][strong],effective[strong])
        cap=demand.economic_capacity(d['state'],d['original_desired'],d['eligibility']['price'],15.,.01)
        for k,v in cap.items():same(v,d['eligibility'][k])
        for physical in ('UP','DOWN'):
            owners=[o for o in d['state']['owners'] if o['side']==physical]
            same(sum(o['qty'] for o in owners),d['state']['pending_qty'][physical])
            same(sum(o['qty']*o['limit'] for o in owners),d['state']['pending_cash'][physical])
    new=[]
    for plan_index,plan in enumerate(tr['plans']):
        births=[x for x in plan['operations'] if x['kind']=='NEW']
        if not births:continue
        source_index=tr['direction_rows'][plan_index]['index']
        s=demand_rows[source_index]['state'];owners=[dict(key=o['key'],side=o['side'],price=o['limit']) for o in s['owners']]
        book=inp['books'][plan_index];assert book['source_ms']<=plan['t'] and book['received_ms']==plan['t']
        for op in births:
            assert start<=plan['t']<end
            assert op['parent_id']==(1 if op['side']=='UP' else 2)
            assert op['key'].startswith(op['side']+'_')
            ask=book['best_ask'] if op['side']=='UP' else round(1-book['best_bid'],10)
            assert op['price']*op['qty']>=1-1e-8
            if op['route']=='PASSIVE':assert op['qty']==15. and op['price']<ask-1e-10
            else:same(op['price'],ask)
            assert not crossing_owners(op['side'],op['price'],owners)
            owners.append(dict(key=op['key'],side=op['side'],price=op['price']))
            new.append(dict(t=plan['t'],**op))
    for row in tr['opportunity_rows']:
        # Side is latched; before its birth these rows have no directional inventory.
        selected=next(r['side'] for r in tr['direction_rows'] if r['t']==row['t']);roles.configure('KNOWN_FINAL_DIRECTION',selected or 'UP')
        test=opportunity.decide(row['state'],row['paid'],row['passive_price'],row['active_ask'],row['visible_depth'],row['original_operations'],.01,0.,crossing_owners)
        for k,v in test.items():same(v,row[k])
    active=[x for x in new if x['route']=='ACTIVE']
    assert len(active)==n['active_native_submits']==len(tr['opportunity_submissions'])+len(tr['coordination_submissions'])<=2
    for work in tr['demand_final']['works']:
        assert work['status']!='ACTIVE' and work['ended_t'] is not None
        born=next(r for r in tr['demand_rows'] if r['t']==work['born_t'] and r['work_id']==work['id'])
        strong=next(r['side'] for r in tr['direction_rows'] if r['t']==work['born_t']);weak='DOWN' if strong=='UP' else 'UP'
        same(work['target'],born['state']['inv'][weak]+born['state']['pending_qty'][weak]+15.)
    legs=canonical_legs(n,tr)
    for physical in ('UP','DOWN'):same(sum(x['qty'] for x in legs if x['side']==physical),n['final_inventory'][physical])
    same(sum(x['cash'] for x in legs),n['final_cost'])
    path=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),tr['states'],start,end)
    path.pop('changed_rows',None)
    weak='DOWN' if side=='UP' else 'UP' if side=='DOWN' else None
    batches=collections.defaultdict(list)
    for leg in legs:batches[leg['t']].append(leg)
    repair_since_add=False;cycles=0;simultaneous=0;add_batches=0;repair_batches=0
    for t,group in sorted(batches.items()):
        adds=any(x['side']==side for x in group);repairs=any(x['side']==weak for x in group)
        add_batches+=adds;repair_batches+=repairs;simultaneous+=adds and repairs
        if adds and repair_since_add:cycles+=1;repair_since_add=False
        if repairs:repair_since_add=True
    worst=min(tr['states'],key=lambda x:min(x['inv'].values())-x['cost'])
    after=[x for x in legs if x['t']>worst['t']]
    weak_repair=sum(x['qty']-x['cash'] for x in after if x['side']==weak)
    strong_drag=sum(x['cash'] for x in after if x['side']==side)
    terminal=geometry(n['final_inventory'],n['final_cost'])
    out=dict(execution_status='PASS',job_id=job['job_id'],market_id=job['market'],arm=job['arm'],
        native_elapsed_seconds=read(artifact(job,'COLLECT'))['status']['elapsed_seconds'],
        selected_direction=side,direction_birth=clock['first_direction_birth'],frames=len(inp['books']),
        frame_coverage=dict(source_prefix_exact=True,source_frames=len(source_times),closure_plans=len(closure_plans),
            closure_times=[p['t'] for p in closure_plans],closure_has_no_new=True,
            note='Frozen drain_queued_responses may invoke closure-only plans after tape EOF; these are not new book rows.'),
        terminal_timing=dict(missing_owner_observation_clocks=missing_terminal_clock,
            canonical_final_state='All TERMINAL after native response drain',
            drain_clock_kind='NOT_PERSISTED_BY_FROZEN_RUNNER',
            note='Do not substitute last state/EOF time for missing exact owner terminal time; final canonical state and cash reconciliation are separately verified.'),
        terminal=terminal,trajectory=path,submits=len(new),active_submits=len(active),
        raw_receipts=receipt['count'],economic_filled_orders=n['economic_filled_orders'],all_owners_terminal=True,
        all_pending_zero=True,receipt_accounting=receipt,
        flow={s:{route:dict(qty=sum(x['qty'] for x in legs if x['side']==s and x['route']==route),
                           cash=sum(x['cash'] for x in legs if x['side']==s and x['route']==route)) for route in ('MAKER','TAKER')} for s in ('UP','DOWN')},
        structural_activity=dict(both_sides_filled=all(n['final_inventory'][s]>1e-8 for s in ('UP','DOWN')),
            addition_fill_batches=add_batches,repair_fill_batches=repair_batches,repair_then_add_transitions=cycles,
            simultaneous_fill_batches=simultaneous,finite_work_count=len(tr['demand_final']['works']),
            work_statuses=dict(collections.Counter(w['status'] for w in tr['demand_final']['works'])),
            note='Observed fill-batch transitions are descriptive; not independent learned cycles.'),
        economic_flags=dict(both_terminal_branches_negative=max(terminal['up'],terminal['down'])<0,
            held_direction_reversed=(n['final_inventory'][side]<n['final_inventory'][weak]) if side else None),
        post_worst=dict(seconds=(worst['t']-start)/1000,worst_floor=min(worst['inv'].values())-worst['cost'],
            weak_repair_contribution=weak_repair,strong_addition_drag=strong_drag,
            note='Post-hoc worst-state response, not a policy trigger.'),
        source_manifest_sha256=sha(PACKAGE/'manifest.json'),result_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'))
    save(job,'AUDIT',out)
    if index==0:
        base=R/'lan_worker_returns/fixed15-core-loop-2026085-addition-growth-20260913-v1'
        old=read(base/'result.json');bt=read(base/'clock_trace.json.gz')
        parity={k:tr[k]==bt[k] for k in ('states','plans','native_actions','demand_owner_rows','demand_final')}
        parity.update({k:n[k]==old[k] for k in ('final_inventory','final_cost','theta','submits','active_native_submits','passive_native_submits','atomic_responsibility_events')})
        pp=dict(status='PASS' if all(parity.values()) else 'FAIL',comparisons=parity,
            purpose='One separate original-UP port parity, not a repeated strategy test or cross-market result')
        dump('PARITY',pp);assert pp['status']=='PASS',pp
    return {k:out[k] for k in ('execution_status','market_id','arm','native_elapsed_seconds','selected_direction','terminal','structural_activity','economic_flags')}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--index',type=int,required=True);a=p.parse_args()
    print(json.dumps(inspect_job(a.index)))
