"""Observed Target strong-side adds and dual-route weak-side service.

No HFT or fitting. Same-second mixed routes receive allocation bounds rather
than invented FIFO route order. FIFO lot identity remains an audit convention.
"""
from __future__ import annotations
import collections
import json
from pathlib import Path
from audit_btc5m_target_core_loop_topology_v1 import (
    ROOT, R, RET, MIDS, SIDES, EPS, Ledger, read, sha, side_of, FROZEN)

STEM = 'BTC5M_TARGET_DUAL_ROUTE_SURPLUS_LOOP_V1_20260912'
ROUTES = ('PASSIVE', 'ACTIVE')


def target_batches(source):
    by = collections.defaultdict(list)
    seen = set()
    for a in source['targetActions']:
        assert a['quote_type'] == 'BID' and a['side'] in SIDES
        assert a['role'] in ('MAKER','TAKER') and a['shares'] > 0 and 0 < a['price'] < 1
        assert a['source_leg_id'] not in seen
        seen.add(a['source_leg_id'])
        assert int(a['event_ms']) % 1000 == 0
        by[int(a['event_ms'])].append(dict(side=a['side'], route='PASSIVE' if a['role']=='MAKER' else 'ACTIVE',
            qty=float(a['shares']), source_leg_id=a['source_leg_id'], price=a['price']))
    return by


