"""R88 b03 saved-artifact analysis only. No native execution or source mutation."""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
import statistics

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'data/research/btc5m_batch_search_20260921_r88'
RET = ROOT / 'data/research/lan_worker_returns'
OLD = RET / 'btc5m-batch-search-20260921-r88-b02'
NEW = RET / 'btc5m-batch-search-20260921-r88-b03'
OUT = ROOT / 'data/research/btc5m_batch_search_20260921_r88_final_analysis'


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def canonical(x): return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(x): return hashlib.sha256(canonical(x)).hexdigest()
def normalized(p):
    p = dict(p)
    if p['history'] == 'age': p['failure_weight'] = 0.
    if p['history'] == 'attempts': p['age_weight'] = 0.
    return p


def verify(root, entries):
    for name, expected in entries.items():
        p = (root / name).resolve()
        assert p.is_relative_to(root.resolve()) and p.is_file() and sha(p) == expected, name
    return len(entries)


def front(trials):
    return sorted(set(tuple(t['objectives']) for t in trials if not any(
        all(a >= b for a, b in zip(q['objectives'], t['objectives'])) and
        any(a > b for a, b in zip(q['objectives'], t['objectives'])) for q in trials)))


def profile(study):
    rows = study['trials']
    return dict(trials=len(rows), unique_effective_configs=len({digest(normalized(t['params'])) for t in rows}),
        unique_scores=len({tuple(t['objectives']) for t in rows}),
        unique_actions=len({tuple(r['candidate']['physical_action_hash'] for r in t['metrics']['rows']) for t in rows}),
        frontier=front(rows), maxima=[max(t['objectives'][i] for t in rows) for i in range(3)])


def paired(rows):
    d = sorted([r['delta_floor'] for r in rows], reverse=True)
    cc = [r for r in rows if r['common_closed']]
    return dict(better=sum(v > 1e-7 for v in d), worse=sum(v < -1e-7 for v in d), same=sum(abs(v) <= 1e-7 for v in d),
        total_delta=sum(d), median_delta=statistics.median(d), without_best=sum(d[1:]), without_best_two=sum(d[2:]),
        common_closed=len(cc), common_better=sum(r['delta_floor'] > 1e-7 for r in cc),
        common_worse=sum(r['delta_floor'] < -1e-7 for r in cc))


