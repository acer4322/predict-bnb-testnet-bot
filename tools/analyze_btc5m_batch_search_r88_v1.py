"""Read-only R88 artifact analysis and round-2 preparation. No native dispatch.

Run from the repository root. Original packages and worker returns are immutable.
Uses only Python standard-library operations on small saved research artifacts.
"""
from __future__ import annotations
import collections
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import statistics
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'data/research/btc5m_batch_search_20260921_r88'
JOB = 'btc5m-batch-search-20260921-r88'
RETURNS = ROOT / 'data/research/lan_worker_returns' / JOB
OUT = ROOT / 'data/research/btc5m_batch_search_20260921_r88_analysis_round2'
NEXT_JOB = JOB + '-b02'
THROUGH = 36
EPS = 1e-7


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def rel(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def write_once(path, value):
    data = value.encode('utf-8') if isinstance(value, str) else canonical(value)
    with Path(path).open('xb') as stream:
        stream.write(data)


def check_tree(root, entries):
    for name, expected in entries.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha(path) != expected:
            raise ValueError('Missing or mismatched pinned file: ' + name)
    return len(entries)


def normalized(params):
    p = dict(params)
    if p['history'] == 'age':
        p['failure_weight'] = 0.
    elif p['history'] == 'attempts':
        p['age_weight'] = 0.
    return p


def trace_for(number, case, study, manifest_hash):
    config = normalized(study['trials'][number]['params'])
    key = hashlib.sha256(canonical([manifest_hash, config, case])).hexdigest()
    path = RETURNS / 'cache' / (key + '.json.gz')
    meta = load(RETURNS / 'cache' / (key + '.meta.json'))
    if meta['state'] != 'COMPLETE' or meta['trace_sha256'] != sha(path):
        raise ValueError('Trace cache mismatch')
    return path, json.loads(gzip.decompress(path.read_bytes()))


def slim_trace(path, trace, sample_seconds):
    first = trace['decisions'][0]
    start = first['ms'] - round(first['state']['elapsed_seconds'] * 1000)
    final = trace['final']['observation']
    owner_keys = {str(o['key']) for o in final['owners']}
    sampled = []
    for second in sample_seconds:
        d = min(trace['decisions'], key=lambda row: abs(row['state']['elapsed_seconds'] - second))
        sampled.append(dict(elapsed_seconds=d['state']['elapsed_seconds'], inv=d['state']['inv'],
            payoff=d['state']['payoff'], executed=d['executed'], attempt_observer=d.get('attempt_observer')))
    return dict(path=rel(path), sha256=sha(path), replay_status=trace['replay']['status'],
        final={k: final[k] for k in ('elapsed_seconds', 'remaining_seconds', 'inv', 'cost', 'owners', 'payoff')},
        source_boundary=dict(censored=trace['censored'], endpoint_reaches_expiry=trace['endpoint_reaches_expiry']),
        final_receipts=[dict(elapsed_seconds=round((r['receive_ts']/1e6-start)/1000,6),
            key=r['order_id'], side='UP' if r['side']==1 else 'DOWN',
            price=r['price'] if r['side']==1 else 1.-r['price'], qty=r['qty'], maker=r['maker'])
            for r in trace['raw_receipts'][-6:]],
        unresolved_owner_events=[dict(e, elapsed_seconds=(e['ms']-start)/1000)
            for e in trace['events'] if str(e.get('key')) in owner_keys], sampled_decisions=sampled)


def main():
    if OUT.exists():
        raise RuntimeError('Analysis directory already exists; do not overwrite this frozen analysis')
    manifest = load(PACKAGE / 'MANIFEST.json')
    manifest_hash = sha(PACKAGE / 'MANIFEST.json')
    package_count = check_tree(PACKAGE, manifest['files'])
    result = load(RETURNS / 'RESULT.json')
    if result['status'] != 'COMPLETE_UNPROMOTED' or result['manifest_sha256'] != manifest_hash:
        raise ValueError('Unexpected completed result identity')
    artifact_count = check_tree(RETURNS, result['artifacts'])
    exact = load(RETURNS / 'EXACT_CONTROL.json')
    if len(exact['equal']) != 10 or not all(exact['equal'].values()):
        raise ValueError('Original observer control failed')
    study = load(RETURNS / 'STUDY.json')
    spec = load(PACKAGE / 'SEARCH_SPEC.json')
    if study['spec'] != spec or study['spec_hash'] != hashlib.sha256(canonical(spec)).hexdigest():
        raise ValueError('Frozen search specification mismatch')
    if len(study['trials']) != 12 or any(t['state'] != 'COMPLETE' for t in study['trials']):
        raise ValueError('Expected exactly twelve completed trials')
    short = load(RETURNS / 'FROZEN_SHORTLIST.json')
    if short['study_sha256'] != sha(RETURNS / 'STUDY.json') or not short['screen_only_selection']:
        raise ValueError('Shortlist did not pin the completed screen study')
    robust = load(RETURNS / 'ROBUSTNESS.json')
    if set(map(str, short['trials'])) != set(robust):
        raise ValueError('Shortlist and robustness results differ')
    execution = load(RETURNS / 'EXECUTION_PLAN.json')
    screen_names = {f'{a}_L{b}_P{c}' for a,b,c in execution['screen']}
    equivalent = collections.defaultdict(list)
    trial_rows = []
    for t in study['trials']:
        rows = t['metrics']['rows']
        if {r['scenario'] for r in rows} != screen_names:
            raise ValueError('Non-screen scenario was fed back into the sampler')
        expected = [t['metrics']['candidate'][k] for k in spec['objectives']]
        if expected != t['objectives']:
            raise ValueError('Saved objectives differ from the preregistered screen')
        if any(n >= t['number'] for n in t['completed_results_used']):
            raise ValueError('Non-past sampling parent')
        equivalent[tuple(r['candidate']['physical_action_hash'] for r in rows)].append(t['number'])
        trial_rows.append(dict(number=t['number'], params=t['params'], effective_params=normalized(t['params']),
            objectives=t['objectives'], sampling=t['sampling'], parent_trial=t['parent_trial'],
            closed_screen_cases=t['metrics']['candidate']['closed'],
            pending_screen_owners=t['metrics']['candidate']['total_pending']))
    candidates = {}
    for number, item in robust.items():
        comp = item['comparison']; rows = comp['rows']; common = [r for r in rows if r['common_closed']]
        original = [r for r in rows if r['baseline']['closed'] and r['baseline']['floor'] >= 0]
        deltas = [r['delta_floor'] for r in rows]
        for r in rows:
            for arm in ('baseline', 'candidate'):
                v = r[arm]
                if abs(v['floor'] - min(v['payoff'].values())) > 1e-7:
                    raise ValueError('Branch/floor identity mismatch')
                if v['pending_conservative_floor'] > v['floor'] + 1e-7:
                    raise ValueError('Pending lower bound exceeds observed floor')
            if not math.isclose(r['delta_floor'],r['candidate']['floor']-r['baseline']['floor'],abs_tol=1e-7):
                raise ValueError('Pair delta mismatch')
        candidates[number] = dict(effective_params=normalized(item['params']), comparison=comp,
            continuation_gates=item['continuation_gates'], continuation_candidate=item['continuation_candidate'],
            common_closed=len(common), common_closed_better=sum(r['delta_floor']>EPS for r in common),
            common_closed_worse=sum(r['delta_floor']<-EPS for r in common),
            common_closed_same=sum(abs(r['delta_floor'])<=EPS for r in common),
            original_positive_preserved=sum(r['candidate']['closed'] and r['candidate']['floor']>=0 for r in original),
            closed_both_positive_scenarios=sum(r['candidate']['closed'] and r['candidate']['floor']>0 for r in rows),
            median_delta_floor=statistics.median(deltas), total_delta_floor=sum(deltas),
            best_delta_scenario=max(rows,key=lambda r:r['delta_floor'])['scenario'],
            total_delta_without_best=sum(deltas)-max(deltas),
            mean_delta_without_best=(sum(deltas)-max(deltas))/(len(rows)-1),
            receipts_change_pct=100*(comp['candidate']['mean_receipts']/comp['baseline']['mean_receipts']-1),
            new_orders_change_pct=100*(comp['candidate']['mean_new_orders']/comp['baseline']['mean_new_orders']-1),
            spend_change_pct=100*(comp['candidate']['mean_actual_spend']/comp['baseline']['mean_actual_spend']-1))
    episodes = {}
    for num, case, samples in [(5,['mid',250,500],[214.5,215.5,221.5,224.5]),
                               (10,['early',750,0],[215,216,245,251,300]),
                               (10,['mid',750,0],[214,216,224,227])]:
        name = f'{case[0]}_L{case[1]}_P{case[2]}'
        cp, ct = trace_for(num,case,study,manifest_hash)
        bp = PACKAGE/'traces'/('CELL_RECENT_'+name+'.json.gz')
        bt = json.loads(gzip.decompress(bp.read_bytes()))
        episodes[f'{num}:{name}'] = dict(candidate=slim_trace(cp,ct,samples), baseline=slim_trace(bp,bt,samples))
    OUT.mkdir(parents=True)
    # Clone only the frozen sampler state; no replay or objective evaluation.
    es = importlib.util.spec_from_file_location('_r88_frozen_engine_audit', PACKAGE/'engine.py')
    engine = importlib.util.module_from_spec(es);es.loader.exec_module(engine)
    original_study_hash = sha(RETURNS/'STUDY.json')
    with tempfile.TemporaryDirectory(prefix='sampler_probe_',dir=OUT) as temp:
        trial_path = Path(temp)/'STUDY.json';shutil.copyfile(RETURNS/'STUDY.json',trial_path)
        clone = engine.Study(trial_path,spec);preview = dict(clone.ask())
        restored = engine.Study(trial_path,spec)
        if preview != restored.ask() or preview['number'] != 12:
            raise ValueError('Persisted sampler does not resume exactly at trial 12')
    if sha(RETURNS/'STUDY.json') != original_study_hash:
        raise ValueError('Frozen parent study was modified')
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    checks = dict(package_hashes=True,result_artifact_hashes=True,exact_control_10groups=True,
        frozen_spec_identity=True,all_twelve_complete=True,frozen_shortlist_identity=True,
        screen_only_objectives_all12=True,past_only_proposal_history=True,
        payoff_and_pending_bound_identities=True,paired_deltas_exact=True,
        three_selected_trace_pairs_verified=True,sampler_resumes_at_trial12=True,
        sampler_reload_same_pending_proposal=True,original_study_unchanged=True)
    analysis = dict(status='ANALYZED_UNPROMOTED_ROUND2_PREPARED',created_at_taipei=now,
        parent_job=JOB,checks=checks,package_files_verified=package_count,result_artifacts_verified=artifact_count,
        native_initial_job=result['native_this_job'],completed_trials=12,shortlist=short['trials'],
        source_pins={rel(path):sha(path) for path in [PACKAGE/'MANIFEST.json',PACKAGE/'SEARCH_SPEC.json',
            PACKAGE/'PROTOCOL.md',RETURNS/'RESULT.json',RETURNS/'STUDY.json',RETURNS/'ROBUSTNESS.json',
            RETURNS/'EXACT_CONTROL.json',RETURNS/'FROZEN_SHORTLIST.json',Path(__file__)]},
        trial_rows=trial_rows,screen_action_equivalence_groups=list(equivalent.values()),candidates=candidates,
        saved_trace_episodes=episodes,scope=dict(unique_markets=1,market='1977248',consumed_development=True,
            not_OOS=True,fees='CONDITIONAL_200BPS',source_clock='UNKNOWN',capital_cap=None,
            new_native_this_analysis=0,new_jobs_this_analysis=0,neural_updates=0,live_changes=0),
        inference_boundaries=['The best observed branch is not actual winner-selected PnL.',
            'Owner-closed does not mean economically hedged or zero residual inventory.',
            'The pending lower bound is a scenario bound, not a realized loss.',
            'Leave-one-best-out and activity changes are new posthoc diagnostics, not amended preregistered gates.',
            'Different configurations with the same two-screen action hash need not match in other scenarios.',
            'This screen-plus-sensitivity result is not a causal factorial attribution or a stable parameter interval.'])
    argv=['python','-X','utf8','BTC5M_batch_search_v1/tools/btc5m_batch_search_v1.py','continue','--repo','.',
        '--job-id',NEXT_JOB,'--from-job',JOB,'--through',str(THROUGH)]
    plan=dict(status='PREPARED_NOT_SUBMITTED',created_at_taipei=now,parent_job=JOB,next_job=NEXT_JOB,
        research_mode='SAME_FROZEN_STUDY_SEARCH_EXPLORATION_NOT_POLICY_PROMOTION',
        reason='Nine family-coverage trials and only three adaptive proposals do not establish a stable region; continue bounded discovery without waiving failed policy continuation gates.',
        package_dir=rel(PACKAGE),package_sha256=manifest_hash,parent_result_sha256=sha(RETURNS/'RESULT.json'),
        parent_study_sha256=original_study_hash,parent_completed_trials=12,through=THROUGH,new_trials=THROUGH-12,
        new_trial_numbers_zero_based=[12,35],declared_total_budget=spec['trials'],spec_unchanged=True,
        screen_cases=execution['screen'],robustness_cases=execution['robustness'],maximum_shortlist=3,
        global_exploration_probability=spec['exploration_probability'],sampler='UNCHANGED_RANDOM_PLUS_PARETO_NEIGHBOR',
        preview_only_next_proposal=preview,all_later_proposals='Adaptive: generated only after newly completed screen results.',
        maximum_new_native_replays=2*(THROUGH-12)+10*3,control_and_completed_replays='Use only verified same-package cache; no planned reruns.',
        original_continuation_gates_unchanged=True,initial_passed_policy_candidates=[],
        no_policy_promotion_or_NN_fit_even_if_search_continues=True,
        posthoc_diagnostics_to_repeat=['paired regressions and both branches','pending bounds and owners',
            'leave-one-best-out concentration','activity and residual cost','physical action equivalence',
            'nearby sampled coefficients are not a dedicated perturbation validation'],
        cases_to_explain_not_optimizer_input=['5:mid_L250_P500','10:early_L750_P0','10:mid_L750_P0'],
        search_reads_only_frozen_screen_scores=True,robustness_not_fed_back_to_sampler=True,
        validation_boundary='All cases are consumed development sensitivity tests. No claim of fresh held-out evidence.',
        execution_constraints=dict(worker='DESKTOP-JIERAGF',one_heavy_job=True,max_threads=4,
            capital_cap=None,active_repair_unchanged=True,live_changes=0,neural_updates=0),
        required_before_submit=['Reverify exact parent and next job plus global worker state.',
            'Reverify strict SSH identity, capacity and pinned interpreter with the existing dispatcher.',
            'Reverify source package and all inherited result/cache hashes.',
            'No uncertain submit marker or unfinished destination; no duplicate submission.',
            'Use the existing load-only preflight. Stop on any failure; do not bypass safeguards.'],
        argv=argv,stop_after_this_batch=True,automatic_extension_to128=False,
        known_risks=['The two fixed screening cases may omit critical failure modes.',
            'If the next shortlist still fails on repeated mechanisms, do not assume a larger coefficient sweep fixes those mechanisms.',
            'A mechanism, objective or scenario change requires a separately versioned study, not edits to this frozen study.'])
    write_once(OUT/'ANALYSIS.json',analysis)
    write_once(OUT/'ROUND2_PLAN.json',plan)
    write_once(OUT/'ANALYSIS_CHECKS.json',dict(status='PASS',checks=checks,count=len(checks),native_executed=0,
        original_study_sha256=original_study_hash,next_proposal_number=preview['number']))
    print(json.dumps(dict(status=analysis['status'],out=rel(OUT),checks=len(checks),package_files=package_count,
        artifacts=artifact_count,initial_native=result['native_this_job'],shortlist=short['trials'],
        next_job=NEXT_JOB,through=THROUGH,new_trials=THROUGH-12,maximum_new_native=plan['maximum_new_native_replays'],
        next_trial_preview=preview,submitted=False),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
