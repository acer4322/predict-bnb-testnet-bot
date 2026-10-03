"""Coordinator-only receipt accounting and preregistered fixed-cohort evaluation."""
import argparse
import gzip
import hashlib
import json
import math
import random
import statistics as S
from collections import Counter, defaultdict
from pathlib import Path
from strict_completion import completion_errors
from owner_receipt_audit import reconcile_owners

P = Path(__file__).resolve().parent
R = P.parents[2]
RET = R / 'data/research/lan_worker_returns'
CLASSES = ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')


def load(p):
    return json.loads(p.read_bytes())


def load_trace(arm):
    name = 'clock_trace.json.gz' if (arm/'clock_trace.json.gz').exists() else 'failure_trace.json.gz'
    return json.loads(gzip.decompress((arm/name).read_bytes())), name


def save(p, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, indent=2, allow_nan=False), encoding='utf8')


def read_jobs(package):
    protocol = load(package / 'PROTOCOL.json')
    job = protocol['job_id']
    global_result = load(RET / job / 'RESULT.json')
    jobs = load(package / 'JOBS.json')
    lookup = {x['name']: x for x in global_result['completed']}
    if (package / 'REUSED_BATCH.json').exists():
        batch = load(package / 'REUSED_BATCH.json')
        reused_jobs = []
        source_jobs = {}
        for rec in batch['records']:
            pname = rec['package_name']
            if pname not in source_jobs:
                source_jobs[pname] = {j['name']: j for j in load(P/'stage'/pname/'JOBS.json')}
            j = dict(source_jobs[pname][rec['name']], result_job=rec['job_id'])
            oldroot = RET / rec['job_id'] / 'arms' / rec['name']
            for n, h in rec['files'].items():
                assert hashlib.sha256((oldroot / n).read_bytes()).hexdigest() == h, n
            reused_jobs.append(j)
            lookup.update({x['name']: x for x in load(RET/rec['job_id']/'RESULT.json')['completed']})
        jobs = [*reused_jobs, *jobs]
    elif (package / 'REUSED.json').exists():
        reuse = load(package / 'REUSED.json')
        oldpackage = P / 'stage/original_maker_layer_20261002_v1r5'
        j = next(j for j in load(oldpackage / 'JOBS.json')
                 if j['market_id'] == reuse['market_id'] and j['arm'] == reuse['arm'])
        j = dict(j, result_job=reuse['job_id'])
        oldroot = RET / reuse['job_id'] / reuse['path']
        for n, h in reuse['files'].items():
            assert hashlib.sha256((oldroot / n).read_bytes()).hexdigest() == h, n
        lookup.update({x['name']: x for x in load(RET / reuse['job_id'] / 'RESULT.json')['completed']})
        jobs = [j, *jobs]
    assert len({(j['market_id'], j['arm']) for j in jobs}) == len(jobs)
    return protocol, global_result, jobs, lookup