def main():
    if OUT.exists(): raise RuntimeError('Analysis already exists; do not replace frozen report')
    old = read(OLD/'STUDY.json'); new = read(NEW/'STUDY.json'); result = read(NEW/'RESULT.json')
    manifest = read(P/'MANIFEST.json'); exact = read(NEW/'EXACT_CONTROL.json'); short = read(NEW/'FROZEN_SHORTLIST.json')
    checks = {}
    checks['package_hashes'] = verify(P, manifest['files']) == 53
    checks['result_artifact_hashes'] = verify(NEW, result['artifacts']) == len(result['artifacts'])
    checks['manifest_identity'] = result['manifest_sha256'] == sha(P/'MANIFEST.json')
    checks['completed_64'] = len(new['trials']) == 64 and all(t['state'] == 'COMPLETE' for t in new['trials'])
    checks['old36_unchanged'] = new['trials'][:36] == old['trials']
    checks['exact_control'] = len(exact['equal']) == 10 and all(exact['equal'].values())
    checks['shortlist_identity'] = short['screen_only_selection'] and short['study_sha256'] == sha(NEW/'STUDY.json')
    checks['frozen_spec_unchanged'] = new['spec'] == old['spec'] == read(P/'SEARCH_SPEC.json')
    screen = {'early_L250_P0', 'late_L750_P500'}
    checks['screen_only_objectives'] = all({r['scenario'] for r in t['metrics']['rows']} == screen and
        t['objectives'] == [t['metrics']['candidate'][k] for k in new['spec']['objectives']] for t in new['trials'])
    rob = read(NEW/'ROBUSTNESS.json'); oldrob = read(OLD/'ROBUSTNESS.json')
    summaries = {}
    for k, v in rob.items():
        c = v['comparison']; rows = c['rows']
        for row in rows:
            for arm in ('baseline', 'candidate'):
                x = row[arm]
                assert abs(x['floor'] - min(x['payoff'].values())) < 1e-7
                assert abs(x['floor'] - x['paired_surplus'] + x['residual_cost']) < 1e-6
        summaries[k] = dict(params=v['params'], effective_params=normalized(v['params']),
            baseline=c['baseline'], candidate=c['candidate'], gates=v['continuation_gates'],
            passed=v['continuation_candidate'], paired=paired(rows), rows=rows,
            by_latency={str(L):paired([r for r in rows if '_L'+str(L)+'_' in r['scenario']]) for L in (250,750)},
            activity_change_pct={f:100*(c['candidate'][f]/c['baseline'][f]-1) for f in
                ['mean_actual_spend','mean_new_orders','mean_receipts','mean_best_branch','mean_paired_surplus']})
    checks['branch_and_fifo_identities'] = True
    a37 = {r['scenario']:r['candidate'] for r in rob['37']['comparison']['rows']}
    a52 = {r['scenario']:r['candidate'] for r in rob['52']['comparison']['rows']}
    a20 = {r['scenario']:r['candidate'] for r in oldrob['20']['comparison']['rows']}
    comparison20 = [dict(scenario=k, delta_floor=a37[k]['floor']-a20[k]['floor'],
        delta_pair=a37[k]['paired_surplus']-a20[k]['paired_surplus'],
        delta_best=a37[k]['best_branch']-a20[k]['best_branch']) for k in a37]
    equality52 = dict(economic_endpoints_same=sum(all(abs(a37[k][f]-a52[k][f])<1e-7 for f in
        ('floor','paired_surplus','best_branch','residual_cost')) for k in a37),
        physical_actions_same=sum(a37[k]['physical_action_hash']==a52[k]['physical_action_hash'] for k in a37))
    oldkeys = {p.name for p in (OLD/'cache').glob('*.meta.json')}; newkeys = {p.name for p in (NEW/'cache').glob('*.meta.json')}
    added = newkeys-oldkeys
    new_meta = [read(NEW/'cache'/name) for name in added]
    counts = dict(parent_cache_cases=len(oldkeys), cumulative_cache_cases=len(newkeys), new_native=len(added),
        new_screen_native=sum(m['scenario'] in screen for m in new_meta),
        new_sensitivity_native=sum(m['scenario'] not in screen for m in new_meta),
        reused_screen_requests=56-sum(m['scenario'] in screen for m in new_meta))
    checks['native_count_reconciles'] = counts['new_native'] == result['native_this_job'] == 64
    evidence = {}
    config = normalized(new['trials'][37]['params']); mh = sha(P/'MANIFEST.json')
    for case in [('mid',250,500),('late',250,500)]:
        key=digest([mh,config,case]); p=NEW/'cache'/(key+'.json.gz'); meta=read(NEW/'cache'/(key+'.meta.json'))
        assert meta['trace_sha256']==sha(p) and meta['state']=='COMPLETE'
        tr=json.loads(gzip.decompress(p.read_bytes())); start=tr['decisions'][0]['ms']-round(1000*tr['decisions'][0]['state']['elapsed_seconds'])
        picked=[]
        for row in tr['decisions']:
            sec=row['state']['elapsed_seconds']
            if 214<=sec<=225 and (row['executed'] or sec in (215.5,221.5,224.5)):
                picked.append(dict(elapsed_seconds=sec,inv=row['state']['inv'],payoff=row['state']['payoff'],
                    active_evidence=row['evidence'].get('active'),executed=row['executed'],
                    attempt_observer=row['attempt_observer'],
                    down_forecasts=[f for f in tr['formula_forecasts'] if f['ms']==row['ms'] and f['side']=='DOWN'][-2:]))
        evidence[str(case)] = dict(path=p.relative_to(ROOT).as_posix(),sha256=sha(p),replay=tr['replay']['status'],
            final=tr['final']['observation'],selected_decisions=picked,
            last_receipts=[dict(elapsed_seconds=(r['receive_ts']/1e6-start)/1000,side=r['side'],price=r['price'],qty=r['qty'],maker=r['maker']) for r in tr['raw_receipts'][-6:]])
    checks['two_native_failure_traces_verified'] = all(x['replay']=='PASS' for x in evidence.values())
    checks['no_auto_promotion_or_NN_or_live'] = not result['promoted'] and result['neural_updates']==0 and result['live_changes']==0
    assert all(checks.values()), checks
    pold,pnew=profile(old),profile(new)
    result_analysis=dict(status='R88_CLOSED_AT64_SWITCH_TO_R89_MECHANISM_SMOKE',created_at_taipei=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        checks=checks,package_files=53,result_artifacts=len(result['artifacts']),cache_counts=counts,
        profile36=pold,profile64=pnew,frontier_moved=pold['frontier']!=pnew['frontier'],
        all_maxima_improved=all(b>a for a,b in zip(pold['maxima'],pnew['maxima'])),
        new_sampling_counts=dict(Counter(t['sampling'] for t in new['trials'][36:])),
        shortlist=short['trials'],passed_candidates=[k for k,v in summaries.items() if v['passed']],candidates=summaries,
        candidate37_vs20=comparison20,candidate37_vs52=equality52,selected_failure_evidence=evidence,
        source_pins={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in [P/'MANIFEST.json',NEW/'RESULT.json',NEW/'STUDY.json',NEW/'ROBUSTNESS.json',OLD/'STUDY.json',OLD/'ROBUSTNESS.json',Path(__file__)]},
        decision=dict(original_plan_branch='FRONTIER_MOVED_BUT_GATES_FAIL',automatic_unchanged_frontier_stop_triggered=False,
            authorized_change='User requested changing approach absent clear practical progress; no extension to128.',
            observed='Screen frontier improves materially, but matched twelve-case worst regressions persist and all full-case candidates fail.',
            proposed_new_hypothesis='R88 only prices confirmed active-zero history and fill-lot age. It misses currently unserviceable confirmed repair debt. Add a soft marginal new-exposure cost derived from inherited active feasibility and observed opposite-side price.',
            no_invented_hard_limits=True,no_weights_reset=True),
        scope=dict(unique_markets=1,market='1977248',consumed_development=True,conditional_200bps=True,source_clock='UNKNOWN',new_native_this_analysis=0,NN_updates=0,live_changes=0))
    OUT.mkdir(parents=True)
    for name,value in [('ANALYSIS.json',result_analysis),('CHECKS.json',dict(status='PASS',checks=checks,count=len(checks)))]:
        (OUT/name).write_bytes(canonical(value))
    print(json.dumps(dict(status=result_analysis['status'],checks=len(checks),frontier_moved=result_analysis['frontier_moved'],profile36=pold,profile64=pnew,
        cache_counts=counts,shortlist=short['trials'],passed=[],candidate37_vs52=equality52,comparison20=comparison20,out=str(OUT.relative_to(ROOT))),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
