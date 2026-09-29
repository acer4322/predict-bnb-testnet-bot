"""Read-only paired verification: actual mixed-route receipts, no native imports."""
import argparse
import bisect
import collections
import inspect
import json
import math

from aggregate_btc5m_exposure_intent_ablation_v1 import get, ROOT, R, RET, STREAMS
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS
from audit_btc5m_target_core_loop_topology_v1 import read, sha
import verify_btc5m_whole_oracle_repair_v1 as previous
from verify_btc5m_whole_oracle_repair_v1 import close
from btc5m_fixed15_research_condition_v1 import make_validator
from pair_core_asset_route_sizing_v2 import validate_size
from audit_btc5m_post_exposure_response_v1 import reconstruct, analyze
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary
from btc5m_active_repair_opportunity_v1 import decide
from hft244_pair_route_legality_v1 import crossing_owners

STEM = 'BTC5M_ACTIVE_REPAIR_OPPORTUNITY_V1_20260913'
PACKAGE = ROOT / '.lan_worker_v1/fixed15_active_opportunity_2026085_20260913_v1'
START, END = 1788758100000, 1788758400000


def same_value(a, b):
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for k in a: same_value(a[k], b[k])
    elif isinstance(a, (float, int)) and not isinstance(a, bool):
        close(a, b)
    else:
        assert a == b, (a, b)


def folder(tag):
    return RET / f'fixed15-core-loop-2026085-opportunity-{tag}-20260913-v1'


def receipt_auditor():
    # Reuse complete raw/canonical accounting with explicit route-aware sizing.
    source = inspect.getsource(previous.audit_receipts)
    before = "validate_size('BTC', 'PASSIVE', b['price'], b['qty'], quantity_step=.01)"
    assert source.count(before) == 1
    source = source.replace(before, "validate_size('BTC', b['route'], b['price'], b['qty'], quantity_step=.01)")
    before = "assert c['route']=='PASSIVE' and c['state']=='TERMINAL'"
    assert source.count(before) == 1
    source = source.replace(before, "assert c['route']==b['route'] and c['route'] in ('PASSIVE','ACTIVE') and c['state']=='TERMINAL'")
    ns = dict(previous.__dict__, validate_size=make_validator(validate_size))
    exec(compile(source, 'mixed_route_receipt_audit', 'exec'), ns)
    return ns['audit_receipts']


def control_check():
    old, ot = get(RET / 'fixed15-core-loop-2026085-half-20260913-v2')
    control, ct = get(folder('control'))
    parity = {k: old[k] == control[k] for k in PARITY_FIELDS}
    parity.update({'trace.'+k: v == ct[k] for k, v in ot.items()})
    assert all(parity.values()), [k for k,v in parity.items() if not v]
    old_gate = dict(old['safety_gate']); gate = dict(control['safety_gate'])
    assert old_gate.pop('passive_only_has_no_active') == gate.pop('active_matches_opportunity')
    assert old_gate == gate
    first = ct['opportunity_first']
    observed = read(R / (STEM+'_OBSERVATION.json'))
    # Independent offline observation is evidence, never an input to the runner.
    witness = observed['first']
    for k in ('t', 'quantity', 'active_ask', 'available_cash'):
        close(first[k], witness[k])
    assert ct['opportunity_submissions'] == [] and control['active_native_submits'] == 0
    receipt_auditor()(control, ct)
    out = dict(status='PASS', complete_v18_half_trace_parity=parity,
               native_safety_semantics_parity=True, first=first,
               result_sha256=sha(folder('control')/'result.json'))
    (R / (STEM+'_CONTROL.json')).write_text(json.dumps(out, indent=2)+'\n', encoding='utf-8')
    return control, ct, out