def metric(j, arm, rc, cls, sourcepin):
    raw = load(arm / 'result.json')
    clock = load(arm / 'execution_clock.json')
    errors = completion_errors(raw, clock, rc)
    assert not errors, errors
    inv = dict(UP=0., DOWN=0.)
    costs = dict(UP=0., DOWN=0.)
    maker = taker = fees = 0.
    prev_sequence = 0
    for receipt in clock['receipts']:
        assert receipt['side'] in (-1, 1) and receipt['maker'] in (0, 1)
        side = 'UP' if receipt['side'] == 1 else 'DOWN'
        price = receipt['price'] if side == 'UP' else 1 - receipt['price']
        qty = receipt['qty']
        assert math.isfinite(price) and 0 <= price <= 1 and math.isfinite(qty) and qty > 0
        assert receipt['receive_ts'] >= receipt['exchange_ts']
        assert receipt['sequence'] > prev_sequence
        prev_sequence = receipt['sequence']
        inv[side] += qty
        costs[side] += qty * price
        fees += receipt['fee']
        if receipt['maker']:
            maker += qty
        else:
            taker += qty
    cost = sum(costs.values())
    assert fees == 0
    for s in inv:
        assert abs(inv[s] - raw['final_inventory'][s]) < 1e-6
        assert abs(inv[s] - clock['our_inventory'][s]) < 1e-6
    assert abs(cost - raw['final_cost']) < 1e-6 and abs(cost - clock['our_cost']) < 1e-6
    native = clock['native']
    assert abs(native['position'] - inv['UP'] + inv['DOWN']) < 1e-6
    assert abs(inv['DOWN'] - native['balance'] - cost) < 1e-6
    assert abs(native['trading_volume'] - maker - taker) < 1e-6
    assert native['num_trades'] == len(clock['receipts']) and native['fee'] == 0
    assert raw['native_receipts'] == len(clock['receipts'])
    assert raw['submits'] == len(clock['carriers'])
    assert clock['actual_sha'] == clock['expected_sha'] == '033469835b44f94f1a022e419e79be61be72a9f2501ceea11b8e255b4824d145'
    settings = load(arm / 'ENGINE_SETTINGS.json')
    assert settings['queue_model'] == j['queue']
    assert settings['entry_latency_ms'] == settings['response_latency_ms'] == j['latency_ms']
    assert settings['tick'] == settings['lot'] == .01
    assert settings['maker_fee'] == settings['taker_fee'] == 0 and settings['exchange'] == 'PartialFill'
    assert settings['events_sha256'] == sourcepin['events_sha256']
    trace, trace_name = load_trace(arm)
    assert trace_name == 'clock_trace.json.gz'
    owner_audit = reconcile_owners(clock, trace)
    passive_first = {}
    active_ops = []
    linked_sources = []
    for p in trace['plans']:
        for op in p['operations']:
            if op['kind'] != 'NEW':
                continue
            if op['route'] == 'PASSIVE':
                passive_first.setdefault(op['parent_id'], p['t'])
            else:
                active_ops.append((p['t'], op))
                if op.get('source_terminal_key'):
                    source_key=op['source_terminal_key']
                    assert clock['carriers'][source_key]['route']=='PASSIVE'
                    linked_sources.append(source_key)
    exhaustive = len(linked_sources) == len(active_ops)
    conversion_frequency = len(set(linked_sources)) / raw['passive_carriers'] if exhaustive and raw['passive_carriers'] else (0. if not active_ops else None)
    actions = trace['native_actions']
    assert sum(a['kind'] in ('NEW', 'NEW_ACTIVE') for a in actions) == raw['submits']
    assert sum(a['kind'] == 'CANCEL' for a in actions) == raw['cancel_requests']
    assert len(trace['plans']) == clock['frames']
    plan_times = [a['t'] for a in trace['plans']]
    assert plan_times == sorted(plan_times)
    end = sourcepin.get('window_end_ms')
    after = dict(post_window_new=None, post_window_cancel=None,
                 post_window_exchange_receipts=None, post_window_exchange_fill_qty=None,
                 post_window_receive_receipts=None)
    if end is not None:
        er = [r for r in clock['receipts'] if r['exchange_ts'] > end * 1_000_000]
        after = dict(post_window_new=sum(a['kind'] in ('NEW', 'NEW_ACTIVE') and a['t'] > end for a in actions),
                     post_window_cancel=sum(a['kind'] == 'CANCEL' and a['t'] > end for a in actions),
                     post_window_exchange_receipts=len(er),
                     post_window_exchange_fill_qty=sum(r['qty'] for r in er),
                     post_window_receive_receipts=sum(r['receive_ts'] > end * 1_000_000 for r in clock['receipts']))
    avg = {s: costs[s] / inv[s] if inv[s] else 0. for s in inv}
    paired = min(inv.values())
    big = 'UP' if inv['UP'] >= inv['DOWN'] else 'DOWN'
    excess = inv[big] - paired
    locked = paired * (1 - sum(avg.values()))
    winner = j['winner']
    residual = excess * ((1. if winner == big else 0.) - avg[big]) if winner else None
    pnl = inv[winner] - cost if winner else None
    if pnl is not None:
        assert abs(locked + residual - pnl) < 1e-6
    return dict(market_id=j['market_id'], asset=j['asset'], arm=j['arm'],
                name=j['name'], result_job=j.get('result_job'),
                status='COMPLETE' if winner else 'UNKNOWN_OFFICIAL_NONBINARY',
                execution_valid=True, rc=rc, winner=winner, cls=cls,
                pnl=pnl, up=inv['UP'], down=inv['DOWN'], cost=cost,
                maker_fill_qty=maker, taker_fill_qty=taker,
                maker_fraction=maker / (maker + taker) if maker + taker else None,
                avg_up=avg['UP'], avg_down=avg['DOWN'], paired_cost=sum(avg.values()),
                paired_qty=paired, paired_cost_both_sides=sum(avg.values()) if paired else None,
                locked_pnl=locked, directional_residual_pnl=residual, excess_qty=excess,
                native_submits=raw['submits'], cancel_requests=raw['cancel_requests'],
                cancel_api_calls=sum(a['kind'] == 'CANCEL' and 'key' not in a for a in actions),
                cancel_log_note='Original CANCEL logging contains AuditBT API row plus keyed plan row; raw cancel_requests retained; API calls count only rows without key',
                receipt_count=len(clock['receipts']), passive_carriers=raw['passive_carriers'],
                active_carriers=raw['active_carriers'],
                active_birth_count=raw['active_birth_count'], unresolved_owners=0,
                active_order_roles=dict(Counter(op.get('role','UNSPECIFIED') for _,op in active_ops)),
                explicit_passive_to_active_children=len(linked_sources),
                explicit_passive_sources_converted=len(set(linked_sources)),
                passive_to_active_frequency=conversion_frequency,
                passive_to_active_frequency_status='EXHAUSTIVE_EXPLICIT_SOURCE_LINKS' if exhaustive else 'UNKNOWN_NO_COMPREHENSIVE_PASSIVE_SOURCE_LINK',
                active_after_same_parent_passive_count=sum(t>=passive_first.get(op['parent_id'],math.inf) for t,op in active_ops),
                conversion_note='Raw active_birth_count counts every ACTIVE carrier, including direct payoff/repair orders. It is not a literal conversion count. Only explicit source_terminal_key links identify individual passive source conversions; same-parent sequence is a diagnostic proxy.',
                raw_safety_gate=raw['safety_gate'],
                legacy_active_matches_opportunity=raw['safety_gate']['active_matches_opportunity'],
                all_old_gates_passed=raw['safety_gate']['pass'],
                receipt_accounting='PASS', receipt_owner_audit=owner_audit, frames=clock['frames'],
                events_sha256=settings['events_sha256'], engine=settings,
                path_process_elapsed_seconds=raw['elapsed_seconds'],
                temporal_audit=after)


