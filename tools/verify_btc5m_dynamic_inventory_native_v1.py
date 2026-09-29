"""V48 independent accounting, causal role, physical work, and mechanism audit."""
import collections
from copy import deepcopy
import gzip
import hashlib
import math
import sys
import btc5m_dynamic_inventory_native_v1 as d
from verify_btc5m_transfer_components_v1 import same
from verify_btc5m_active_repair_opportunity_v1 import receipt_auditor, canonical_legs
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary
from hft244_pair_route_legality_v1 import crossing_owners


def bank_audit(tr, n, context):
    roles=sys.modules['roles_runtime'].roles
    frames={x['index']:x for x in tr['bridge_frames']}
    intents={x['index']:x for x in tr['intent']}
    counts=collections.Counter()
    for side,bank in tr['banks'].items():
        roles.configure('KNOWN_FINAL_DIRECTION',side);weak=roles.weak
        growth=context['addition_growth'].AdditionGrowth(side)
        demand=context['demand'];commit=context['commitment_repair'];coord=context['coordination'];scope=context['maintenance_scope'];opp=context['opportunity']
        released=False
        assert len(bank['addition_growth_rows'])==len(bank['growth_hold_rows'])==len(bank['demand_rows'])
        for g,h,row in zip(bank['addition_growth_rows'],bank['growth_hold_rows'],bank['demand_rows']):
            t=row['t'];frame=frames[h['index']];state=frame['state'];intent=intents[h['index']]
            assert frame['selected']==side and t==g['t']==h['t']
            same(state,row['state']);same(state['inv'],g['inv']);same(state['cost'],g['cost'])
            expected=growth.update(t,g['inv'],g['cost'],g['original_desired'])
            same(expected,g['effective_desired']);same(growth.anchor,g['anchor'])
            same(expected,h['input_desired'])
            same(h['strong_pending_cash'],state['pending_cash'][side]);same(h['confirmed_weak_payoff'],state['payoff'][weak])
            test=context['growth_hold'].decide(g,h['arm'],h['receipt'],h['confirmed_weak_payoff'],released,h['strong_pending_cash'])
            same(test,h['decision']);released=test['released'];same(test['effective_desired'],row['original_desired'])
            same(row['effective_desired'],intent['desired'])
            cap=demand.economic_capacity(state,row['original_desired'],row['eligibility']['price'],15.,.01)
            for k,v in cap.items():same(v,row['eligibility'][k])
            same(row['effective_desired'][side],row['original_desired'][side]);counts['growth_hold_demand']+=1
        for work in bank['demand_final']['works']:
            assert work['status']!='ACTIVE' and work['ended_t'] is not None
            goal=demand.old.FiniteGoal(work['initial_state'],15.)
            same(goal.target,work['target'])
            for f in tr['bridge_frames']:
                if work['born_t']<=f['t']<=work['ended_t']:
                    progress=goal.update(f['state'],f['t'],f['end'])
                    if f['t']<work['ended_t']:assert progress['status']=='ACTIVE'
            same(goal.status,work['status']);same(goal.stopped_t,work['ended_t']);counts['physical_finite_work']+=1
        for row in bank['opportunity_rows']:
            expected=opp.decide(row['state'],row['paid'],row['passive_price'],row['active_ask'],row['visible_depth'],row['original_operations'],.01,0.,crossing_owners)
            for k,v in expected.items():same(v,row[k])
            counts['opportunity']+=1
        for row in bank['commitment_repair_rows']:
            expected=commit.decide(row['state'],row['original_operations'],row['quote']['raw_price'],row['ask'],row['active_confirmed'],row['outstanding'],crossing_owners)
            for k,v in expected.items():same(v,row[k])
            counts['commitment']+=1
        previous=None;seen=False;episode=None
        for row in bank['coordination_rows']:
            state=row['state'];confirmed=row['active_confirmed']
            if episode is None:episode=coord.detect(previous,state,confirmed,seen,row['t'])
            if confirmed and previous and state['inv'][weak]>previous['inv'][weak]+1e-8:seen=True
            previous={k:state[k] for k in ('inv','cost','payoff')}
            expected=coord.decide(state,row['original_operations'],row['active_ask'],row['visible_depth'],episode,confirmed,crossing_owners,continuation=row.get('continuation',False))
            for k,v in expected.items():same(v,row[k])
            counts['coordination']+=1
        for row in bank['commitment_maintenance_rows']:
            q=commit.legal_quote(row['quote']['raw_price'],row['quote']['ask']);same(q,row['quote'])
            same(row['new_stale'],abs(row['limit']-q['price'])>row['threshold']+1e-9);counts['maintenance']+=1
        for row in bank['maintenance_scope_rows']:
            expected=scope.decide(row['quote']['raw_price'],row['quote']['ask'],row['limit'],row['threshold'],row['is_extra'],commit.legal_quote)
            for k,v in expected.items():same(v,row[k])
            counts['maintenance_scope']+=1
        for name in ('opportunity_submissions','coordination_submissions','commitment_repair_submissions'):
            for o in bank[name]:
                assert o['side']==weak and o['parent_id']==roles.pid(weak)
                assert tr['birth_provenance'][o['key']]['bank']==side
        # Stateful renewal replay uses the unchanged frozen implementation on saved rows.
        from renewed_work import WorkMemory
        import pending_service as service
        import depth_slippage as depth
        memory=WorkMemory();issued=[]
        plan_at={f['index']:p for f,p in zip(tr['bridge_frames'],tr['plans'])}
        renewal_cursor=-1
        for row in bank['renewal_rows']:
            # Multiple source frames may share a millisecond; preserve occurrence order.
            matches=[h for h in bank['growth_hold_rows'] if h['index']>renewal_cursor and h['t']==row['t']]
            assert matches
            renewal_cursor=matches[0]['index'];plan=plan_at[renewal_cursor]
            status=memory.advance(row['state'],row['t'],episode,row['old_service_states'],coord.detect)
            same(status,row['status']);same(bool(issued),row['continuation'])
            ops=deepcopy(row['original_operations'])
            from types import SimpleNamespace
            prior=SimpleNamespace(state=row['previous_service_state']) if issued else None
            ready=service.ready(status['work'],issued,prior) if issued else True
            if status['work'] is not None and len(issued)<2 and ready:
                if issued:
                    legacy=service.decide(coord.decide,row['state'],ops,row['active_ask'],row['visible_depth'],status['work'],crossing_owners)
                    ops,decision,diag=depth.replan(row['state'],ops,weak,status['work']['anchor_floor'],row['actor_asks'],row['cancellable'],legacy)
                    same(diag,row['adaptive'])
                else:decision=coord.decide(row['state'],ops,row['active_ask'],row['visible_depth'],status['work'],True,crossing_owners)
                same(decision,row['decision']);same(decision['eligible'],row['submitted'])
                if decision['eligible']:
                    role='ACTIVE_RENEWED_FINITE_CONTINUATION' if issued else 'ACTIVE_RENEWED_FINITE_REPAIR'
                    op=next(o for o in plan['operations'] if o.get('role')==role)
                    same(op['qty'],decision['quantity']);same(op['price'],decision.get('execution_limit',row['active_ask']))
                    ops.append(op);issued.append(dict(work_id=status['work']['id'],anchor=status['work']['anchor_floor'],t=row['t'],**op))
            else:assert row['decision'] is None and not row['submitted']
            same(ops,plan['operations']);counts['renewal']+=1
        same(issued,bank['renewal_submissions']);same(memory.events,bank['renewal_events']);same(memory.work,bank['renewal_work'])
    return dict(counts)