def canonical_legs(result, trace):
    """Attribute actual receipt prices to the canonical time that saw each fill."""
    queues = collections.defaultdict(collections.deque)
    for row in trace['demand_final']['full_raw_receipts']:
        assert row['fee'] == 0., 'This diagnostic retains the pinned zero-fee native model'
        if row['qty'] > 0:
            queues[row['key']].append([row['qty'], row['contractPrice'], row['maker']])
    legs = []
    for event in result['atomic_responsibility_events']:
        for fill in event['fill_rows']:
            remaining = fill['fill_increment']
            while remaining > 1e-10:
                raw = queues[fill['key']][0]
                qty = min(remaining, raw[0])
                legs.append(dict(t=event['t'], side=fill['side'], route='MAKER' if raw[2] else 'TAKER',
                                 qty=qty, cash=qty*raw[1]))
                remaining -= qty; raw[0] -= qty
                if raw[0] <= 1e-10: queues[fill['key']].popleft()
    assert sum(v[0] for q in queues.values() for v in q) < 1e-7
    return legs


def audit(tag, result, trace):
    assert result['worker'].upper() == 'DESKTOP-JIERAGF'
    assert result['execution_accounting_valid'] and result['unresolved_owners'] == 0
    assert result['oracle_direction'] == 'ORACLE_UP' and result['runtime_eligible'] is False
    assert result['legacy_active_policy_disabled']
    clock = result['clock_smoke']
    assert clock['manifest_sha256'] == sha(PACKAGE/'manifest.json')
    assert clock['actual_replay_frames'] == 1462 and clock['profit_retention'] == .5
    assert clock['passive_ticket'] == 15.
    new = [o for p in trace['plans'] for o in p['operations'] if o['kind'] == 'NEW']
    assert all(o['qty']==15. for o in new if o['route']=='PASSIVE')
    assert all(o['price']*o['qty'] >= 1-1e-8 for o in new)
    active = [o for o in new if o['route']=='ACTIVE']
    assert len(active) == result['active_native_submits'] == (tag=='active')
    receipts = receipt_auditor()(result, trace)
    rows = reconstruct(canonical_legs(result, trace))
    states = {s['t']:s for s in trace['states']}
    paid = dict(UP=0., DOWN=0.); paid_rows = [(0, dict(paid))]
    for row in rows:
        close(row['cost'], states[row['t']]['cost'])
        for side in paid:
            close(row['inv'][side], states[row['t']]['inv'][side])
            paid[side] += sum(row['flow'][side][r]['cash'] for r in ('MAKER','TAKER'))
        assert paid['DOWN'] <= .5*(row['inv']['UP']-paid['UP'])+1e-7
        paid_rows.append((row['t'], dict(paid)))
    times = [r[0] for r in paid_rows]
    paid_at = lambda t: paid_rows[bisect.bisect_right(times,t)-1][1]
    for row in trace['profit_budget_rows']:
        actual = paid_at(row['t'])
        for s in actual: close(row['paid'][s], actual[s])
        room = max(0., .5*(row['inv']['UP']-actual['UP'])-actual['DOWN']-row['pending_down_cash'])
        close(room, row['available_cash'])
        expected = min(row['old_quantity_cap'], max(0., round(math.floor((room/row['price']+1e-10)/.01)*.01,8)))
        close(expected, row['admitted'])
    demand = {row['t']:row['state'] for row in trace['demand_rows']}
    for row in trace['demand_rows']:
        s = row['state']; p = paid_at(row['t'])
        for side in p:
            owners = [o for o in s['owners'] if o['side']==side]
            close(sum(o['qty']*o['limit'] for o in owners),s['pending_cash'][side])
            close(sum(o['qty'] for o in owners),s['pending_qty'][side])
        assert p['DOWN']+s['pending_cash']['DOWN'] <= .5*(s['inv']['UP']-p['UP'])+1e-7
    for plan in trace['plans']:
        down = [o for o in plan['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
        if down:
            p = paid_at(plan['t']); s = demand[plan['t']]
            room = max(0.,.5*(s['inv']['UP']-p['UP'])-p['DOWN']-s['pending_cash']['DOWN'])
            assert sum(o['qty']*o['price'] for o in down) <= room+1e-7
    source_path = ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(source_path) == read(PACKAGE/'manifest.json')['oracle_source_sha256']
    books = {b['received_ms']:b for b in read(source_path)['books']}
    for row in trace['opportunity_rows']:
        book = books[row['t']]
        assert book['source_ms'] <= row['t']
        close(row['active_ask'], round(1-book['best_bid'],10))
        close(row['visible_depth'], dict(book['bids'])[book['best_bid']])
        replay = decide(row['state'], paid_at(row['t']), row['passive_price'], row['active_ask'],
                        row['visible_depth'], row['original_operations'], .01, .5, crossing_owners)
        for k,v in replay.items(): same_value(v,row[k])
    selected = trace['opportunity_first']
    direct = None
    if active:
        op = active[0]; c = next(c for c in receipts['orders'] if c['key']==op['key'])
        assert op['qty'] == selected['quantity'] and op['qty'] != 15.
        assert op['qty'] <= min(selected['quantity_cap'], selected['payoff_cap'], selected['visible_depth'])+1e-7
        assert op['qty']*op['price'] <= selected['available_cash']+1e-7
        raw = [r for r in trace['demand_final']['full_raw_receipts'] if r['key']==op['key'] and r['qty']>1e-8]
        assert all(r['contractPrice']<=op['price']+1e-8 for r in raw)
        direct = dict(order=c, raw_receipts=raw, weak_payoff_gain=c['filled']-c['payment']-c['fees'],
                      strong_payoff_drag=c['payment']+c['fees'], maker_zero_observed=any(r['maker']==0 for r in raw))
    terminal = geometry(result['final_inventory'], result['final_cost'])
    return dict(terminal=terminal, paid_by_side=paid, retention=terminal['up']/(result['final_inventory']['UP']-paid['UP']),
                trajectory=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),trace['states'],START,END),
                post_exposure=analyze(rows,START,END), receipts=receipts, direct_active=direct,
                budget_rows=len(trace['profit_budget_rows']), own_budget_checks=len(demand),
                first=selected, submits=result['submits'], passive_submits=result['passive_native_submits'],
                active_submits=result['active_native_submits'], core_similarity=result['core_similarity'],
                work_status=dict(collections.Counter(w['status'] for w in trace['demand_final']['works'])),
                native_safety=result['safety_gate'], result_sha256=sha(folder(tag)/'result.json'))