def receipt_prefix(j, arm):
    """Confirmed prefix only: never substitutes for a complete replay valuation."""
    c = load(arm/'execution_clock.json')
    inv = dict(UP=0., DOWN=0.)
    costs = dict(UP=0., DOWN=0.)
    maker = taker = 0.
    for r in c['receipts']:
        s = 'UP' if r['side'] == 1 else 'DOWN'
        price = r['price'] if s == 'UP' else 1-r['price']
        assert math.isfinite(price) and math.isfinite(r['qty']) and r['qty'] > 0 and r['fee'] == 0
        inv[s] += r['qty']
        costs[s] += r['qty'] * price
        if r['maker']:
            maker += r['qty']
        else:
            taker += r['qty']
    cost = sum(costs.values())
    assert all(abs(inv[s]-c['our_inventory'][s]) < 1e-6 for s in inv)
    assert abs(cost-c['our_cost']) < 1e-6
    native=c['native']
    assert abs(native['position']-inv['UP']+inv['DOWN'])<1e-6
    assert abs(inv['DOWN']-native['balance']-cost)<1e-6
    assert abs(native['trading_volume']-maker-taker)<1e-6
    assert native['num_trades']==len(c['receipts']) and native['fee']==0
    trace, trace_name=load_trace(arm)
    owner_audit=reconcile_owners(c,trace)
    assert len(trace['plans'])==c['frames']
    avg = {s: costs[s]/inv[s] if inv[s] else 0. for s in inv}
    pair = min(inv.values())
    big = 'UP' if inv['UP'] >= inv['DOWN'] else 'DOWN'
    pnl = inv[j['winner']]-cost if j['winner'] else None
    locked = pair*(1-sum(avg.values()))
    residual = (inv[big]-pair)*((1. if big==j['winner'] else 0.)-avg[big]) if j['winner'] else None
    return dict(scope='Observed simulated receipt prefix only; excluded from formal complete-cohort gate',
                up=inv['UP'], down=inv['DOWN'], cost=cost,
                maker_fill_qty=maker, taker_fill_qty=taker,
                paired_cost=sum(avg.values()), locked_pnl=locked,
                directional_residual_if_no_further_activity=residual,
                pnl_if_no_further_activity=pnl, receipt_count=len(c['receipts']),
                unresolved_owners=sum(o['state']!='TERMINAL' for o in c['carriers'].values()),
                frames=c.get('frames'), source_updates=c.get('source_updates'),
                observed_prefix_owner_audit=owner_audit,sequence_source=trace_name,
                missing_source_updates=c.get('source_updates',0)-c.get('frames',0))


