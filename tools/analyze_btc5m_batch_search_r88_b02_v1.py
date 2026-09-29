"""Analyze saved R88 b02 JSON summaries and prepare b03; never dispatch/replay.

Frozen packages, previous reports, studies and caches remain immutable. No raw
compressed market trace is opened by this analysis. The next proposal is checked
only on a temporary study copy, without reporting a fabricated objective value.
"""
from __future__ import annotations
import collections
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import statistics
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'data/research/btc5m_batch_search_20260921_r88'
INITIAL_JOB = 'btc5m-batch-search-20260921-r88'
PARENT_JOB = INITIAL_JOB + '-b02'
NEXT_JOB = INITIAL_JOB + '-b03'
INITIAL = ROOT / 'data/research/lan_worker_returns' / INITIAL_JOB
RETURNS = ROOT / 'data/research/lan_worker_returns' / PARENT_JOB
OUT = ROOT / 'data/research/btc5m_batch_search_20260921_r88_analysis_round3'
THROUGH = 64
EPS = 1e-7


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def check_tree(base, entries):
    for name, expected in entries.items():
        path = (base / name).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file() or sha(path) != expected:
            raise ValueError('Artifact outside boundary, missing, or hash mismatch: ' + name)
    return len(entries)


def effective(params):
    value = dict(params)
    if value['history'] == 'age':
        value['failure_weight'] = 0.
    elif value['history'] == 'attempts':
        value['age_weight'] = 0.
    return value


def group_ids(trials, key):
    groups = collections.defaultdict(list)
    for row in trials:
        groups[key(row)].append(row['number'])
    return list(groups.values())


def delta_summary(rows):
    values = [row['delta_floor'] for row in rows]
    common = [row for row in rows if row['common_closed']]
    return dict(n=len(rows), better=sum(x > EPS for x in values), worse=sum(x < -EPS for x in values),
        same=sum(abs(x) <= EPS for x in values), mean_delta=statistics.mean(values),
        median_delta=statistics.median(values), total_delta=sum(values),
        total_without_best=sum(values)-max(values), total_without_best_two=sum(sorted(values)[:-2]),
        common_closed_n=len(common), common_closed_better=sum(x['delta_floor'] > EPS for x in common),
        common_closed_worse=sum(x['delta_floor'] < -EPS for x in common),
        common_closed_same=sum(abs(x['delta_floor']) <= EPS for x in common))


def counts(rows, field):
    return dict(collections.Counter(str(row[field]) for row in rows))


def write_new(path, value):
    with Path(path).open('xb') as stream:
        stream.write(value.encode('utf-8') if isinstance(value, str) else canonical(value))