def diagnose(pairs, terminal_delta):
    flows = {}; plans = {}
    for tag, (result, trace) in pairs.items():
        f = collections.defaultdict(lambda: dict(qty=0., cash=0.))
        for leg in canonical_legs(result, trace):
            key = (leg['t'], leg['side'], leg['route'])
            for field in ('qty', 'cash'): f[key][field] += leg[field]
        flows[tag] = f
        plans[tag] = {p['t']:[{k:o[k] for k in ('side','route','qty','price','role')}
                              for o in p['operations'] if o['kind']=='NEW'] for p in trace['plans']}
    changes = []; totals = {s:dict(qty=0., cash=0.) for s in ('UP','DOWN')}
    for key in sorted(set(flows['control']) | set(flows['active'])):
        before, after = flows['control'][key], flows['active'][key]
        delta = {field:after[field]-before[field] for field in before}
        if any(abs(v)>1e-7 for v in delta.values()):
            changes.append(dict(t=key[0],seconds=(key[0]-START)/1000,side=key[1],route=key[2],
                                control=before,active=after,difference=delta))
            for field in delta: totals[key[1]][field] += delta[field]
    cash_change = sum(v['cash'] for v in totals.values())
    close(cash_change, terminal_delta['cost'])
    close(totals['UP']['qty']-cash_change,terminal_delta['up'])
    close(totals['DOWN']['qty']-cash_change,terminal_delta['down'])
    new_changes=[]
    for t in sorted(set(plans['control']) | set(plans['active'])):
        if plans['control'].get(t) == plans['active'].get(t): continue
        row=dict(t=t,seconds=(t-START)/1000)
        for tag, (_,trace) in pairs.items():
            intent=next(x for x in trace['intent'] if x['t']==t)
            budget=[{k:b[k] for k in ('price','available_cash','admitted','pending_down_cash','paid')}
                    for b in trace['profit_budget_rows'] if b['t']==t]
            row[tag]=dict(new=plans[tag][t], budget=budget,
                          intent={k:intent[k] for k in ('inv','desired','applied_exposure','reserved_qty')})
        new_changes.append(row)
    out=dict(status='PASS',flow_changes=changes,totals_by_side=totals,changed_new_frames=new_changes,
             interpretation='Shared-budget competition and later own-state policy changes are observed. The extra UP fill is not proof of a specific release-credit rule or Target policy.',
             native_jobs_added=0)
    (R/(STEM+'_FLOW_DIAGNOSTIC.json')).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    return dict(flow_changed_buckets=len(changes),changed_new_frames=len(new_changes),totals_by_side=totals)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--control-only',action='store_true');args=parser.parse_args()
    manifest=read(PACKAGE/'manifest.json')
    assert all(sha(PACKAGE/n)==h for n,h in manifest['files'].items())
    c,ct,control=control_check()
    if args.control_only:
        print(json.dumps(dict(status='PASS',first_t=control['first']['t'],quantity=control['first']['quantity'])));return
    a,at=get(folder('active'))
    assert a['theta']==c['theta'] and at['opportunity_first']==ct['opportunity_first']
    first=ct['opportunity_first']['t']
    prefix={k:[r for r in ct[k] if r['t']<first]==[r for r in at[k] if r['t']<first]
            for k in (*STREAMS,'intent','money_rows','profit_budget_rows','demand_rows')}
    assert all(prefix.values()),prefix
    old_plan=next(p for p in ct['plans'] if p['t']==first)
    new_plan=next(p for p in at['plans'] if p['t']==first)
    assert new_plan['operations'][:-1]==old_plan['operations'] and new_plan['operations'][-1]['route']=='ACTIVE'
    arms=dict(control=audit('control',c,ct),active=audit('active',a,at))
    delta={k:arms['active']['terminal'][k]-arms['control']['terminal'][k] for k in ('up','down','cost','up_net')}
    direct=arms['active']['direct_active']
    postcheck=read(R/(STEM+'_POSTCHECK.json'))
    assert postcheck['status']=='PASS' and postcheck['native_sha256']==manifest['native_sha256']
    condition=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert all(sha(ROOT/'tools'/n)==v['before'] for n,v in condition.items())
    assert all(r['sizing_installation']==condition for r in (a,c))
    diagnosis=diagnose(dict(control=(c,ct),active=(a,at)),delta)
    out=dict(status='COMPLETE',verification='PASS',native_jobs=2,local_native_jobs=0,model_fits=0,
             manifest_sha256=sha(PACKAGE/'manifest.json'),control_parity=control,prefix=prefix,
             arms=arms,terminal_delta=delta,flow_diagnosis=diagnosis,worker_postcheck_pass=True,
             downstream_policy_effect=dict(up=delta['up']+direct['strong_payoff_drag'],down=delta['down']-direct['weak_payoff_gain']),
             scope='One causal opportunity in one consumed oracle market; not a recurrent learned route policy.')
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(out,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',delta=delta,direct_active=direct,
                         arms={t:dict(terminal=r['terminal'],paid=r['paid_by_side'],retention=r['retention'],submits=r['submits'],
                                      area=r['trajectory']['negative_floor_area_currency_seconds'],worst=r['trajectory']['minimum_floor']) for t,r in arms.items()}),indent=2))


if __name__=='__main__': main()