def summarize(rows, arms, expected):
    summary = {}
    for arm in arms:
        actual = [r for r in rows if r['arm'] == arm]
        execution = [r for r in actual if r.get('execution_valid')]
        valued = [r for r in execution if r['pnl'] is not None]
        groups = defaultdict(list)
        for r in valued:
            groups[r['cls']].append(r)
        stats = {}
        for c, g in groups.items():
            both = [r['paired_cost_both_sides'] for r in g if r['paired_cost_both_sides'] is not None]
            stats[c] = dict(n=len(g), pnl_mean=S.fmean(r['pnl'] for r in g),
                            paired_cost_mean_target_definition=S.fmean(r['paired_cost'] for r in g),
                            paired_cost_mean_both_sides=S.fmean(both) if both else None,
                            locked_pnl_mean=S.fmean(r['locked_pnl'] for r in g),
                            residual_pnl_mean=S.fmean(r['directional_residual_pnl'] for r in g),
                            maker_qty=sum(r['maker_fill_qty'] for r in g),
                            taker_qty=sum(r['taker_fill_qty'] for r in g),
                            active_births=sum(r['active_birth_count'] for r in g),
                            explicit_passive_sources_converted=sum(r['explicit_passive_sources_converted'] for r in g),
                            conversion_frequency_unknown_markets=sum(r['passive_to_active_frequency'] is None for r in g))
        gate = dict(status='NOT_EVALUATED_INCOMPLETE', EACH=None, CI_lower_gt_zero=None)
        descriptive = {}
        if valued:
            pnls = [r['pnl'] for r in valued]
            descriptive['pnl_mean'] = S.fmean(pnls)
            descriptive['paired_cost_mean_target_definition'] = S.fmean(r['paired_cost'] for r in valued)
            mq = sum(r['maker_fill_qty'] for r in execution)
            tq = sum(r['taker_fill_qty'] for r in execution)
            descriptive.update(maker_fraction_qty_weighted=mq / (mq + tq) if mq + tq else None,
                               maker_fill_qty=mq, taker_fill_qty=tq,
                               active_birth_count=sum(r['active_birth_count'] for r in execution),
                               active_orders_per_passive_carrier=sum(r['active_birth_count'] for r in execution) / max(1, sum(r['passive_carriers'] for r in execution)),
                               explicit_passive_sources_converted=sum(r['explicit_passive_sources_converted'] for r in execution),
                               active_after_same_parent_passive_count=sum(r['active_after_same_parent_passive_count'] for r in execution),
                               conversion_frequency_unknown_markets=sum(r['passive_to_active_frequency'] is None for r in execution),
                               passive_to_active_frequency=(sum(r['explicit_passive_sources_converted'] for r in execution) / max(1,sum(r['passive_carriers'] for r in execution))) if all(r['passive_to_active_frequency'] is not None for r in execution) else None)
        if len(execution) == expected and valued:
            rng = random.Random(20261002)
            means = sorted(S.fmean(rng.choices(pnls, k=len(pnls))) for _ in range(10000))
            ci = [means[250], means[9750]]
            descriptive['bootstrap95'] = ci
            if len(valued) == expected and all(c in stats for c in CLASSES) and 'UNKNOWN' not in groups:
                v = [stats[c]['pnl_mean'] for c in CLASSES]
                neg = [x for x in v if x < 0]
                pos = [x for x in v if x > 0]
                each = all(x > 0 for x in v) or (len(neg) == 1 and len(pos) == 2 and abs(neg[0]) < min(pos))
                gate = dict(status='PASS' if each and ci[0] > 0 else 'FAIL', EACH=each,
                            CI_lower_gt_zero=ci[0] > 0, bootstrap95=ci, cohort_n=expected)
            else:
                gate['status'] = 'UNKNOWN_LABEL_OR_CLASSIFICATION'
        summary[arm] = dict(expected=expected, observed=len(actual), execution_valid=len(execution),
                            valued=len(valued), unknown_winner=len(execution)-len(valued),
                            groups=stats, descriptive=descriptive, gate=gate,
                            sensitivity_only=arm != 'BASE',
                            raw_legacy_failure_count=sum(r.get('legacy_active_matches_opportunity') is False for r in execution))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('package')
    a = ap.parse_args()
    package = P / 'stage' / a.package
    protocol, global_result, jobs, completed = read_jobs(package)
    job = protocol['job_id']
    asset = jobs[0]['asset']
    inputs = {r['market_id']: r for r in load(package / 'INPUTS.json')[asset]}
    if asset == 'BTC':
        cloud = load(R / 'docs/research_specs/results/CLOUD_LAB_RESULTS_20261002.json')['FAV_TAKER']
        classes = {r['market']: r['cls'] for r in cloud}
        assert len(classes) == 185 and set(inputs) == set(classes)
        class_source = 'FROZEN_CLOUD_A_185; offline only; two late-book fallback warnings retained'
    else:
        classification = load(P / 'ETH_CLASSIFICATION.json')
        classes = {r['market_id']: r['cls'] for r in classification['records']}
        assert set(inputs) == set(classes)
        class_source = classification['method']
    rows = []
    missing = []
    for j in jobs:
        result_job = j.get('result_job', job)
        arm = RET / result_job / 'arms' / j['name']
        if j['name'] not in completed or not (arm / 'result.json').exists():
            missing.append(j['name'])
            continue
        try:
            meta = load(package / 'META' / (str(j['market_id']) + '.json'))
            sourcepin = dict(inputs[j['market_id']], window_end_ms=meta['window_end_ms'])
            row = metric(j, arm, completed[j['name']]['rc'], classes[j['market_id']], sourcepin)
            row.update(result_job=result_job, classification_source=class_source)
        except Exception as exc:
            raw = load(arm / 'result.json')
            row = dict(market_id=j['market_id'], asset=asset, arm=j['arm'], name=j['name'],
                       result_job=result_job, status=('UNKNOWN_EOF_CENSORED' if 'EOF_CENSORED' in raw.get('error','') else 'INVALID_EXECUTION_OR_ACCOUNTING'),
                       execution_valid=False, error=repr(exc), rc=completed[j['name']].get('rc'),
                       raw_status=raw.get('status'), raw_error=raw.get('error'),
                       raw_safety_gate=raw.get('safety_gate'), winner=j['winner'],
                       cls=classes[j['market_id']], pnl=None,
                       classification_source=class_source)
            try:
                row['receipt_prefix'] = receipt_prefix(j, arm)
            except Exception as prefix_error:
                row['receipt_prefix_error'] = repr(prefix_error)
        rows.append(row)
        save(P / 'metrics' / job / (j['name'] + '.json'), row)
    expected = len(inputs)
    source_jobs=sorted({j.get('result_job',job) for j in jobs})
    elapsed_by_job={name:load(RET/name/'RESULT.json')['elapsed_seconds'] for name in source_jobs}
    report = dict(job_id=job, asset=asset, worker_status=global_result['status'],
                  latest_job_worker_elapsed_seconds=global_result['elapsed_seconds'],
                  productive_jobs_worker_elapsed_seconds=elapsed_by_job,
                  worker_elapsed_seconds=sum(elapsed_by_job.values()), expected_markets=expected,
                  expected_paths=len(jobs), observed=len(rows), missing=missing,
                  status_counts=dict(Counter(r['status'] for r in rows)), rows=rows,
                  summary=summarize(rows, protocol['arms'], expected),
                  classification_source=class_source,
                  classification_warnings=[2807895, 2809188] if asset == 'BTC' else [],
                  Target_105_reference_pair_cost=dict(NO_FLIP=1.023, FALSE_FLIP=.980, TRUE_FLIP=1.005,
                    source='preregistered historical reference; cohorts and costs differ; not paired causal evidence'),
                  fits=0, live_changes=0, shadow_test_started=False)
    save(P / (a.package + '_ANALYSIS.json'), report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('rows', 'missing')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