def main():
    if OUT.exists():
        raise RuntimeError('Output exists. Preserve this analysis; do not overwrite or rerun blindly.')
    manifest = load(PACKAGE/'MANIFEST.json')
    mh = sha(PACKAGE/'MANIFEST.json')
    package_count = check_tree(PACKAGE, manifest['files'])
    result = load(RETURNS/'RESULT.json')
    if result['status'] != 'COMPLETE_UNPROMOTED' or result['manifest_sha256'] != mh:
        raise ValueError('Parent completion/package identity mismatch')
    artifact_count = check_tree(RETURNS, result['artifacts'])
    spec = load(PACKAGE/'SEARCH_SPEC.json')
    study = load(RETURNS/'STUDY.json'); old = load(INITIAL/'STUDY.json')
    trials = study['trials']; before = old['trials']
    if len(before) != 12 or len(trials) != 36 or trials[:12] != before:
        raise ValueError('Previous trial prefix changed or trial counts unexpected')
    if result['completed_trials'] != 36 or any(x['state'] != 'COMPLETE' for x in trials):
        raise ValueError('Not all 36 trials are complete')
    if study['spec'] != spec or old['spec'] != spec or study['spec_hash'] != hashlib.sha256(canonical(spec)).hexdigest():
        raise ValueError('Frozen specification changed')
    if result['spec_sha256'] != sha(PACKAGE/'SEARCH_SPEC.json'):
        raise ValueError('Result specification hash mismatch')
    exact = load(RETURNS/'EXACT_CONTROL.json')
    if len(exact['equal']) != 10 or not all(exact['equal'].values()):
        raise ValueError('Exact original control mismatch')
    shortlist = load(RETURNS/'FROZEN_SHORTLIST.json')
    if not shortlist['screen_only_selection'] or shortlist['study_sha256'] != sha(RETURNS/'STUDY.json'):
        raise ValueError('Shortlist identity failed')
    robust = load(RETURNS/'ROBUSTNESS.json'); old_robust = load(INITIAL/'ROBUSTNESS.json')
    if set(robust) != set(map(str, shortlist['trials'])):
        raise ValueError('Shortlist/robustness mismatch')
    if any(robust[number] != value for number,value in old_robust.items()):
        raise ValueError('Prior robustness evidence changed')
    if result['evaluations'] != robust:
        raise ValueError('RESULT and ROBUSTNESS disagree')
    plan = load(RETURNS/'EXECUTION_PLAN.json')
    screen_names = {f'{a}_L{b}_P{c}' for a,b,c in plan['screen']}
    trial_rows = []
    for t in trials:
        rows=t['metrics']['rows']
        if {x['scenario'] for x in rows} != screen_names:
            raise ValueError('Sampler metrics include a non-screen case')
        expected=[t['metrics']['candidate'][key] for key in spec['objectives']]
        if t['objectives'] != expected or not all(math.isfinite(x) for x in expected):
            raise ValueError('Objective values do not match frozen screen scores')
        if any(n >= t['number'] for n in t['completed_results_used']):
            raise ValueError('Proposal used a future trial')
        if t['parent_trial'] is not None and t['parent_trial'] not in t['completed_results_used']:
            raise ValueError('Uncompleted proposal parent')
        trial_rows.append(dict(number=t['number'],params=t['params'],effective_params=effective(t['params']),
            sampling=t['sampling'],parent_trial=t['parent_trial'],objectives=t['objectives'],
            closed_screen_cases=t['metrics']['candidate']['closed'],
            pending_screen_owners=t['metrics']['candidate']['total_pending']))
    all_rows=[row for t in trials for row in t['metrics']['rows']]
    all_rows += [row for value in robust.values() for row in value['comparison']['rows']]
    for row in all_rows:
        for arm in ('baseline','candidate'):
            v=row[arm]
            if not math.isclose(v['floor'],min(v['payoff'].values()),abs_tol=EPS):
                raise ValueError('Floor does not equal lower settlement branch')
            if v['pending_conservative_floor'] > v['floor'] + EPS:
                raise ValueError('Pending bound exceeds the observed floor')
            if not math.isclose(v['floor'],v['paired_surplus']-v['residual_cost'],abs_tol=EPS):
                raise ValueError('Pair/residual-cost identity mismatch')
        if not math.isclose(row['delta_floor'],row['candidate']['floor']-row['baseline']['floor'],abs_tol=EPS):
            raise ValueError('Paired floor delta mismatch')
    old_meta={p.name:p for p in (INITIAL/'cache').glob('*.meta.json')}
    new_meta={p.name:p for p in (RETURNS/'cache').glob('*.meta.json')}
    if not set(old_meta).issubset(new_meta):
        raise ValueError('Inherited cache missing')
    for name,path in old_meta.items():
        if sha(path) != sha(new_meta[name]):
            raise ValueError('Inherited cache metadata changed')
        gz=name.replace('.meta.json','.json.gz')
        if sha(INITIAL/'cache'/gz) != sha(RETURNS/'cache'/gz):
            raise ValueError('Inherited compressed trace bytes changed')
    added=[load(new_meta[n]) for n in sorted(set(new_meta)-set(old_meta))]
    if len(added) != result['native_this_job'] or any(x['state']!='COMPLETE' for x in added):
        raise ValueError('New cache/native execution count mismatch')
    cache_split=dict(inherited_cases=len(old_meta),cumulative_cases=len(new_meta),new_native=len(added),
        new_screen_native=sum(x['scenario'] in screen_names for x in added),
        new_sensitivity_native=sum(x['scenario'] not in screen_names for x in added),
        nominal_new_screen_requests=24*len(screen_names))
    cache_split['screen_requests_reusing_cache']=cache_split['nominal_new_screen_requests']-cache_split['new_screen_native']
    sys.dont_write_bytecode=True
    modspec=importlib.util.spec_from_file_location('_r88_engine_analysis_only',PACKAGE/'engine.py')
    engine=importlib.util.module_from_spec(modspec);modspec.loader.exec_module(engine)
    f0=engine.pareto(before);f1=engine.pareto(trials)
    v0={tuple(x['objectives']) for x in f0};v1={tuple(x['objectives']) for x in f1}
    novelty=dict(score_groups_first12=group_ids(before,lambda x:tuple(x['objectives'])),
        score_groups_all36=group_ids(trials,lambda x:tuple(x['objectives'])),
        action_groups_first12=group_ids(before,lambda x:tuple(y['candidate']['physical_action_hash'] for y in x['metrics']['rows'])),
        action_groups_all36=group_ids(trials,lambda x:tuple(y['candidate']['physical_action_hash'] for y in x['metrics']['rows'])),
        effective_parameter_groups=group_ids(trials,lambda x:canonical(effective(x['params']))),
        pareto_trials_first12=[x['number'] for x in f0],pareto_trials_all36=[x['number'] for x in f1],
        distinct_pareto_scores_first12=[list(x) for x in sorted(v0)],distinct_pareto_scores_all36=[list(x) for x in sorted(v1)],
        distinct_frontier_unchanged=v0==v1,
        objective_best_first12=[max(t['objectives'][i] for t in before) for i in range(len(spec['objectives']))],
        objective_best_all36=[max(t['objectives'][i] for t in trials) for i in range(len(spec['objectives']))],
        proposals_second_batch=counts(trials[12:],'sampling'),parents_second_batch=counts(trials[12:],'parent_trial'),
        family_curve_counts=[dict(history=k[0],curve=k[1],count=v) for k,v in collections.Counter((t['params']['history'],t['params']['curve']) for t in trials).items()],
        not_proof_of_global_optimum=True,not_proof_of_robust_parameter_interval=True)
    candidates={}
    for number,value in robust.items():
        comp=value['comparison'];rows=comp['rows'];base=comp['baseline'];cand=comp['candidate']
        original=[x for x in rows if x['baseline']['closed'] and x['baseline']['floor']>=0]
        group=collections.defaultdict(list)
        for row in rows:
            label=row['scenario'].split('_')[1]
            group[label].append(row)
        candidates[number]=dict(effective_params=effective(value['params']),comparison=comp,
            paired_diagnostics=delta_summary(rows),continuation_gates=value['continuation_gates'],
            failed_gates=[k for k,v in value['continuation_gates'].items() if not v],
            gate_pass_count=sum(value['continuation_gates'].values()),
            original_positive_preserved=sum(x['candidate']['closed'] and x['candidate']['floor']>=0 for x in original),
            original_positive_total=len(original),
            grouped_by_case_label={k:delta_summary(v) for k,v in group.items()},
            relative_change_pct={k:100*(cand[k]/base[k]-1) for k in ['mean_receipts','mean_new_orders','mean_actual_spend','mean_best_branch','mean_paired_surplus']})
    by5={x['scenario']:x['candidate'] for x in robust['5']['comparison']['rows']}
    contrast=[]
    for row in robust['20']['comparison']['rows']:
        x=by5[row['scenario']];y=row['candidate']
        contrast.append(dict(scenario=row['scenario'],candidate5=x,candidate20=y,
            delta_floor=y['floor']-x['floor'],delta_best=y['best_branch']-x['best_branch'],
            delta_pair=y['paired_surplus']-x['paired_surplus'],delta_residual=y['residual_cost']-x['residual_cost'],
            same_physical_actions=x['physical_action_hash']==y['physical_action_hash']))
    if set(robust['20']['params']) != set(spec['space']):
        raise ValueError('Candidate20 schema mismatch')
    old_study_hash=sha(RETURNS/'STUDY.json')
    OUT.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='resume_check_',dir=OUT) as temp:
        cp=Path(temp)/'STUDY.json';shutil.copyfile(RETURNS/'STUDY.json',cp)
        clone=engine.Study(cp,spec)
        if clone.shortlist(3) != shortlist['trials']:
            raise ValueError('Frozen shortlist cannot be reproduced')
        preview=dict(clone.ask());restored=engine.Study(cp,spec)
        if preview!=restored.ask() or preview['number']!=36:
            raise ValueError('Continuation does not resume at trial36 identically')
    if sha(RETURNS/'STUDY.json')!=old_study_hash:
        raise ValueError('Parent STUDY mutated')
    checks=dict(package_hashes=True,result_artifact_hashes=True,source_and_spec_hashes=True,
        all36_complete=True,first12_exact_unchanged=True,exact_control10=True,
        previous_shortlist_results_unchanged=True,result_and_robustness_equal=True,
        shortlist_frozen_study_hash=True,screen_only_scores_all36=True,past_only_proposals=True,
        branch_floor_identity=True,pending_bound_not_overstated=True,pair_minus_residual_identity=True,
        paired_deltas_exact=True,inherited_cache_metadata_and_bytes_unchanged=True,
        new_native_cache_count_exact=True,original_shortlist_reproduced=True,
        next_proposal_number36=True,pending_proposal_reload_exact=True,original_study_unchanged=True)
    now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    source_paths=[PACKAGE/'MANIFEST.json',PACKAGE/'SEARCH_SPEC.json',PACKAGE/'PROTOCOL.md',
        RETURNS/'RESULT.json',RETURNS/'STUDY.json',RETURNS/'ROBUSTNESS.json',
        RETURNS/'FROZEN_SHORTLIST.json',RETURNS/'EXACT_CONTROL.json',INITIAL/'STUDY.json',Path(__file__)]
    analysis=dict(status='B02_ANALYZED_B03_PREPARED_NOT_SUBMITTED',created_at_taipei=now,
        job=PARENT_JOB,package_verified=package_count,result_artifacts_verified=artifact_count,
        checks=checks,source_pins={relative(path):sha(path) for path in source_paths},
        completed_trials=36,new_trials=24,cache_counts=cache_split,trial_rows=trial_rows,
        search_novelty=novelty,candidates=candidates,candidate20_vs5=contrast,
        candidate20_vs5_summary=dict(same_endpoint_floor=sum(abs(x['delta_floor'])<=EPS for x in contrast),
            changed_endpoint_floor=sum(abs(x['delta_floor'])>EPS for x in contrast),
            same_physical_actions=sum(x['same_physical_actions'] for x in contrast),
            total_delta_floor=sum(x['delta_floor'] for x in contrast),
            total_delta_pair=sum(x['delta_pair'] for x in contrast)),
        scope=dict(market='1977248',unique_markets=1,consumed_development=True,not_OOS=True,
            fee_assumption='CONDITIONAL_200BPS',source_clock='UNKNOWN',capital_cap=None,
            raw_trace_inspection_this_analysis=False,raw_trace_block_not_retried=True,
            component_checks_not_market_results=True,new_native=0,new_jobs=0,neural_updates=0,live_changes=0),
        limitations=['The optional raw-trace/source inspection was blocked by the platform; it was not retried by an alternative path.',
            'Equal loss magnitudes do not prove identical causal execution paths; new candidate20 receipt-level causes are not verified here.',
            'Owner closure is not economic repair; positive paired surplus can coexist with negative floor due to residual cost.',
            'All36 have only2 screening cases; only3 shortlisted candidates have12-case sensitivity evidence.',
            'The original pending-conservative objective already exists; the issue is information coverage, not an invented absence of pending in the reward.',
            'Unchanged frontier in24 extra draws does not establish a global optimum or prove all coefficients fail.',
            'All concentration, score-equivalence and case-group diagnostics are posthoc descriptions; original gates remain unchanged.'])
    argv=['python','-X','utf8','BTC5M_batch_search_v1/tools/btc5m_batch_search_v1.py','continue',
        '--repo','.','--job-id',NEXT_JOB,'--from-job',PARENT_JOB,'--through',str(THROUGH)]
    third=dict(status='PREPARED_NOT_SUBMITTED',created_at_taipei=now,job_id=NEXT_JOB,parent_job=PARENT_JOB,
        research_intent='BOUNDED_TEST_OF_REMAINING_PARAMETER_SEARCH_PROGRESS',
        hypothesis='Further independent/global and Pareto-neighbor proposals within the unchanged space may reveal a new screen frontier or a candidate passing the unchanged12-case continuation gates; no improvement is an informative outcome.',
        prior_evidence='36trials give15score groups and23screen action groups; two distinct nondominated score vectors are unchanged since trial11. Candidate20 is a tie-selected new sensitivity path, not a screen-score breakthrough.',
        package_dir=relative(PACKAGE),package_sha256=mh,spec_sha256=sha(PACKAGE/'SEARCH_SPEC.json'),
        parent_result_sha256=sha(RETURNS/'RESULT.json'),parent_study_sha256=old_study_hash,
        parent_completed_trials=36,through=THROUGH,new_trials=THROUGH-36,new_trial_numbers_zero_based=[36,63],
        declared_total_budget=128,initial_trials_must_be_unchanged=36,screen_cases=plan['screen'],
        sensitivity_cases=plan['robustness'],maximum_shortlist=3,maximum_new_native_replays=2*(THROUGH-36)+10*3,
        cached_parent_native_cases=cache_split['cumulative_cases'],reuse_only_verified_identical_inputs=True,
        unchanged=dict(source_package=True,sampler=True,seed=True,search_ranges=True,
            objective_names=spec['objectives'],screen_scores_only=True,original_seven_gates=True,
            active_repair=True,neural_weights_and_Adam=True),
        exploration_probability=spec['exploration_probability'],
        next_proposal_preview_only=preview,remaining_proposals='Generated adaptively from new completed screen scores; not pre-filled or fabricated.',
        preregistered_batch3_reporting=['objective frontier movement and maxima versus the36trial parent',
            'new effective configurations, new score groups, new physical action groups and cache reuse',
            'all seven original gates and every paired regression',
            'matched candidate20/5 comparison, especially mid_L250_P500 and late_L250_P500',
            'pending conservative endpoints, original positive paths, path-floor minima and activity changes',
            'leave-one/two-best-scenario-out concentration as descriptive sensitivity, not new promotion gates',
            'shortlist saturation: equal-score candidates not selected for12-case evaluation remain unevaluated there'],
        after_batch_decision=dict(no_automatic_extension=True,stop_at=THROUGH,
            if_frontier_unchanged_and_zero_gate_pass='Do not automatically continue toward128; report stalled parameter search and prepare a separately versioned same-score/path or repair-gap mechanism experiment.',
            if_frontier_moves_but_gates_fail='Analyze failures before authorizing further search; no automatic promotion.',
            if_a_candidate_passes='Report as one-consumed-market continuation evidence only; still no NN fitting, fresh-market job or live deployment in this batch.'),
        deferred_not_in_this_batch=['Changing the two screen scenarios or their weights.',
            'Changing shortlist tie-breaking, constraint domination, effective-dimension sampling or the reward.',
            'Adding a manual lock-profit, arbitrary loss cap, permanent HOLD or a new repair mechanism.',
            'Dedicated matched perturbation validation of candidates13/22/23/29; requires a separately frozen evaluation contract.'],
        resource_contract=dict(worker='DESKTOP-JIERAGF',one_heavy_job=True,max_threads=4,
            numerical_library_threads=1,capital_cap=None,no_main_host_native=True,neural_updates=0,live_changes=0),
        requires_at_dispatch=['Exact parent succeeded and next job missing; if running/completed, attach rather than resubmit.',
            'No unknown prior submission marker or destination; inspect global worker state.',
            'Strict SSH host identity, capacity and pinned interpreter checked through the existing entry point.',
            'Package, parent result/cache hashes and load-only ABI checks pass.',
            'Preserve pending/UNKNOWN and canonical owner release semantics.'],
        argv=argv,submitted=False,new_native_this_preparation=0)
    write_new(OUT/'ANALYSIS.json',analysis)
    write_new(OUT/'ROUND3_PLAN.json',third)
    write_new(OUT/'ANALYSIS_CHECKS.json',dict(status='PASS',count=len(checks),checks=checks,
        package_files=package_count,result_artifacts=artifact_count,native_executed=0,
        parent_study_sha256=old_study_hash,next_trial_number=preview['number']))
    compact=dict(status=analysis['status'],out=relative(OUT),checks=len(checks),cache_counts=cache_split,
        score_groups=[len(novelty['score_groups_first12']),len(novelty['score_groups_all36'])],
        action_groups=[len(novelty['action_groups_first12']),len(novelty['action_groups_all36'])],
        effective_configs=len(novelty['effective_parameter_groups']),frontier_unchanged=novelty['distinct_frontier_unchanged'],
        candidate20_vs5_summary=analysis['candidate20_vs5_summary'],
        candidate20_vs5=[{k:x[k] for k in ['scenario','delta_floor','delta_best','delta_pair','delta_residual','same_physical_actions']} for x in contrast],
        candidate20=dict(paired_diagnostics=candidates['20']['paired_diagnostics'],
            grouped=candidates['20']['grouped_by_case_label'],relative_change_pct=candidates['20']['relative_change_pct']),
        next_job=NEXT_JOB,through=THROUGH,new_trials=28,maximum_new_native=third['maximum_new_native_replays'],
        next_proposal=preview,submitted=False)
    write_new(OUT/'SUMMARY.json',compact)
    print(json.dumps(compact,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
