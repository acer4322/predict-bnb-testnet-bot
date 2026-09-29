"""Read-only verification and diagnosis of the three frozen whole-market arms."""
import collections
import json
import math
import statistics
import sys

from aggregate_btc5m_exposure_intent_ablation_v1 import get, ROOT, R, RET, STREAMS
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS
from audit_btc5m_target_core_loop_topology_v1 import read, sha
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary

sys.path.insert(0, str(ROOT))
from tools.pair_core_asset_route_sizing_v2 import validate_size

STEM = 'BTC5M_WHOLE_ORACLE_REPAIR_V1_20260913'
PACKAGE = ROOT / '.lan_worker_v1/whole_oracle_repair_2026085_20260913_v1'
START, END = 1788758100000, 1788758400000
ZERO = dict(inv=dict(UP=0., DOWN=0.), cost=0.)


def close(a, b):
    assert abs(a-b) < 1e-7, (a, b)


def distribution(values):
    return dict(count=len(values), minimum=min(values), median=statistics.median(values), maximum=max(values)) if values else dict(count=0)


def audit_receipts(result, tr):
    final = tr['demand_final']
    raw = final['full_raw_receipts']
    assert final['receipt_history_complete'] and len(raw) == result['native_receipts']
    assert [r['sequence'] for r in raw] == list(range(1, len(raw)+1))
    carriers = {r['key']:r for r in final['all_final_carriers']}
    births = {o['key']:dict(t=p['t'], **o) for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW'}
    assert set(carriers) == set(births) and len(births) == result['submits']
    groups = collections.defaultdict(list)
    canonical = collections.defaultdict(list)
    cancel = collections.defaultdict(list)
    history = collections.defaultdict(list)
    for r in raw:
        assert r['key'] in carriers and r['exchange_ts'] <= r['receive_ts']
        assert r['side'] == (1 if carriers[r['key']]['side']=='UP' else -1)
        close(r['contractPrice'], r['price'] if r['side']==1 else 1-r['price'])
        groups[r['key']].append(r)
    for event in result['atomic_responsibility_events']:
        for row in event['fill_rows']:
            canonical[row['key']].append(dict(t=event['t'], **row))
    for p in tr['plans']:
        for o in p['operations']:
            if o['kind']=='CANCEL': cancel[o['key']].append(p['t'])
    for row in tr['demand_owner_rows']: history[row['key']].append(row)
    order_rows = []
    for key, c in carriers.items():
        b = births[key]; rows = groups[key]
        validate_size('BTC', 'PASSIVE', b['price'], b['qty'], quantity_step=.01)
        assert c['route']=='PASSIVE' and c['state']=='TERMINAL'
        assert -1e-8 <= c['filled'] <= c['qty']+1e-7
        assert all(a['cumulative_qty'] <= z['cumulative_qty']+1e-7 for a,z in zip(rows,rows[1:]))
        close(math.fsum(x['qty'] for x in rows), c['filled'])
        close(rows[-1]['cumulative_qty'] if rows else 0., c['filled'])
        close(math.fsum(x['qty']*x['contractPrice'] for x in rows), c['payment'])
        close(math.fsum(x['fee'] for x in rows), c['fees'])
        close(math.fsum(x['fill_increment'] for x in canonical[key]), c['filled'])
        positive = [r for r in rows if r['qty']>1e-8]
        first_receive = min(r['receive_ts']/1e6 for r in positive) if positive else None
        first_exchange = min(r['exchange_ts']/1e6 for r in positive) if positive else None
        first_seen = next((r['t'] for r in canonical[key] if r['fill_increment']>1e-8), None)
        if first_receive is not None:
            assert first_receive >= b['t']-1e-3 and first_seen >= first_receive-1e-3
        terminals = [r['t'] for r in history[key] if r['state']=='TERMINAL']
        order_rows.append(dict(**c, born_t=b['t'], cancel_times=cancel[key],
            first_exchange_t=first_exchange, first_receive_t=first_receive, first_canonical_t=first_seen,
            last_receive_t=max(r['receive_ts']/1e6 for r in positive) if positive else None,
            terminal_observed_t=min(terminals) if terminals else None,
            receipt_count=len(rows), positive_receipt_count=len(positive),
            raw_wait_ms=first_receive-b['t'] if positive else None,
            canonical_wait_ms=first_seen-b['t'] if positive else None))
    for s in ('UP','DOWN'):
        close(math.fsum(c['filled'] for c in carriers.values() if c['side']==s), result['final_inventory'][s])
        a = final['final_accounts'][s]
        assert a['reserved_qty']==0 and a['reserved_cash']==0
    close(math.fsum(c['payment']+c['fees'] for c in carriers.values()), result['final_cost'])
    positive = [r for r in raw if r['qty']>1e-8]
    tiny = [r for r in raw if r['qty']<=1e-8]
    return dict(count=len(raw), complete=True, owner_accounting_pass=True,
        positive_rows=len(positive), numerical_dust_rows=len(tiny),
        sub_one_dollar_positive_receipts=sum(r['qty']*r['contractPrice'] < 1-1e-8 for r in positive),
        maker_flags=dict(collections.Counter(str(r['maker']) for r in positive)),
        exchange_to_receive_ms=distribution([(r['receive_ts']-r['exchange_ts'])/1e6 for r in positive]),
        order_first_fill_wait_ms={s:distribution([r['raw_wait_ms'] for r in order_rows if r['side']==s and r['raw_wait_ms'] is not None]) for s in ('UP','DOWN')},
        orders=order_rows)


def stop_reason(state, target):
    if target-state['inv']['DOWN'] <= 1e-8: return 'CONFIRMED_TARGET_REACHED'
    if state['t'] >= END: return 'WITHDRAWN_MARKET_END'
    if state['inv']['UP'] <= state['inv']['DOWN']+1e-8: return 'WITHDRAWN_ORIGINAL_NET_DIRECTION_GONE'
    if state['inv']['DOWN']-state['cost'] >= 0: return 'WITHDRAWN_DOWN_ALREADY_NONNEGATIVE'
    return None


def receipt_time_path(result,tr):
    # Sensitivity check: economic geometry at receipt availability instead of the
    # producer's observation grid. These are simulated receipt times, not Target latency.
    batches=collections.defaultdict(list)
    for row in tr['demand_final']['full_raw_receipts']: batches[row['receive_ts']].append(row)
    inv=dict(UP=0.,DOWN=0.); cost=0.; cumulative={}; states=[]
    for ts,rows in sorted(batches.items()):
        for row in rows:
            key=row['key']; side='UP' if row['side']==1 else 'DOWN'
            delta=row['cumulative_qty']-cumulative.get(key,0.)
            assert delta>=-1e-8
            cumulative[key]=row['cumulative_qty']; inv[side]+=delta
            cost+=row['qty']*row['contractPrice']+row['fee']
        states.append(dict(t=ts/1e6,inv=dict(inv),cost=cost))
    for side in inv: close(inv[side],result['final_inventory'][side])
    close(cost,result['final_cost'])
    assert states[-1]['t']<=END
    path=path_summary(ZERO,states,START,END)
    return {k:v for k,v in path.items() if k!='changed_rows'}


def audit_automatic(tag, result, tr):
    rows = tr['demand_rows']; works = tr['demand_final']['works']
    states = {r['t']:r for r in tr['states']}
    observations = {r['t']:r for r in tr['observations']}
    intents = {r['t']:r for r in tr['intent']}
    assert len(rows)==len(observations)
    if tag=='capacity': assert not works
    workmap = {w['id']:w for w in works}
    for row in rows:
        t=row['t']; s=row['state']; e=row['eligibility']; work=workmap.get(row['work_id'])
        assert s['inv']==states[t]['inv']; close(s['cost'],states[t]['cost'])
        assert row['original_desired']==observations[t]['desired']
        assert row['effective_desired']==intents[t]['desired']
        assert row['original_desired']['UP']==row['effective_desired']['UP']
        for side in ('UP','DOWN'):
            owners=[o for o in s['owners'] if o['side']==side]
            assert all(o['state']!='TERMINAL' for o in owners)
            close(math.fsum(o['qty'] for o in owners),s['pending_qty'][side])
            close(math.fsum(o['qty']*o['limit'] for o in owners),s['pending_cash'][side])
        if work:
            assert work['born_t'] <= t < work['ended_t']
            assert row['progress']['status']=='ACTIVE' and row['progress']['target']==work['target']
            close(row['effective_desired']['DOWN'],max(row['original_desired']['DOWN'],work['target']))
            close(row['progress']['unreserved_need'],max(0.,work['target']-s['inv']['DOWN']-s['pending_qty']['DOWN']))
        else: assert row['effective_desired']==row['original_desired'] and row['progress'] is None
        expected_active=next((w['id'] for w in works if w['born_t']<=t<w['ended_t']),None)
        assert row['work_id']==expected_active
        if tag=='repeat' and expected_active is None and t not in {w['ended_t'] for w in works}:
            assert not e['eligible'], ('eligible work was skipped',t)
    audited=[]
    for i,w in enumerate(works):
        assert w['id']==i+1 and (i==0 or works[i-1]['ended_t']<w['born_t'])
        birth=next(r for r in rows if r['t']==w['born_t']); s=birth['state']; e=birth['eligibility']
        assert e['eligible'] and not e['conflicts'] and e['requested']==30.
        assert s['payoff']['DOWN']<0 and s['inv']['UP']>s['inv']['DOWN']
        assert e['original_request'] < max(18.,1/e['price']) <= 30.
        close(w['target'],s['inv']['DOWN']+s['pending_qty']['DOWN']+30.)
        assert e['quantity_capacity'] >= 30.-1e-8 and e['money_capacity'] >= 30.-1e-8
        terminal=next(r for r in tr['states'] if r['t']>=w['born_t'] and stop_reason(r,w['target']))
        assert terminal['t']==w['ended_t'] and stop_reason(terminal,w['target'])==w['status']
        active=[r for r in rows if r['work_id']==w['id']]
        audited.append(dict(**w, duration_ms=w['ended_t']-w['born_t'], active_rows=len(active),
            binding_rows=sum(r['floor_increment']>1e-8 for r in active),
            unreserved_subminimum_rows=sum(1e-8<r['progress']['unreserved_need']<18 for r in active),
            fully_reserved_but_unconfirmed_rows=sum(r['progress']['unreserved_need']<=1e-8 and r['progress']['remaining_confirmed']>1e-8 for r in active)))
    return dict(works=audited,status_counts=dict(collections.Counter(w['status'] for w in works)),
        eligibility_counts=dict(collections.Counter(r['eligibility']['reason'] for r in rows)),
        binding_rows=sum(r['floor_increment']>1e-8 for r in rows))


def audit_arm(tag,result,tr):
    assert result['worker'].upper()=='DESKTOP-JIERAGF' and result['runtime_eligible'] is False
    assert result['oracle_direction']=='ORACLE_UP' and result['target_direction_input']
    assert result['clock_smoke']['actual_replay_frames']==1462
    assert result['clock_smoke']['manifest_sha256']==sha(PACKAGE/'manifest.json')
    assert result['execution_accounting_valid'] and result['unresolved_owners']==0
    assert result['active_native_submits']==0 and result['max_epoch_residual_overfill']<=1e-9
    receipt=audit_receipts(result,tr)
    path=path_summary(ZERO,tr['states'],START,END)
    points={START:ZERO};points.update({r['t']:r for r in tr['states'] if START<r['t']<=END})
    times=sorted(points)
    area=math.fsum(max(0.,points[t]['cost']-min(points[t]['inv'].values()))*(n-t)/1000. for t,n in zip(times,times[1:]+[END]))
    close(area,path['negative_floor_area_currency_seconds'])
    close(path['market_end']['cost'],result['final_cost'])
    assert result['final_inventory']==tr['states'][-1]['inv']
    worst=min(tr['states'],key=lambda s:min(s['inv'].values())-s['cost'])
    state_at_worst=next(r for r in tr['demand_rows'] if r['t']==worst['t'])
    carriers={r['key']:r for r in receipt['orders']}
    pending=[dict(**o, born_t=carriers[o['key']]['born_t'],
                  age_ms=worst['t']-carriers[o['key']]['born_t'],
                  first_receive_t=carriers[o['key']]['first_receive_t'],
                  last_receive_t=carriers[o['key']]['last_receive_t']) for o in state_at_worst['state']['owners']]
    weakest=state_at_worst['state']
    pending_down_gain=weakest['pending_qty']['DOWN']-weakest['pending_cash']['DOWN']
    future_up_cost=weakest['pending_cash']['UP']
    raw=tr['demand_final']['full_raw_receipts']
    last_fills={s:max(r['receive_ts'] for r in raw if r['side']==sgn and r['qty']>1e-8)/1e6 for s,sgn in (('UP',1),('DOWN',-1))}
    late_orders={s:[o for o in receipt['orders'] if o['side']==s and o['born_t']>last_fills[s]] for s in ('UP','DOWN')}
    last_intent=tr['intent'][-1]
    final_net=last_intent['inv']['UP']-last_intent['inv']['DOWN']
    final_gross=sum(last_intent['inv'].values())
    close(last_intent['applied_exposure'],abs(math.tanh(result['theta'][3]*final_net/(1.+final_gross))))
    folder=RET/f'whole-oracle-repair-2026085-{tag}-20260913-v1'
    return dict(terminal=geometry(result['final_inventory'],result['final_cost']),trajectory=path,
        score=result['core_similarity'],submits=result['submits'],cancel_requests=result['cancel_requests'],
        new_by_side={s:sum(o['side']==s for o in receipt['orders']) for s in ('UP','DOWN')},
        zero_filled_orders_by_side={s:sum(o['side']==s and o['filled']<=1e-8 for o in receipt['orders']) for s in ('UP','DOWN')},
        native_receipts=receipt, automatic=audit_automatic(tag,result,tr) if tag!='control' else None,
        receipt_time_trajectory=receipt_time_path(result,tr),
        direction_diagnosis=dict(last_intent=last_intent,
            magnitude_rule_verified='abs(tanh(theta[3] * current_net/(1+current_gross))); frozen theta[3]=-0.8',
            last_positive_receipt_seconds={s:(t-START)/1000. for s,t in last_fills.items()},
            new_after_last_fill={s:dict(count=len(orders),requested_qty=math.fsum(o['qty'] for o in orders),
                                      filled_qty=math.fsum(o['filled'] for o in orders)) for s,orders in late_orders.items()}),
        worst=dict(t=worst['t'],seconds=(worst['t']-START)/1000.,state=weakest,pending=pending,
            down_payoff_if_pending_down_fills_only=weakest['payoff']['DOWN']+pending_down_gain,
            down_payoff_if_all_pending_fill=weakest['payoff']['DOWN']+pending_down_gain-future_up_cost,
            demand_row=state_at_worst),
        native_safety=result['safety_gate'],final_pending_cash_direct=result['clock_smoke']['final_pending_cash_direct'],
        source_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'))


def main():
    m=read(PACKAGE/'manifest.json')
    assert m==read(R/(STEM+'_PREREGISTERED.json'))
    assert all(sha(PACKAGE/f)==h for f,h in m['files'].items())
    loaded={tag:get(RET/f'whole-oracle-repair-2026085-{tag}-20260913-v1') for tag in ('control','capacity','repeat')}
    old,ot=get(RET/'persistent-repair-demand-2026085-persist-20260913-v1')
    c,ct=loaded['control']
    parity={k:old[k]==c[k] for k in PARITY_FIELDS}
    parity.update({k:ot[k]==ct[k] for k in (*STREAMS,'intent','money_rows','demand_events')})
    assert all(parity.values())
    assert all(r['theta']==c['theta'] for r,t in loaded.values())
    first_birth=loaded['repeat'][1]['demand_final']['works'][0]['born_t']
    prefix={k:[r for r in loaded['capacity'][1][k] if r['t']<first_birth]==[r for r in loaded['repeat'][1][k] if r['t']<first_birth] for k in (*STREAMS,'intent','money_rows')}
    assert all(prefix.values())
    arms={tag:audit_arm(tag,*pair) for tag,pair in loaded.items()}
    target=read(R/'BTC5M_EXPOSURE_SUPPRESSION_METRIC_V1_20260913.json')['target_markets']['2026085']
    assert target['source_sha256']==m['oracle_source_sha256']
    target_states=[dict(t=r['t'], inv=dict(UP=r['inventory_up'],DOWN=r['inventory_down']),cost=r['cost']) for r in target['curve']]
    base,new=arms['capacity'],arms['repeat']
    comparisons=dict(repeat_minus_capacity={k:new['terminal'][k]-base['terminal'][k] for k in ('up','down','up_net','cost')},
        repeat_loss_area_change_percent=100*(new['trajectory']['negative_floor_area_currency_seconds']/base['trajectory']['negative_floor_area_currency_seconds']-1),
        repeat_worst_floor_change=new['trajectory']['minimum_floor']-base['trajectory']['minimum_floor'],
        repeat_receipt_time_loss_area_change_percent=100*(new['receipt_time_trajectory']['negative_floor_area_currency_seconds']/base['receipt_time_trajectory']['negative_floor_area_currency_seconds']-1),
        repeat_target_net_fraction=new['terminal']['up_net']/target['terminal']['up_net'])
    output=dict(status='COMPLETE',verification='PASS',manifest_sha256=sha(PACKAGE/'manifest.json'),
        control_parity=parity,automatic_prefix_before_first_birth=prefix,arms=arms,comparisons=comparisons,
        target_offline=dict(terminal=target['terminal'],trajectory=path_summary(ZERO,target_states,START,END),
            source_sha256=target['source_sha256'],timing_limit='Target event-time buckets are offline observations, not verified runtime receipt availability or queue evidence.'),
        native_jobs=3,local_native_jobs=0,model_fits=0,scope=m['limits'])
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(output,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=output['status'],verification=output['verification'],comparisons=comparisons,
        arms={tag:dict(terminal=a['terminal'],area=a['trajectory']['negative_floor_area_currency_seconds'],
            worst=a['trajectory']['minimum_floor'],receipts=a['native_receipts']['count'],
            works=a['automatic']['status_counts'] if a['automatic'] else None) for tag,a in arms.items()}),indent=2))


if __name__=='__main__': main()
