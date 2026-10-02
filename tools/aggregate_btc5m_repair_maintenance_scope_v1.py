"""V27 read-only panel: same Active degree, three maintenance scopes."""
import json
from prepare_btc5m_repair_maintenance_scope_v1 import ROOT,R,BASE,START,ARMS,config,read,sha,get,RET,dump_for
from aggregate_btc5m_repair_constraints_v1 import summarize
from aggregate_btc5m_reexposure_coordination_v1 import event_path
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs,close

STEM='BTC5M_MAINTENANCE_SCOPE_PANEL_V1_20260913'


def paid_groups(n,trace,cut):
    groups={}
    for leg in canonical_legs(n,trace):
        if leg['t']<cut or leg['qty']<1e-8:continue
        key=leg['side']+'_'+leg['route']+'_'+str(round(leg['cash']/leg['qty'],8))
        row=groups.setdefault(key,dict(qty=0.,cash=0.))
        for k in row:row[k]+=leg[k]
    return groups


def pending_realization(n,trace,cut):
    state=next(r['state'] for r in trace['demand_rows'] if r['t']==cut)
    final={o['key']:o for o in trace['demand_final']['all_final_carriers']}
    owners=[];totals={side:dict(pending_quantity=0.,future_filled=0.) for side in ('UP','DOWN')}
    for owner in state['owners']:
        end=final[owner['key']];filled_now=end['qty']-owner['qty']
        later=end['filled']-filled_now
        assert end['state']=='TERMINAL' and -1e-7<=later<=owner['qty']+1e-7
        later=max(0.,later)
        owners.append(dict(key=owner['key'],side=owner['side'],remaining_at_snapshot=owner['qty'],
            state_at_snapshot=owner['state'],filled_at_snapshot=filled_now,final_filled=end['filled'],future_filled=later))
        totals[owner['side']]['pending_quantity']+=owner['qty'];totals[owner['side']]['future_filled']+=later
    for side in ('UP','DOWN'):
        v=totals[side];close(v['pending_quantity'],state['pending_qty'][side])
        v['realized_fraction']=v['future_filled']/v['pending_quantity'] if v['pending_quantity'] else None
        v['total_later_inventory_increase']=n['final_inventory'][side]-state['inv'][side]
        v['future_fills_from_orders_outside_snapshot']=v['total_later_inventory_increase']-v['future_filled']
        assert v['future_fills_from_orders_outside_snapshot']>=-1e-7
        v['future_fills_from_orders_outside_snapshot']=max(0.,v['future_fills_from_orders_outside_snapshot'])
    repair=next(r for r in trace['commitment_repair_rows'] if r['t']==cut)
    return dict(t=cut,seconds=(cut-START)/1000,state=state,totals=totals,owners=owners,
        current_overlay_decision={k:repair[k] for k in ('reason','eligible','money_quantity_need','deterioration_debt','confirmed_payoff_floor')},
        interpretation='Offline eventual receipt attribution of this exact pending cohort. Future fills are never supplied to the actor. Excludes NEW proposals later in the same frame from the snapshot cohort.')