def own_batches(doc):
    by = collections.defaultdict(list)
    assert doc['status']=='COMPLETE' and doc['safety_gate']['pass']
    for ev in doc['atomic_responsibility_events']:
        for fr in ev['fill_rows']:
            by[int(ev['t'])//1000*1000].append(dict(side=fr['side'],route=fr['route'],qty=fr['fill_increment'],key=fr['key']))
    return by


def bounds(portion, route_qty, total):
    assert -EPS <= portion <= total+EPS and -EPS <= route_qty <= total+EPS
    return dict(lower=max(0., portion-(total-route_qty)), upper=min(portion,route_qty))


def same_summary(actual, expected):
    assert actual.keys()==expected.keys()
    for k,v in actual.items():
        if isinstance(v,dict):
            same_summary(v,expected[k])
        elif isinstance(v,float):
            assert abs(v-expected[k])<1e-7, (k,v,expected[k])
        else:
            assert v==expected[k], (k,v,expected[k])


def analyze(by):
    ledger = Ledger()
    events, lots, epochs = [], {}, []
    current = None
    total_bounds = {r:{'lower':0.,'upper':0.} for r in ROUTES}
    role_counts = collections.Counter()
    for t, legs in sorted(by.items()):
        qty = {s:sum(x['qty'] for x in legs if x['side']==s) for s in SIDES}
        rq = {s:{r:sum(x['qty'] for x in legs if x['side']==s and x['route']==r) for r in ROUTES} for s in SIDES}
        for s in SIDES:
            assert abs(sum(rq[s].values())-qty[s])<1e-7
        before = side_of(ledger.totals())
        ev = ledger.process_batch(t,qty['UP'],qty['DOWN'])
        after = side_of(ledger.totals())
        pays = {s:sum(p['qty'] for p in ev['payments'] if p['fill_side']==s) for s in SIDES}
        repair_bounds = {r:{'lower':0.,'upper':0.} for r in ROUTES}
        for s in SIDES:
            for r in ROUTES:
                b = bounds(pays[s],rq[s][r],qty[s])
                for k in b:
                    repair_bounds[r][k] += b[k]
                    total_bounds[r][k] += b[k]
        certain_routes = [r for r in ROUTES if repair_bounds[r]['lower']>EPS]
        possible_routes = [r for r in ROUTES if repair_bounds[r]['upper']>EPS]
        role_counts['payment_clocks'] += bool(ev['payments'])
        for r in ROUTES:
            role_counts[r+'_certain_payment_clocks'] += r in certain_routes
        role_counts['both_routes_certain_same_clock'] += len(certain_routes)==2
        # Route-to-lot attribution also has bounds; no fractional split assumption.
        for p in ev['payments']:
            lot = lots[p['responsibility_id']]
            route_bounds = {r:bounds(p['qty'],rq[p['fill_side']][r],qty[p['fill_side']]) for r in ROUTES}
            lot['payments'].append(dict(t=t,qty=p['qty'],remaining_after=p['remaining_after'],route_bounds=route_bounds))
            for r in ROUTES:
                lot['route_lower'][r] += route_bounds[r]['lower']
                lot['route_upper'][r] += route_bounds[r]['upper']
        for b in ev['births']:
            lots[b['responsibility_id']] = dict(id=b['responsibility_id'],side=b['side'],born_t=t,qty=b['qty'],
                route_lower={r:0. for r in ROUTES},route_upper={r:0. for r in ROUTES},payments=[])
        # Pure one-side strong-side add, with a preexisting nonzero same-side gap.
        clean_add = (before==after and before!='FLAT' and qty[before]>EPS
                     and qty['DOWN' if before=='UP' else 'UP']<=EPS and not ev['payments'])
        keeps = before==after and before!='FLAT'
        item = dict(t=t,before=before,after=after,fill_qty=qty,route_qty=rq,
            payment_qty=sum(pays.values()),repair_route_bounds=repair_bounds,
            certain_repair_routes=certain_routes,possible_repair_routes=possible_routes,
            clean_strong_add=clean_add,birth=bool(ev['births']),
            completed_fifo_ids=[p['responsibility_id'] for p in ev['payments'] if p['remaining_after']<=EPS],
            outstanding_before=ev['outstanding_before'],outstanding_after=ev['outstanding_after'])
        events.append(item)
        # Crossing clock is deliberately excluded from either stable episode.
        if not keeps:
            current = None
        else:
            if current is None:
                current = dict(side=before,events=[])
                epochs.append(current)
            current['events'].append(item)
    assert ledger.summary()['pass']
    qualifying = []
    patterns = []
    for epoch in epochs:
        es = epoch['events']
        adds = [e for e in es if e['clean_strong_add']]
        passive = [e for e in es if 'PASSIVE' in e['certain_repair_routes']]
        active = [e for e in es if 'ACTIVE' in e['certain_repair_routes']]
        epoch.update(start=es[0]['t'],end=es[-1]['t'],clean_add_clocks=len(adds),
                     passive_payment_clocks=len(passive),active_payment_clocks=len(active))
        completions = [e for e in es if e['completed_fifo_ids']]
        first_complete = completions[0]['t'] if completions else None
        epoch.update(completed_fifo_lots=sum(len(e['completed_fifo_ids']) for e in completions),
            first_fifo_completion=first_complete,
            clean_adds_after_first_completion=sum(e['t']>first_complete for e in adds) if first_complete is not None else 0,
            active_payments_after_first_completion=sum(e['t']>first_complete for e in active) if first_complete is not None else 0,
            passive_payments_after_first_completion=sum(e['t']>first_complete for e in passive) if first_complete is not None else 0)
        if len(adds)>=2 and passive and active:
            qualifying.append(epoch)
        # Adjacent relevant event runs; BOTH and mixed add/payment stay explicit.
        runs = []
        for e in es:
            if e['clean_strong_add']:
                label = 'ADD'
            elif len(e['possible_repair_routes'])==1 and e['certain_repair_routes']==e['possible_repair_routes']:
                label = e['certain_repair_routes'][0]
            elif e['possible_repair_routes']:
                label = 'MIXED_OR_UNCERTAIN'
            else:
                label = 'OTHER'
            if not runs or runs[-1]['label']!=label:
                runs.append(dict(label=label,events=[]))
            runs[-1]['events'].append(e)
        for a,b,c in zip(runs,runs[1:],runs[2:]):
            labels = [x['label'] for x in (a,b,c)]
            if labels in [['PASSIVE','ADD','ACTIVE'],['ACTIVE','ADD','PASSIVE'],['PASSIVE','ACTIVE','PASSIVE']]:
                patterns.append(dict(pattern=' -> '.join(labels),side=epoch['side'],
                    events=[a['events'][-1],b['events'][0],c['events'][0]]))
    dual_lots = [v for v in lots.values() if all(v['route_lower'][r]>EPS for r in ROUTES)]
    result = dict(atomic_summary=ledger.summary(),route_counts=dict(role_counts),repair_qty_bounds=total_bounds,
        stable_epochs=len(epochs),qualifying_stable_epochs=len(qualifying),
        qualifying_events=sum(len(e['events']) for e in qualifying),
        qualified_epoch_summaries=[{k:v for k,v in e.items() if k!='events'} for e in qualifying],
        qualified_epoch_witnesses=qualifying,
        pattern_counts=dict(collections.Counter(p['pattern'] for p in patterns)),patterns=patterns,
        fifo_lots_with_both_routes_guaranteed=len(dual_lots),dual_route_lot_witnesses=dual_lots,
        qualifying_epochs_continue_add_after_completion=sum(e['clean_adds_after_first_completion']>0 for e in qualifying),
        qualifying_epochs_continue_both_routes_after_completion=sum(e['active_payments_after_first_completion']>0 and e['passive_payments_after_first_completion']>0 for e in qualifying),
        events=events)
    return result


def main():
    assert bounds(100.,60.,100.)==dict(lower=60.,upper=60.)
    assert bounds(40.,60.,100.)==dict(lower=0.,upper=40.)
    assert bounds(80.,60.,100.)==dict(lower=40.,upper=60.)
    previous = read(R/'BTC5M_TARGET_CORE_LOOP_TOPOLOGY_AUDIT_V1_20260912.json')
    out = dict(version=STEM,offline_only=True,native_jobs_submitted=0,parameter_fitting=False,
        hypothesis='During repeated same-side net-exposure adds, opposite-side partial payments use both Passive and Active within the same stable observed surplus episode.',
        initial_epoch_definition_fixed_before_results=True,
        completion_continuation_analysis='Exploratory follow-up after the initial route/epoch results; no fitted thresholds.',
        pattern_refinement='Mixed or uncertain repair routes excluded from pure-route pattern labels before final reporting.',
        definitions={'stable_epoch':'Consecutive observed clocks whose before/after surplus side is identical and nonflat; crossing clocks excluded.',
            'qualifying_epoch':'At least two clean same-side add clocks and at least one guaranteed Passive payment and one guaranteed Active payment.',
            'route_bounds':'For allocated portion p, route quantity r, total same-side fill q: [max(0,p-(q-r)), min(p,r)].',
            'pattern':'Adjacent runs of event categories within a stable epoch, no fixed seconds or lookahead side selection.',
            'shared_lot':'FIFO reconstruction convention, not private Target responsibility IDs.'},
        target={},current_own={},provenance=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p)) for p in
            (FROZEN, Path(__file__).resolve(), ROOT/'tools/audit_btc5m_target_core_loop_topology_v1.py',
             R/'BTC5M_TARGET_CORE_LOOP_TOPOLOGY_AUDIT_V1_20260912.json')])
    for mid in MIDS:
        bundle='open_funding_recovery_train_20260911_v3' if mid in MIDS[:3] else 'v20_consumed_btc5_transfer5_20260912_v1'
        p=ROOT/'.lan_worker_v1'/bundle/f'input_{mid}.json.gz'
        result=analyze(target_batches(read(p)))
        if mid==2026085:
            reversed_legs={t:list(reversed(v)) for t,v in target_batches(read(p)).items()}
            alt=analyze(reversed_legs)
            for k in ('route_counts','pattern_counts','qualifying_stable_epochs','fifo_lots_with_both_routes_guaranteed'):
                assert alt[k]==result[k],k
        expected=previous['historical8'][str(mid)]['target']['atomic_summary']
        same_summary(result['atomic_summary'],expected)
        out['target'][str(mid)]=result
        out['provenance'].append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
    for arm in ('pa','aa','pd','ad'):
        p=RET/f'core-mech-2026085-{arm}-20260912-v1/result.json'
        result=analyze(own_batches(read(p)))
        expected=previous['current_factorial'][arm]['own_common_resolution']['atomic_summary']
        for k in ('total_fill','outstanding','births','completed','same_clock_repair_then_birth'):
            if isinstance(expected[k],dict):
                assert all(abs(result['atomic_summary'][k][s]-expected[k][s])<1e-7 for s in SIDES)
            else:
                assert result['atomic_summary'][k]==expected[k]
        out['current_own'][arm]=result
        out['provenance'].append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
    out['limitations']=[
        'Observed acquisitions from zero initial inventory; no hidden orders, cancels, no-fill universe or official Target holdings.',
        'Same-second fills cannot prove concurrent live orders, exact within-second sequencing, or Passive failure before Active.',
        'No per-route proportional allocation is assumed; certain lower bounds can undercount mixed-route lot service.',
        'Stable surplus side is observed inventory, not identified independent thesis or alpha.',
        'FIFO lot service evidence is conditional on imposed accounting convention.',
        'Eight already-consumed markets; counts describe these markets and are not independent trials.',
        'Current OUR four arms only have zero or one exact Active intervention; absence of full dual-route loops is not a fair universal rejection of Active capability.']
    out['verification']=dict(target_atomic_parity_all8=True,own_common_resolution_parity_all4=True,
        no_proportional_route_allocation=True,source_ids_unique=True,
        allocation_bound_examples_pass=True,within_second_leg_order_invariant_2026085=True)
    (R/(STEM+'.json')).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({group:{k:{key:v[key] for key in ('route_counts','qualifying_stable_epochs','pattern_counts','fifo_lots_with_both_routes_guaranteed')} for k,v in out[group].items()} for group in ('target','current_own')},ensure_ascii=False))


if __name__=='__main__':
    main()
