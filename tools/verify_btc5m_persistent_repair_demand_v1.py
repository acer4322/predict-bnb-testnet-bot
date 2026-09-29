"""Verify saved finite-goal state, canonical cumulative fills and full trajectories."""
import argparse
import json
import math
import sys
from aggregate_btc5m_exposure_intent_ablation_v1 import get, ROOT, R, RET, STREAMS
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS, compact
from audit_btc5m_target_core_loop_topology_v1 import read, sha
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary

sys.path.insert(0,str(ROOT))
from tools.pair_core_asset_route_sizing_v2 import validate_size

STEM = 'BTC5M_PERSISTENT_REPAIR_DEMAND_V1_20260913'
PACKAGE = ROOT/'.lan_worker_v1/persistent_repair_demand_2026085_20260913_v1'


def audit_arm(tag, result, tr, m):
    cut=m['demand_selection']['t'];end=1788758400000;target=m['work']['absolute_goal']
    assert result['worker'].upper()=='DESKTOP-JIERAGF' and result['runtime_eligible'] is False
    assert result['clock_smoke']['actual_replay_frames']==1462
    assert result['clock_smoke']['manifest_sha256']==sha(PACKAGE/'manifest.json')
    assert result['execution_accounting_valid'] and result['unresolved_owners']==0
    assert result['active_native_submits']==0 and result['max_epoch_residual_overfill']<=1e-9
    assert len(tr['demand_events'])==1 and tr['demand_events'][0]['applied']
    original_by_time={r['t']:r for r in tr['observations']}
    intent_by_time={r['t']:r for r in tr['intent']}
    states_by_time={r['t']:r for r in tr['states']}
    rows_by_time={r['t']:r for r in tr['demand_rows']}
    status='ACTIVE';stop_t=None;binding=[];dust=[];pending_cancel=[];new_down_checks=0
    for row in tr['demand_rows']:
        t=row['t'];state=row['state'];progress=row['progress']
        assert row['original_desired']==original_by_time[t]['desired']
        assert row['effective_desired']==intent_by_time[t]['desired']
        assert state['inv']==states_by_time[t]['inv'] and abs(state['cost']-states_by_time[t]['cost'])<1e-7
        assert progress['target']==target
        remaining=max(0.,target-state['inv']['DOWN'])
        if status=='ACTIVE':
            if remaining<=1e-8:status='CONFIRMED_TARGET_REACHED'
            elif t>=end:status='WITHDRAWN_MARKET_END'
            elif state['inv']['UP']<=state['inv']['DOWN']+1e-8:status='WITHDRAWN_ORIGINAL_NET_DIRECTION_GONE'
            elif state['payoff']['DOWN']>=0:status='WITHDRAWN_DOWN_ALREADY_NONNEGATIVE'
            if status!='ACTIVE':stop_t=t
        assert progress['status']==status and progress['stopped_t']==stop_t
        for side in ('UP','DOWN'):
            owners=[o for o in state['owners'] if o['side']==side]
            assert all(o['state']!='TERMINAL' and o['qty']>=0 for o in owners)
            assert abs(math.fsum(o['qty'] for o in owners)-state['pending_qty'][side])<1e-7
            assert abs(math.fsum(o['qty']*o['limit'] for o in owners)-state['pending_cash'][side])<1e-7
        assert abs(progress['remaining_confirmed']-remaining)<1e-7
        assert abs(progress['unreserved_need']-max(0.,remaining-state['pending_qty']['DOWN']))<1e-7
        assert abs(progress['acquired_since_birth']-(state['inv']['DOWN']-m['work']['initial_down']))<1e-7
        expect=dict(row['original_desired'])
        if (tag=='persist' and status=='ACTIVE') or t==cut:
            expect['DOWN']=max(expect['DOWN'],target)
        assert expect==row['effective_desired'],(tag,t)
        assert row['effective_desired']['UP']==row['original_desired']['UP']
        if row['floor_increment']>1e-8:binding.append(t)
        if status=='ACTIVE' and 1e-8<progress['unreserved_need']<18: dust.append(t)
        if row['cancel_pending_down']>1e-8:pending_cancel.append(t)
    for plan in tr['plans']:
        ops=[o for o in plan['operations'] if o['kind']=='NEW']
        for op in ops:
            validate_size('BTC','PASSIVE',op['price'],op['qty'],quantity_step=.01)
        if plan['t'] not in rows_by_time:continue
        row=rows_by_time[plan['t']];state=row['state']
        quantity=math.fsum(o['qty'] for o in ops if o['side']=='DOWN')
        available=max(0.,row['effective_desired']['DOWN']-state['inv']['DOWN']-state['pending_qty']['DOWN'])
        assert quantity<=available+1e-7,(tag,plan['t'],quantity,available)
        new_down_checks+=bool(quantity)
    final=tr['demand_final']
    assert final is not None
    for side,a in final['final_accounts'].items():
        assert a['reserved_qty']==0 and a['reserved_cash']==0
        assert a['reserved_cash']==result['clock_smoke']['final_pending_cash_direct'][side]
    selected=[]
    for owner in final['tracked_owners']:
        key=owner['key'];assert owner['state']=='TERMINAL'
        fills=[dict(t=e['t'],**r) for e in result['atomic_responsibility_events'] for r in e['fill_rows'] if r['key']==key]
        assert abs(math.fsum(r['fill_increment'] for r in fills)-owner['filled'])<1e-7
        assert 0<=owner['filled']<=owner['qty']+1e-7
        history=[r for r in tr['demand_owner_rows'] if r['key']==key]
        assert all(a['filled']<=b['filled']+1e-7 for a,b in zip(history,history[1:]))
        assert abs(history[-1]['filled']-owner['filled'])<1e-7
        cancels=[dict(t=p['t'],**o) for p in tr['plans'] for o in p['operations'] if o['kind']=='CANCEL' and o['key']==key]
        selected.append(dict(**owner,first_fill_t=fills[0]['t'] if fills else None,
            complete_fill_t=fills[-1]['t'] if fills and abs(owner['filled']-owner['qty'])<1e-7 else None,
            terminal_observed_t=next((r['t'] for r in history if r['state']=='TERMINAL'),None),
            fill_updates=fills,partial_observations=[r for r in history if 1e-8<r['filled']<r['qty']-1e-8],
            cancels=cancels,history=history))
    assert final['work_status']==status or (status=='ACTIVE' and final['work_status']=='WITHDRAWN_MARKET_END')
    initial=tr['demand_events'][0]['state']
    path=path_summary(initial,tr['states'],cut,end)
    points={cut:initial};points.update({r['t']:r for r in tr['states'] if cut<r['t']<=end});times=sorted(points)
    area=math.fsum(max(0.,points[t]['cost']-min(points[t]['inv'].values()))*(n-t)/1000.
                    for t,n in zip(times,times[1:]+[end]))
    assert abs(area-path['negative_floor_area_currency_seconds'])<1e-7
    assert abs(min(min(s['inv'].values())-s['cost'] for s in points.values())-path['minimum_floor'])<1e-7
    assert result['final_inventory']==tr['states'][-1]['inv']
    assert abs(result['final_cost']-tr['states'][-1]['cost'])<1e-7
    assert abs(path['market_end']['cost']-result['final_cost'])<1e-7
    assert all(abs(path['market_end']['inventory_'+s.lower()]-result['final_inventory'][s])<1e-7 for s in ('UP','DOWN'))
    folder=RET/f'persistent-repair-demand-2026085-{tag}-20260913-v1'
    return dict(metrics=compact(result),terminal=geometry(result['final_inventory'],result['final_cost']),trajectory=path,
        source_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'),
        native_safety=result['safety_gate'],unresolved_owners=0,final_pending_cash_direct=result['clock_smoke']['final_pending_cash_direct'],
        post_end_economic_delta_zero=True,tracked_owners=selected,work_status=final['work_status'],work_stopped_t=final['work_stopped_t'],
        goal_audit=dict(rows=len(tr['demand_rows']),constant_target=target,binding_rows=len(binding),
                        first_binding_t=binding[0] if binding else None,last_binding_t=binding[-1] if binding else None,
                        goal_unreserved_below18_observations=len(dust),cancel_pending_observations=len(pending_cancel),new_down_deficit_checks=new_down_checks),
        receipt_history=dict(complete_raw_history_available=False,saved_last_drain_rows=len(final['canonical_receipts']),
            verified_native_receipt_count=result['native_receipts'],
            scope='sim._receipt_delta_rows is reset each physical_process. The finish snapshot is only the last drain batch, NOT full raw receipt history. Use canonical cumulative owner observations and final native receipt-ledger reconciliation; exact private receipt latency remains unidentified.'),
        new_by_side={s:sum(o['kind']=='NEW' and o['side']==s for p in tr['plans'] if cut<=p['t']<end for o in p['operations']) for s in ('UP','DOWN')})


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--control-only',action='store_true');args=ap.parse_args()
    m=read(PACKAGE/'manifest.json')
    assert m==read(R/(STEM+'_PREREGISTERED.json')) and all(sha(PACKAGE/f)==h for f,h in m['files'].items())
    old,ot=get(RET/'single-repair-demand-2026085-pulse-20260913-v1')
    control,ct=get(RET/'persistent-repair-demand-2026085-control-20260913-v1')
    parity={k:old[k]==control[k] for k in PARITY_FIELDS}
    parity.update({k:ot[k]==ct[k] for k in (*STREAMS,'intent','money_rows','demand_events')});assert all(parity.values())
    output=dict(status='PASS',control_parity=parity,baseline='single-repair-demand-2026085-pulse-20260913-v1',native_runs_here=0)
    if args.control_only:
        (R/(STEM+'_CONTROL.json')).write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8');print(json.dumps(output));return
    persist,pt=get(RET/'persistent-repair-demand-2026085-persist-20260913-v1')
    cut=m['demand_selection']['t']
    prefix={k:[r for r in ct[k] if r['t']<=cut]==[r for r in pt[k] if r['t']<=cut] for k in (*STREAMS,'intent','money_rows')}
    assert all(prefix.values()),prefix
    assert persist['theta']==control['theta']
    arms={tag:audit_arm(tag,result,tr,m) for tag,result,tr in (('control',control,ct),('persist',persist,pt))}
    cut_ops=[o for p in pt['plans'] if p['t']==cut for o in p['operations'] if o['kind']=='NEW']
    chosen=next(o for o in cut_ops if o['side']=='DOWN' and o['qty']==30. and o['price']==.31)
    assert len([r for r in pt['native_actions'] if r['t']==cut and r['kind']=='NEW' and r['side']=='DOWN' and r['qty']==30. and r['price']==.31])==1
    selected={tag:next(r for r in arm['tracked_owners'] if r['key']==chosen['key']) for tag,arm in arms.items()}
    t=1788758121304
    maintenance={tag:[r for r in tr['demand_maintenance_rows'] if r['t']==t and r['key'] in ('DOWN_75','DOWN_76','DOWN_77')]
                 for tag,tr in (('control',ct),('persist',pt))}
    output.update(status='COMPLETE',verification='PASS_WITH_RAW_RECEIPT_HISTORY_LIMIT',prefix_through_cut=prefix,
        arms=arms,selected_order=chosen,selected_order_lifecycle=selected,prior_cancel_frame_maintenance=maintenance,
        fitted_weights_changed=False,scope='One finite repair work item on a consumed oracle market; not an independently learned complete loop or actual net-overshoot test.')
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(output,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    brief=dict(status=output['status'],verification=output['verification'],
        selected={tag:{k:v for k,v in r.items() if k not in ('history','fill_updates','partial_observations')} for tag,r in selected.items()},
        maintenance=maintenance,
        arms={tag:dict(terminal=a['terminal'],goal=a['goal_audit'],work_status=a['work_status'],work_stopped_t=a['work_stopped_t'],
                       loss_area=a['trajectory']['negative_floor_area_currency_seconds'],worst=a['trajectory']['minimum_floor'],
                       events=a['metrics']['events'],submits=a['metrics']['submits'],new_by_side=a['new_by_side'],
                       final_pending_cash=a['final_pending_cash_direct']) for tag,a in arms.items()})
    print(json.dumps(brief,indent=2))


if __name__=='__main__':main()
