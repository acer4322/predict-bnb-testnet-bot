"""Collected-result verification for the one pending-UP-backed Passive15 probe."""
import hashlib
import json
import types

import verify_btc5m_unbudgeted_opportunity_v1 as unbudgeted
import verify_btc5m_active_repair_opportunity_v1 as previous
from prepare_btc5m_pending_ticket_probe_v1 import (
    ROOT, R, PACKAGE, PARENT, BASE, JOB, STEM, START, read, sha, dump, compile_source,
)
from aggregate_btc5m_exposure_intent_ablation_v1 import get, RET, STREAMS
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary
from btc5m_pending_ticket_probe_v1 import decide
from hft244_pair_route_legality_v1 import crossing_owners


def main():
    manifest = read(PACKAGE/'manifest.json')
    assert all(sha(PACKAGE/n) == h for n, h in manifest['files'].items())
    assert sha(BASE/'result.json') == manifest['baseline_result_sha256']
    assert sha(BASE/'clock_trace.json.gz') == manifest['baseline_trace_sha256']
    parent = read(PARENT/'manifest.json')
    assert all(sha(PARENT/n) == h for n, h in parent['files'].items())
    b, bt = get(BASE); n, nt = get(RET/JOB)
    assert n['theta'] == b['theta'] and n['cash_budget_enabled'] is False
    transformed = compile_source(PACKAGE)
    worker_path = 'C:\\BTC5M-worker\\.lan_worker_v1\\staging\\' + PACKAGE.name
    for local, remote in [(str(PACKAGE).replace('\\','\\\\'),worker_path.replace('\\','\\\\')),
                          (str(PACKAGE),worker_path), (PACKAGE.as_posix(),worker_path.replace('\\','/'))]:
        transformed = transformed.replace(local, remote)
    assert n['clock_smoke']['transformed_source_sha256'] == hashlib.sha256(transformed.encode()).hexdigest()
    first = nt['pending_ticket_first']; t = first['t']
    observed = read(R/(STEM+'_OBSERVATION.json'))['first']
    for k in ('t','price','quantity','uncovered_filled_gap','excess_over_filled_gap','usable_pending_up'):
        previous.same_value(first[k], observed[k])
    keys = (*STREAMS, 'intent', 'money_rows', 'money_events', 'demand_rows', 'demand_events',
            'demand_owner_rows', 'demand_plan_rows', 'demand_maintenance_rows', 'profit_budget_rows')
    prefix = {k: [r for r in bt[k] if r['t'] < t] == [r for r in nt[k] if r['t'] < t] for k in keys}
    assert all(prefix.values()), [k for k,v in prefix.items() if not v]
    assert bt['opportunity_rows'] == nt['opportunity_rows']
    assert bt['opportunity_submissions'] == nt['opportunity_submissions']
    bp = next(p for p in bt['plans'] if p['t'] == t)
    np = next(p for p in nt['plans'] if p['t'] == t)
    assert np['operations'][:-1] == bp['operations'] == first['original_operations']
    assert next(d['state'] for d in bt['demand_rows'] if d['t'] == t) == first['state']
    assert len(nt['pending_ticket_submissions']) == 1
    op = np['operations'][-1]
    assert op['role'] == 'PASSIVE_PENDING_TICKET_COMPLETION' and op['qty'] == 15
    assert n['pending_ticket_first'] == first and n['pending_ticket_submissions'] == nt['pending_ticket_submissions']
    source = read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    books = {x['received_ms']: x for x in source['books']}
    active_key = nt['opportunity_submissions'][0]['key']
    terminal_t = min(r['t'] for r in nt['demand_owner_rows'] if r['key'] == active_key and r['state'] == 'TERMINAL' and r['filled'] > 0)
    for row in nt['pending_ticket_rows']:
        book = books[row['t']]
        assert book['source_ms'] <= row['t']
        previous.close(row['ask'], round(1-book['best_bid'],10))
        expected = decide(row['state'], row['original_operations'], row['price'], row['ask'], row['t'] >= terminal_t, crossing_owners)
        for k,v in expected.items():
            previous.same_value(v, row[k])
    assert sum(r['eligible'] for r in nt['pending_ticket_rows']) == 1
    factory = types.FunctionType(unbudgeted.auditor.__code__,
        dict(unbudgeted.auditor.__globals__, PACKAGE=PACKAGE, folder=lambda tag: RET/JOB), 'bound_no_cash_auditor')
    verified = factory()('active', n, nt)
    bridge = next(c for c in verified['receipts']['orders'] if c['key'] == op['key'])
    raw = [r for r in nt['demand_final']['full_raw_receipts'] if r['key'] == op['key'] and r['qty'] > 0]
    assert all(r['maker'] == 1 and r['contractPrice'] <= op['price'] + 1e-8 for r in raw)
    normal = geometry(b['final_inventory'], b['final_cost'])
    final = verified['terminal']
    delta = {k: final[k] - normal[k] for k in ('up','down','cost','up_net')}
    flow = types.FunctionType(previous.diagnose.__code__, dict(previous.diagnose.__globals__, STEM=STEM), 'ticket_flow_diagnose')
    difference = flow(dict(control=(b,bt), active=(n,nt)), delta)
    fd = read(R/(STEM+'_FLOW_DIAGNOSTIC.json'))
    fd['interpretation'] = 'Both branches already include the same V20 Active and cash gates OFF. This difference is one appended Passive15 plus its later OWN/native feedback; not fixed cash-budget competition.'
    dump('FLOW_DIAGNOSTIC', fd)
    replacements = []
    nplans = {p['t']:p for p in nt['plans']}
    for p in bt['plans']:
        old_down = [o for o in p['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
        new_down = [o for o in nplans[p['t']]['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
        if old_down and not new_down:
            for order in old_down:
                owner = next(c for c in bt['demand_final']['all_final_carriers'] if c['key']==order['key'])
                receipts = [r for r in bt['demand_final']['full_raw_receipts'] if r['key']==order['key'] and r['qty']>0]
                canonical = [dict(t=e['t'],qty=f['fill_increment']) for e in b['atomic_responsibility_events']
                             for f in e['fill_rows'] if f['key']==order['key'] and f['fill_increment']>0]
                replacements.append(dict(born_t=p['t'],order=order,owner=owner,raw_receipts=receipts,canonical=canonical))
    states_equal = bt['states'] == nt['states']
    replacement_proof = dict(every_confirmed_state_identical=states_equal,
                            canonical_flow_changed_buckets=difference['flow_changed_buckets'],
                            omitted_later_baseline_orders=replacements)
    if states_equal and len(replacements)==1:
        replacement = replacements[0]
        assert replacement['owner']['filled']==bridge['filled']==15
        previous.close(replacement['owner']['payment'],bridge['payment'])
        assert replacement['canonical']==[dict(t=bridge['first_canonical_t'],qty=15.)]
        replacement_proof.update(earlier_submission_ms=replacement['born_t']-t,
                                 accounting_equivalent_order_replacement=True,
                                 interpretation='The added order replaces a later baseline order. Its per-order payoff contribution is not incremental improvement vs baseline.')
    dump('REPLACEMENT',replacement_proof)
    baseline_path = path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),bt['states'],START,START+300000)
    direct_down = bridge['filled'] - bridge['payment'] - bridge['fees']
    direct_up = -bridge['payment'] - bridge['fees']
    post = read(R/(STEM+'_POSTCHECK.json'))
    assert post['status'] == 'PASS' and post['native_sha256'] == manifest['native_sha256']
    pins = read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert n['sizing_installation'] == pins
    assert all(sha(ROOT/'tools'/name) == p['before'] for name,p in pins.items())
    out = dict(status='COMPLETE', verification='PASS', new_native_jobs=1, reused_native_controls=1,
        local_native_jobs=0, model_fits=0, parameter_search=0, runtime_eligible=False,
        manifest_sha256=sha(PACKAGE/'manifest.json'), prefix=prefix, existing_active_unchanged=True,
        source=first, bridge_order=bridge, bridge_raw_receipts=raw,
        replacement=replacement_proof,baseline=dict(terminal=normal, trajectory=baseline_path, submits=b['submits'],
                      passive_submits=b['passive_native_submits'], active_submits=b['active_native_submits'], core_similarity=b['core_similarity']),
        candidate=verified, terminal_delta=delta, flow_diagnosis=difference,
        direct_vs_downstream=dict(direct_down=direct_down,direct_up=direct_up,
                                 downstream_down=delta['down']-direct_down, downstream_up=delta['up']-direct_up),
        limitations='One consumed market and one added Passive15. Pending UP is conditional exposure, not a promised fill. No target architecture or cross-market success claim.')
    dump('RESULT', out)
    print(json.dumps(dict(status='PASS', bridge=bridge, delta=delta, direct_vs_downstream=out['direct_vs_downstream'],
        baseline=dict(terminal=normal,both_positive_seconds=baseline_path['both_positive_seconds']),
        candidate=dict(terminal=final,both_positive_seconds=verified['trajectory']['both_positive_seconds'],submits=n['submits']))))


if __name__ == '__main__':
    main()
