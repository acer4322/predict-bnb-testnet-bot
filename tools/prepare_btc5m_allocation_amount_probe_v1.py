"""Prepare an offline amount-learning task on unchanged consumed event anchors."""
import collections
import json
import math
import shutil
from audit_btc5m_target_core_loop_topology_v1 import ROOT, R, read, sha

PACKAGE = ROOT / '.lan_worker_v1/allocation_amount_probe_20260913_v1'
STEM = 'BTC5M_ALLOCATION_AMOUNT_PROBE_V1_20260913'


def cash(legs, weak):
    total = math.fsum(a['shares'] * a['price'] for a in legs)
    amount = math.fsum(a['shares'] * a['price'] for a in legs if a['side'] == weak)
    assert total > 0
    return amount / total, math.log1p(total)


def main():
    source_package = ROOT / '.lan_worker_v1/concurrent_flow_probe_20260913_v1'
    sm = read(source_package / 'manifest.json')
    assert sha(source_package / 'dataset.json') == sm['files']['dataset.json']
    original = read(source_package / 'dataset.json')
    markets = {}
    for item in original['sources']:
        path = ROOT / item['path']; assert sha(path) == item['sha256']
        source = read(path); by = collections.defaultdict(list)
        for a in source['targetActions']:
            assert a['quote_type'] == 'BID'
            by[a['event_ms']].append(a)
        markets[source['market']['market_id']] = by
    rows = []; prior = {}
    common = ['phase','strong_mid','spread','strong_depth','book_age_log','net_fraction','gross_log']
    for old in original['rows']:
        r = dict(old); by = markets[r['market']]
        weak = 'DOWN' if r['strong_side'] == 'UP' else 'UP'
        last_fraction, last_cash = cash(by[r['last_fill_bucket']], weak)
        before = prior.get(r['market'])
        if before and before['next_fill_bucket'] == r['last_fill_bucket']:
            previous_fraction, previous_cash = cash(by[before['last_fill_bucket']], weak)
        else:
            previous_fraction = previous_cash = -1.
        past = [a for t, legs in by.items() if t < r['anchor_ms'] for a in legs]
        cost = math.fsum(a['shares'] * a['price'] for a in past)
        inv = {s:math.fsum(a['shares'] for a in past if a['side']==s) for s in ('UP','DOWN')}
        assert cost > 0 and inv[r['strong_side']] > inv[weak]
        state = {k:r['features'][k] for k in common}
        state.update(last_weak_cash_fraction=last_fraction, last_cash_log=last_cash,
                     prior_weak_cash_fraction=previous_fraction, prior_cash_log=previous_cash)
        money = dict(cost_log=math.log1p(cost), strong_payoff_per_cost=(inv[r['strong_side']]-cost)/cost,
                     weak_payoff_per_cost=(inv[weak]-cost)/cost)
        fraction, amount = cash(by[r['next_fill_bucket']], weak)
        r.update(amount_state=state, amount_money=money,
                 amount_labels=dict(weak_cash_fraction=fraction, log_total_cash=amount))
        rows.append(r); prior[r['market']] = r
    assert len(rows) == 1097
    assert not PACKAGE.exists(), 'immutable package exists'
    PACKAGE.mkdir()
    payload = dict(rows=rows, sources=original['sources'], state_features=list(rows[0]['amount_state']), money_features=list(rows[0]['amount_money']))
    (PACKAGE / 'dataset.json').write_text(json.dumps(payload, indent=2)+'\n', encoding='utf-8')
    shutil.copy2(ROOT / 'tools/run_btc5m_allocation_amount_probe_v1.py', PACKAGE / 'worker.py')
    params = read(ROOT / '.lan_worker_v1/target_whole_market_memory_20260912_v1/manifest.json')['model_parameters']
    manifest = dict(version=STEM, rows=1097, market_count=8, no_native=True, max_threads=4,
        parent_dataset_sha256=sha(source_package/'dataset.json'), model_parameters=params,
        targets=['weak_cash_fraction','log_total_cash'], models=['INTERCEPT','EVENT2_MEAN','HISTORY_STATE','HISTORY_MONEY'],
        comparisons=[['HISTORY_STATE','EVENT2_MEAN'],['HISTORY_MONEY','HISTORY_STATE']],
        fixed_design='Same eight LOMO plus original TRAIN2 forward; fixed two regressors per model, no tuning or weight export.',
        event_baseline='Two physical-side tokens, context mean shrunk by two train-global pseudo-observations; unseen contexts use train-global means.',
        feature_design='STATE: last two physical tokens, past bucket amount/fraction, strict-past public context and cumulative quantities. MONEY adds current cumulative cost and both conditional payoffs per cost. No terminal ratio, future direction or private pending input.',
        prediction_domain='Clip fraction to[0,1], log1p cash to>=0; report both, do not convert a predicted fraction alone into an order.',
        gate='For each target: >=6/8 LOMO MSE wins, positive LOMO median and forward market median, lower pooled forward MSE and nonworse MAE. Forward BOTH-only fraction MSE and MAE must not worsen. All conditions required.',
        limits=['Conditioned on next observed fill bucket; this is not a timing, HOLD, desired inventory or submission label.',
                'Target event-time anatomy, not observer-time availability. Private receipts and unfilled orders remain unknown.',
                'Consumed eight markets with previous reuse, not fresh validation. First-observed/crossing subsets are outcomes only.',
                'Better forecasts do not demonstrate closed-loop repair, economy, independent intent, or causality. No actor/native promotion.'],
        files={p.name:sha(p) for p in PACKAGE.iterdir()})
    for path in (PACKAGE/'manifest.json', R/(STEM+'_PREREGISTERED.json')):
        path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    wave = dict(progress_artifact='data/research/ALLOCATION_AMOUNT_PROBE_PROGRESS_20260913.json', jobs=[dict(
        job_id='allocation-amount-probe-20260913-v1', argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/worker.py'],
        cwd='.', max_threads=4, min_free_ram_gb=6, max_start_cpu_pct=90, required_artifact='result.json')])
    (R/'allocation_amount_probe_wave_20260913.json').write_text(json.dumps(wave, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status='PREPARED', rows=len(rows), targets=manifest['targets'], state_features=payload['state_features'], money_features=payload['money_features'])))


if __name__ == '__main__': main()