def audit(index):
    job=d.jobs()[index];w=d.worker(job);d.PACKAGE=w.PACKAGE;folder=d.R/'lan_worker_returns'/job['job_id'];n=d.read(folder/'result.json')
    if n['status']!='COMPLETE':
        out=dict(execution_status='FAIL',economic_status='UNKNOWN',job_id=job['job_id'],error=n.get('error'),failure_capture=n.get('failure_capture'))
        w.save(job,'AUDIT',out);return out
    tr=d.read(folder/'clock_trace.json.gz');m=d.read(d.PACKAGE/'manifest.json')
    assert all(d.sha(d.PACKAGE/k)==h for k,h in m['files'].items())
    assert n['worker'].upper()=='DESKTOP-JIERAGF' and n['safety_gate']['pass'] and n['execution_accounting_valid'] and n['unresolved_owners']==0
    assert n['theta']==m['theta'] and n['fixed_train_share_unit']==m['fixed_qref'] and n['source_frames']==1487.
    assert n['capital_cap'] is None and not n['cash_budget_enabled'] and n['runtime_eligible'] is False
    assert not n['target_runtime_access'] and n['target_profile'] is None and n['path_mse'] is None
    assert n['target_direction_input']==(job['mode']!='NO_DIRECTION')
    clock=n['clock_smoke'];assert clock['manifest_sha256']==d.sha(d.PACKAGE/'manifest.json') and clock['direction_rule']==job['rule']
    assert hashlib.sha256(gzip.decompress((folder/'clock_trace.json.gz').read_bytes())).hexdigest()==clock['trace_payload_sha256']
    context=d.compile_policy(d.PACKAGE,job['mode'],job['rule'],True);src=context['source']
    remote='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+d.PACKAGE.name
    for a,b in [(str(d.PACKAGE).replace('\\','\\\\'),remote.replace('\\','\\\\')),(str(d.PACKAGE),remote),(d.PACKAGE.as_posix(),remote.replace('\\','/'))]:src=src.replace(a,b)
    assert hashlib.sha256(src.encode()).hexdigest()==clock['transformed_source_sha256']
    inp=d.read(d.PACKAGE/f'inputs/public_{job["market"]}.json.gz');start=inp['market']['window_start_ms'];end=inp['market']['window_end_ms']
    times=[b['received_ms'] for b in inp['books']]
    assert [p['t'] for p in tr['plans'][:len(times)]]==times and clock['actual_replay_frames']==len(times)
    assert all(p['t']>=max(end,times[-1]) and all(o['kind']!='NEW' for o in p['operations']) for p in tr['plans'][len(times):])
    receipt=receipt_auditor()(n,tr);legs=canonical_legs(n,tr)
    assert d.read(w.artifact(job,'POSTCHECK'))['status']=='PASS'
    mechanisms={};switches=[];grace=[]
    if job['rule']=='LEGACY':
        base=d.R/'lan_worker_returns/fixed15-core-loop-1977248-tail-acquisition-ablation-20260913-v1'
        old=d.read(base/'result.json');bt=d.read(base/'clock_trace.json.gz')
        parity={k:tr[k]==v for k,v in bt.items()}
        parity.update({'result.'+k:n[k]==old[k] for k in ('final_inventory','final_cost','theta','submits','active_native_submits','passive_native_submits','atomic_responsibility_events')})
        d.dump('PARITY',dict(status='PASS' if all(parity.values()) else 'FAIL',comparisons=parity))
        assert all(parity.values()),[k for k,v in parity.items() if not v]
    else:
        roles=sys.modules['roles_runtime'].roles
        state_at=collections.defaultdict(list)
        for x in tr['states']:state_at[x['t']].append(x)
        births={};seen={};previous=None;guard=None
        events={e['t']:e['fill_rows'] for e in n['atomic_responsibility_events']}
        assert len(tr['bridge_frames'])==len(tr['plans'])==len(tr['direction_decisions'])
        for idx,(frame,plan,row) in enumerate(zip(tr['bridge_frames'],tr['plans'],tr['direction_decisions'])):
            assert frame['t']==plan['t']==row['t'] and frame['index']==row['index']
            state=frame['state'];inv=state['inv']
            assert any(x['inv']==inv and abs(x['cost']-state['cost'])<1e-7 for x in state_at[frame['t']])
            expected_increments=[dict(key=x['key'],side=x['side'],qty=x['fill_increment'],purpose=births[x['key']]['purpose']) for x in events.get(frame['t'],[]) if x['fill_increment']>1e-8]
            same(sorted(row['increments'],key=lambda x:x['key']),sorted(expected_increments,key=lambda x:x['key']))
            candidate='UP' if inv['UP']>inv['DOWN']+1e-8 else 'DOWN' if inv['DOWN']>inv['UP']+1e-8 else previous
            if job['rule']=='REPAIR_GRACE' and previous is not None and candidate!=previous:
                fills=[x for x in expected_increments if x['side']==candidate]
                independent=any(x['purpose']!='REPAIR' for x in fills)
                hold=abs(inv['UP']-inv['DOWN'])<=15.+1e-8 and not independent and (guard==candidate or bool(fills))
                if hold:guard=candidate;candidate=previous
                else:guard=None
            else:guard=None
            assert candidate==row['side']==frame['selected'] and guard==row['guard'] and previous==row['previous']
            if candidate!=previous and previous is not None:switches.append(dict(row,seconds=(row['t']-start)/1000))
            if guard:grace.append(row)
            previous=candidate
            owners=[dict(key=o['key'],side=o['side'],price=o['limit']) for o in state['owners']]
            for side in ('UP','DOWN'):
                subset=[o for o in state['owners'] if o['side']==side]
                same(sum(o['qty'] for o in subset),state['pending_qty'][side]);same(sum(o['qty']*o['limit'] for o in subset),state['pending_cash'][side])
            for op in plan['operations']:
                if op['kind']=='CANCEL':
                    assert frame['cancellable'].get(op['key'])
                    owner=next(o for o in state['owners'] if o['key']==op['key'])
                    assert owner['state'] not in ('TERMINAL','CANCEL_PENDING','UNKNOWN')
                    continue
                assert op['kind']=='NEW' and op['key'] not in births and start<=plan['t']<end
                assert op['parent_id']==(1 if op['side']=='UP' else 2) and op['price']*op['qty']>=1-1e-8
                book=inp['books'][idx];assert book['source_ms']<=plan['t'] and book['received_ms']==plan['t']
                ask=book['best_ask'] if op['side']=='UP' else round(1-book['best_bid'],10)
                if op['route']=='PASSIVE':assert op['qty']==15. and op['price']<ask-1e-10
                else:assert op['price']>=ask-1e-8
                assert not crossing_owners(op['side'],op['price'],owners)
                owners.append(dict(key=op['key'],side=op['side'],price=op['price']))
                if candidate and op['route']=='PASSIVE' and 3*(plan['t']-start)>=2*(end-start):assert op['side']!=candidate
                b=tr['birth_provenance'][op['key']]
                for k,v in op.items():same(v,b[k])
                assert b['bank']==candidate and b['purpose']==('NEUTRAL' if candidate is None else 'ADD' if candidate==op['side'] else 'REPAIR')
                births[op['key']]=b
        assert set(births)==set(tr['birth_provenance'])
        active=[x for x in births.values() if x['route']=='ACTIVE']
        assert len(active)==n['active_native_submits']<=5
        assert sum(x['role']=='ACTIVE_OPPORTUNITY_REPAIR' for x in active)<=1
        assert sum(x['role']=='ACTIVE_CONFIRMED_REEXPOSURE_REPAIR' for x in active)<=2
        assert sum(x['role'].startswith('ACTIVE_RENEWED_') for x in active)<=2
        for row in tr['physical_maintenance_rows']:
            b=births[row['key']];assert b['purpose']=='REPAIR' and b['bank']==row['birth_bank'] and b['side']==row['physical'] and b['side']!=row['birth_bank']
        for row in tr['opening_rows']:
            ops=row['operations'];news=[o for o in ops if o['kind']=='NEW']
            if news:assert len(news)==2 and {o['side'] for o in news}=={'UP','DOWN'} and not row['live_owner_keys']
            else:assert all(o['kind']=='CANCEL' for o in ops)
        held=None
        for row in tr['intent']:
            side=next(x['side'] for x in tr['direction_decisions'] if x['index']==row['index']);weak='DOWN' if side=='UP' else 'UP'
            inv=row['inv'];legacy=math.tanh(n['theta'][3]*(inv[side]-inv[weak])/(1+sum(inv.values())))
            same(legacy,row['legacy_exposure'])
            if held is None:held=abs(legacy)
            same(held,row['applied_exposure'])
        mechanisms=bank_audit(tr,n,context)
    path=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),tr['states'],start,end);path.pop('changed_rows',None)
    terminal=geometry(n['final_inventory'],n['final_cost'])
    out=dict(execution_status='PASS',job_id=job['job_id'],market=job['market'],rule=job['rule'],terminal=terminal,trajectory=path,
        inventory=n['final_inventory'],cost=n['final_cost'],submits=n['submits'],active=n['active_native_submits'],
        frames=len(times),closure_plans=len(tr['plans'])-len(times),selected=clock['selected_direction'],switches=switches,grace_frames=len(grace),
        mechanism_checks=mechanisms,receipt_accounting=receipt,all_pending_zero=True,all_owners_terminal=True,
        terminal_timing=dict(missing_exact_clocks=[o['key'] for o in receipt['orders'] if o['terminal_observed_t'] is None],drain_clock_kind='NOT_PERSISTED_BY_FROZEN_RUNNER'),
        native_elapsed_seconds=d.read(w.artifact(job,'COLLECT'))['status']['elapsed_seconds'],result_sha256=d.sha(folder/'result.json'),trace_sha256=d.sha(folder/'clock_trace.json.gz'))
    w.save(job,'AUDIT',out)
    return {k:out[k] for k in ('execution_status','market','rule','terminal','cost','active','selected','grace_frames','mechanism_checks','native_elapsed_seconds')}|dict(switches=len(switches))