def main():
    b,bt=get(BASE);pairs=dict(role_scoped=(b,bt));cells=dict(role_scoped=summarize(b,bt));audits={};elapsed=0.
    assert read(R/'BTC5M_REEXPOSURE_RESTORE_V1_20260913_RESULT.json')['verification']=='PASS'
    cells['role_scoped']['reused_existing_result']=True
    for arm in ARMS:
        c=config(arm);a=read(R/(c['STEM']+'_RESULT.json'));m=read(c['PACKAGE']/'manifest.json')
        assert a['verification']=='PASS' and a['manifest_sha256']==sha(c['PACKAGE']/'manifest.json')
        assert all(sha(c['PACKAGE']/name)==h for name,h in m['files'].items())
        assert read(R/(c['STEM']+'_SUBMIT.json'))['accepted'] and read(R/(c['STEM']+'_POSTCHECK.json'))['status']=='PASS'
        status=read(R/(c['STEM']+'_COLLECT.json'))['status'];assert status['state']=='succeeded'
        elapsed+=status['elapsed_seconds'];n,t=get(RET/c['JOB']);pairs[arm]=(n,t);audits[arm]=a
        changes=a['changed_cancel_decisions']
        cells[arm]=dict(summarize(n,t),native_seconds=status['elapsed_seconds'],terminal_delta=a['terminal_delta'],
            changed_cancel_events=len(changes),changed_cancel_unique_owners=len({r['key'] for r in changes}),
            scope_rows_checked=a['maintenance_scope_rows_checked'],new_cancel_events=sum(r['cancelled'] for r in changes),
            prevented_cancel_events=sum(not r['cancelled'] for r in changes),flow=a['flow_diagnosis'],
            manifest_sha256=a['manifest_sha256'],result_sha256=sha(RET/c['JOB']/'result.json'),trace_sha256=sha(RET/c['JOB']/'clock_trace.json.gz'))
    cut=read(R/(config('raw')['STEM']+'_OBSERVATION.json'))['first']['t']
    groups={arm:paid_groups(n,t,cut) for arm,(n,t) in pairs.items()};deltas={};witnesses={}
    base_owners={r['key']:r for r in bt['demand_final']['all_final_carriers']}
    for arm,a in audits.items():
        deltas[arm]={key:{k:groups[arm].get(key,{}).get(k,0.)-groups['role_scoped'].get(key,{}).get(k,0.) for k in ('qty','cash')}
            for key in sorted(groups[arm].keys() | groups['role_scoped'].keys())}
        cash=sum(v['cash'] for v in deltas[arm].values());close(cash,a['terminal_delta']['cost'])
        for side in ('UP','DOWN'):
            qty=sum(v['qty'] for k,v in deltas[arm].items() if k.startswith(side+'_'))
            close(qty-cash,a['terminal_delta'][side.lower()])
        trace=pairs[arm][1];owners={r['key']:r for r in a['candidate']['receipts']['orders']};entries=[]
        for event in a['changed_cancel_decisions']:
            if event['t']!=cut:continue
            key=event['key'];assert key in base_owners
            base_cancel=[r['t'] for r in bt['native_actions'] if r.get('key')==key and r['kind']=='CANCEL']
            entries.append(dict(decision=event,baseline_owner=base_owners[key],candidate_owner=owners[key],
                baseline_cancel_times=base_cancel,filled_delta=owners[key]['filled']-base_owners[key]['filled']))
        witnesses[arm]=entries
    gate=read(R/(config('raw')['STEM']+'_OBSERVATION.json'))['gate_terminal_t']
    paths={arm:event_path(t,gate) for arm,(_,t) in pairs.items()}
    # Conditional pending scenario is reported separately from actual filled inventory.
    pending={}
    for arm,(_,tr) in pairs.items():
        rows=[]
        for d in tr['demand_rows']:
            if d['t']<gate:continue
            s=d['state'];q=s['pending_qty'];cash=s['pending_cash'];net=s['inv']['UP']-s['inv']['DOWN']
            rows.append(dict(t=d['t'],seconds=(d['t']-START)/1000,confirmed_up_net=net,pending_up=q['UP'],pending_down=q['DOWN'],
                confirmed_payoff=s['payoff'],net_if_only_pending_down_fill=net-q['DOWN'],
                payoff_if_only_pending_down_fill=dict(UP=s['payoff']['UP']-cash['DOWN'],DOWN=s['payoff']['DOWN']+q['DOWN']-cash['DOWN'])))
        first_negative=next((r for r in rows if r['confirmed_up_net']<0),None)
        pending[arm]=dict(first_confirmed_net_reversal_after_gate=first_negative,
            most_down_biased_pending_only_scenario=min(rows,key=lambda r:r['net_if_only_pending_down_fill']),
            interpretation='Current pending DOWN fills and no pending UP fills is a conditional stress scenario, not an observed loss or a cash gate.')
    pivot=pending['legal']['first_confirmed_net_reversal_after_gate']['t']
    cohorts={arm:pending_realization(n,t,pivot) for arm,(n,t) in pairs.items()}
    path_out=dict(status='PASS',new_native_jobs=0,first_maintenance_seconds=(cut-START)/1000,paid_groups=groups,
        paid_group_deltas=deltas,first_frame_owner_witnesses=witnesses,paths=paths,pending_scenarios=pending,
        aligned_pending_realization=cohorts)
    dump_for('BTC5M_MAINTENANCE_SCOPE_PATH_V1_20260913','RESULT',path_out)
    out=dict(status='COMPLETE',verification='PASS',new_native_jobs=2,reused_native_controls=1,total_new_native_seconds=elapsed,
        local_native_jobs=0,model_fits=0,parameter_search=0,runtime_eligible=False,both_active_receipts_unchanged=True,
        first_maintenance_difference_seconds=(cut-START)/1000,cells=cells,paid_group_deltas=deltas,
        pending_realization_at_legal_reversal={arm:dict(seconds=(pivot-START)/1000,totals=c['totals']) for arm,c in cohorts.items()},
        pending_scenarios=pending,diagnostics='BTC5M_MAINTENANCE_SCOPE_PATH_V1_20260913_RESULT.json',
        limitations='One consumed oracle-UP market. Isolates post-restore maintenance scope; no optimal repair degree, recurrent controller or held-out Target validation.')
    dump_for(STEM,'RESULT',out)
    progress=dict(status='COMPLETE',new_native_jobs=2,submit_count=2,resubmit_count=0,host_native_jobs=0,model_fits=0,
        parameter_search=0,elapsed_native_seconds=elapsed,next_native_dispatched=False,jobs={arm:config(arm)['JOB'] for arm in ARMS},
        selection='Mechanism contrasts retained; no Target or runtime policy promotion. Next research must coordinate confirmed/pending repair and intended direction.')
    (R/'MAINTENANCE_SCOPE_PROGRESS_20260913.json').write_text(json.dumps(progress,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',seconds=elapsed,table={arm:dict(up=c['terminal']['up'],down=c['terminal']['down'],
        up_net=c['terminal']['up_net'],both_positive=c['trajectory']['both_positive_seconds'],core=c['core_similarity']) for arm,c in cells.items()},
        paid_deltas=deltas,first_reversal={arm:v['first_confirmed_net_reversal_after_gate'] for arm,v in pending.items()},
        transitions={arm:[dict(seconds=e['seconds'],up=e['after']['up'],down=e['after']['down']) for e in p['positive_transitions']] for arm,p in paths.items()})))


if __name__=='__main__':main()
