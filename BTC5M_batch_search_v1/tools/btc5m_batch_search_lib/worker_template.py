"""Second-PC-only native adapter for a persistent, bounded parameter study.

No training of R65/R84 weights, no new markets, no live or testnet API calls.
A control replay must match R85 before any candidate receives a score.
"""
import os
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[variable] = '1'
import argparse
import gzip
import json
import math
import shutil
import socket
import statistics
import time
import traceback
from pathlib import Path
from engine import Study, WriterLock, atomic_bytes, atomic_json, canonical, digest, file_hash, read_json, pareto
from attempt_memory import effective_config

P = Path(__file__).resolve().parent
SCREEN = [('early', 250, 0), ('late', 750, 500)]
ALL_CASES = [(o, l, p) for o in ('early', 'mid', 'late') for l in (250, 750) for p in (0, 500)]


def zread(path):
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def zwrite(path, value):
    atomic_bytes(path, gzip.compress(canonical(value), mtime=0))


def suffix(case):
    return f'{case[0]}_L{case[1]}_P{case[2]}'


def physical_actions(trace):
    return [[d['ms'], [{k: op[k] for k in ('kind', 'key', 'side', 'route', 'price', 'qty') if k in op}
                       for op in d['executed']]] for d in trace['decisions']]


def summarize(trace):
    o = trace['final']['observation']
    pay = dict(o['payoff'])
    lower = dict(pay)
    for owner in o['owners']:
        other = 'DOWN' if owner['side'] == 'UP' else 'UP'
        lower[other] -= owner['qty'] * owner['limit']
    if o['capital_cap'] is not None:
        raise ValueError('Research funding cap was reintroduced')
    if trace['replay']['status'] != 'PASS':
        raise ValueError('Replay accounting failed')
    start = trace['decisions'][0]['ms'] - round(trace['decisions'][0]['state']['elapsed_seconds'] * 1000)
    late_new = [e for e in trace['events'] if e['kind'] == 'NEW' and e['route'] == 'PASSIVE' and e['ms'] >= start + 90000]
    late_pair = [e for e in trace['online_matches'] if e['birth_ms'] >= start + 90000]
    return dict(floor=min(pay.values()), best_branch=max(pay.values()), payoff=pay,
        pending_conservative_floor=min(lower.values()), pending_branch_minima=lower,
        paired_surplus=trace['net_pairing']['paired_surplus'], residual_cost=trace['residual_cost'],
        pending=trace['unresolved'], receipts=trace['receipts'], new_orders=trace['new_orders'],
        closed=trace['unresolved'] == 0 and trace['endpoint_reaches_expiry'],
        endpoint_reaches_expiry=trace['endpoint_reaches_expiry'], censored=trace['censored'],
        negative_floor_area=trace['negative_floor_area'], worst_floor=trace['worst_floor'],
        actual_spend=o['cost'], peak_committed_cash=trace['peak_committed_cash'],
        active_outcomes=trace['active_outcomes'], physical_action_hash=digest(physical_actions(trace)),
        cycle_after90=bool(late_new and late_pair),
        extra_cost_calls=len(trace.get('formula_forecasts', [])),
        positive_cost_charges=sum(x['extra_charge'] > 1e-8 for x in trace.get('formula_forecasts', [])),
        attempt_summary=trace.get('attempt_summary'))


def aggregate(rows):
    if not rows:
        raise ValueError('No scenarios to aggregate')
    fields = ['floor', 'best_branch', 'pending_conservative_floor', 'paired_surplus',
              'residual_cost', 'negative_floor_area', 'actual_spend', 'receipts', 'new_orders']
    result = {'mean_' + k: statistics.mean(r[k] for r in rows) for k in fields}
    result.update(scenarios=len(rows), closed=sum(r['closed'] for r in rows),
                  total_pending=sum(r['pending'] for r in rows),
                  worst_floor=min(r['floor'] for r in rows),
                  worst_observed_path_floor=min(r['worst_floor'] for r in rows))
    return result


def compare(rows, references):
    paired = []
    for key, b in rows.items():
        a = references[key]
        paired.append(dict(scenario=key, baseline=a, candidate=b, delta_floor=b['floor'] - a['floor'],
            delta_pending_floor=b['pending_conservative_floor'] - a['pending_conservative_floor'],
            delta_pairing=b['paired_surplus'] - a['paired_surplus'],
            delta_best_branch=b['best_branch'] - a['best_branch'],
            common_closed=a['closed'] and b['closed'],
            changed=a['physical_action_hash'] != b['physical_action_hash']))
    return dict(rows=paired, better=sum(r['delta_floor'] > 1e-7 for r in paired),
        worse=sum(r['delta_floor'] < -1e-7 for r in paired),
        same=sum(abs(r['delta_floor']) <= 1e-7 for r in paired),
        changed=sum(r['changed'] for r in paired),
        baseline=aggregate([r['baseline'] for r in paired]),
        candidate=aggregate([r['candidate'] for r in paired]))


def continuation_gates(comparison):
    rows = comparison['rows']
    positive = [r for r in rows if r['baseline']['closed'] and r['baseline']['floor'] >= 0]
    common = [r for r in rows if r['common_closed']]
    return dict(twelve_scenarios=len(rows) == 12,
        all_endpoints_closed=all(r['candidate']['closed'] for r in rows),
        original_three_positive_preserved=len(positive) == 3 and all(r['candidate']['closed'] and r['candidate']['floor'] >= 0 for r in positive),
        mean_floor_improves=comparison['candidate']['mean_floor'] > comparison['baseline']['mean_floor'],
        mean_paired_surplus_positive=comparison['candidate']['mean_paired_surplus'] > 0,
        more_common_closed_improvements=sum(r['delta_floor'] > 1e-7 for r in common) > sum(r['delta_floor'] < -1e-7 for r in common),
        cycles_after90_in_at_least6=sum(r['candidate']['cycle_after90'] for r in rows) >= 6)


def human_report(study, evaluations, native_count):
    lines = ['# BTC5M 批次搜尋結果', '',
        '**研究搜尋結果；不升級模型、不部署、不代表實單獲利。**', '',
        f"已完成候選：{sum(r['state']=='COMPLETE' for r in study.trials)}；宣告總預算：{study.spec['trials']}。",
        f'本工作新增 native 回放：{native_count}。其餘相同輸入的完成結果由核對雜湊的快取取得。',
        '全部是已消耗市場 1977248 的執行情境，不是多市場勝率或未見市場驗證。', '',
        '## 搜尋方式', '',
        '先涵蓋公式族群，再依已完成候選的 Pareto 集合進行鄰域變異，保留隨機探索。',
        '搜尋只讀兩個預先指定情境的分數；短名單凍結後再比較其他十個情境。', '',
        '## 短名單與成對結果', '']
    for number, item in evaluations.items():
        comp = item['comparison']; a = comp['baseline']; b = comp['candidate']
        lines += [f'### 候選 {number}', '',
            '參數：`' + json.dumps(item['params'], ensure_ascii=False, sort_keys=True) + '`', '',
            f"12 情境 floor 改善／惡化／相同：{comp['better']}／{comp['worse']}／{comp['same']}。",
            f"平均觀測 floor：{a['mean_floor']:.6f} → {b['mean_floor']:.6f}。",
            f"平均配對盈餘：{a['mean_paired_surplus']:.6f} → {b['mean_paired_surplus']:.6f}。",
            f"平均較佳結算分支：{a['mean_best_branch']:.6f} → {b['mean_best_branch']:.6f}。",
            f"平均成交回報數：{a['mean_receipts']:.3f} → {b['mean_receipts']:.3f}；期末未終結 owner：{b['total_pending']}。",
            '接續門檻：`' + json.dumps(item['continuation_gates'], ensure_ascii=False, sort_keys=True) + '`', '',
            '最大退步切片（保留，不只展示成功案例）：']
        for row in sorted(comp['rows'], key=lambda r:r['delta_floor'])[:3]:
            lines.append(f"- {row['scenario']}：floor 差 {row['delta_floor']:+.6f}，配對盈餘差 {row['delta_pairing']:+.6f}，pending={row['candidate']['pending']}。")
        lines += ['', '完整參數、雙分支、pending 投影、成交與逐步 trace 均保留於 STUDY／ROBUSTNESS／cache。', '']
    lines += ['## 證據邊界', '',
        '200bps 為條件費率；實際費率與來源時鐘認證仍未知。pending／EOF 不作已修復或零成本標籤。',
        '自動候選只改新被動曝險的軟成本；主動修復與交易合法性保持原版本。',
        'R65／R84 權重、Adam 與步數沒有更新。通過門檻仍須另外研究泛化及神經網路語義對齊。',
        '目前未做候選附近的專門參數擾動驗收，不把孤立高分宣稱為穩健區間。', '']
    return '\n'.join(lines)


def check_package():
    if socket.gethostname().upper() != 'DESKTOP-JIERAGF':
        raise RuntimeError('Native execution is restricted to the verified second PC')
    manifest = read_json(P / 'MANIFEST.json')
    for name, expected in manifest['files'].items():
        path = (P / name).resolve()
        if not path.is_relative_to(P) or file_hash(path) != expected:
            raise ValueError('Package file hash mismatch: ' + name)
    pins = read_json(P / 'SOURCE_PINS.json')
    backend = Path(pins['backend'])
    if file_hash(backend / 'hftbacktest/_hftbacktest.cp313-win_amd64.pyd') != pins['binary_sha256']:
        raise ValueError('Pinned native ABI/binary mismatch')
    parents = read_json(P / 'LEARNING_PARENT_PINS.json')
    for name, expected in parents.items():
        if file_hash(Path(name)) != expected:
            raise ValueError('Retained NN/Adam parent was changed')
    from checks_r87 import run as previous_checks
    from batch_component_checks import run as batch_checks
    components = {'r87': previous_checks(), 'batch': batch_checks()}
    return manifest, backend, parents, components


def resource_check():
    import psutil
    memory = psutil.virtual_memory().available / 1024**3
    disk = shutil.disk_usage(P).free / 1024**3
    if memory < 6 or disk < 8:
        raise RuntimeError(f'RESOURCE_PAUSE: free_ram_gb={memory:.3f}, free_disk_gb={disk:.3f}')


def run_study(args):
    manifest, backend, parents, components = check_package()
    spec = read_json(P / 'SEARCH_SPEC.json')
    if not 1 <= args.through <= spec['trials']:
        raise ValueError('Requested trial count exceeds the declared search budget')
    if args.check_only:
        print(json.dumps(dict(status='PASS', native_executed=0, components=components,
                              budget=spec['trials'], requested=args.through, parents=len(parents))))
        return
    out = Path(os.environ['BTC5M_LAN_RESULT_DIR']).resolve()
    result_root = Path('C:/BTC5M-worker/.lan_worker_v1/results').resolve()
    if not out.is_relative_to(result_root):
        raise ValueError('Output is not a worker result directory')
    out.mkdir(parents=True, exist_ok=True)
    with WriterLock(out / '.writer.lock'):
        if (out / 'RESULT.json').exists():
            raise RuntimeError('Completed job is immutable; use a new continuation job')
        if args.resume_from:
            source = Path(args.resume_from).resolve()
            if source == out or not source.is_relative_to(result_root):
                raise ValueError('Invalid continuation source')
            previous = read_json(source / 'RESULT.json')
            if previous['manifest_sha256'] != file_hash(P / 'MANIFEST.json') or previous['status'] != 'COMPLETE_UNPROMOTED':
                raise ValueError('Continuation source must be a completed, same-package study')
            for name, sha in previous['artifacts'].items():
                if file_hash(source / name) != sha:
                    raise ValueError('Continuation artifact mismatch: ' + name)
            if any(out.iterdir()) and any(p.name != '.writer.lock' for p in out.iterdir()):
                raise RuntimeError('Continuation output is not empty')
            for name in previous['artifacts']:
                if name == 'STUDY.json' or name.startswith('cache/'):
                    dest = out / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / name, dest)
            atomic_json(out / 'CONTINUATION_SOURCE.json', dict(path=str(source), result_sha256=file_hash(source/'RESULT.json')))
        study = Study(out / 'STUDY.json', spec)
        atomic_json(out / 'EXECUTION_PLAN.json', dict(through=args.through, shortlist=args.shortlist,
            source_package_sha256=file_hash(P/'MANIFEST.json'), screen=SCREEN, robustness=ALL_CASES,
            neural_updates=0, live_changes=0, capital_cap=None, resume_from=args.resume_from))
        started, actual_native, current = time.perf_counter(), 0, None
        try:
            from run_loop import run, native_modules, build_events
            h, binary = native_modules(backend)
            public = zread(P / 'PUBLIC_1977248.json.gz')
            execution = zread(P / 'EXECUTION_1977248.json.gz')
            arrays = {}
            references = {suffix(c): summarize(zread(P / ('traces/CELL_RECENT_' + suffix(c) + '.json.gz'))) for c in ALL_CASES}
            atomic_json(out / 'BASELINES.json', references)

            def progress(phase, **extra):
                record = dict(phase=phase, current=current, completed_trials=sum(r['state']=='COMPLETE' for r in study.trials),
                    through=args.through, native_this_job=actual_native, elapsed_seconds=time.perf_counter()-started, **extra)
                atomic_json(out / 'PROGRESS.json', record)
                print(json.dumps(record), flush=True)

            def evaluate(config, case):
                nonlocal actual_native, current
                normalized = effective_config(config) if isinstance(config, dict) else config
                key = digest([file_hash(P/'MANIFEST.json'), normalized, case])
                tracepath, metapath = out/'cache'/f'{key}.json.gz', out/'cache'/f'{key}.meta.json'
                current = dict(config=normalized, scenario=suffix(case), cache_key=key)
                if metapath.exists():
                    meta = read_json(metapath)
                    if meta['state'] != 'COMPLETE':
                        raise RuntimeError('Incomplete native scenario needs explicit forensic recovery; not automatically rerun')
                    if file_hash(tracepath) != meta['trace_sha256']:
                        raise ValueError('Cached replay checksum mismatch')
                    return meta['metrics'], tracepath
                resource_check()
                atomic_json(metapath, dict(state='RUNNING', scenario=suffix(case), config=normalized, attempts=1))
                progress('NATIVE_SCENARIO')
                if case[0] not in arrays:
                    arrays[case[0]] = build_events(execution, public, h, 'SOURCE_RECEIVE', case[0])
                arr, feed = arrays[case[0]]
                actual_native += 1
                try:
                    trace = run(public, arr, h, binary, normalized, case[2], case[1])
                    trace.update(case=suffix(case), feed=feed)
                    metrics = summarize(trace)
                    zwrite(tracepath, trace)
                    atomic_json(metapath, dict(state='COMPLETE', scenario=suffix(case), config=normalized,
                        attempts=1, trace_sha256=file_hash(tracepath), metrics=metrics))
                    return metrics, tracepath
                except Exception as exc:
                    if hasattr(exc, 'r87_partial'):
                        zwrite(out/'cache'/f'{key}.FAILED_PREFIX.json.gz', exc.r87_partial)
                    atomic_json(metapath, dict(state='FAILED', scenario=suffix(case), config=normalized,
                        attempts=1, error=repr(exc), traceback=traceback.format_exc()))
                    raise

            progress('EXACT_CONTROL')
            control, controlpath = evaluate('OBSERVER_ONLY', SCREEN[0])
            old = zread(P / 'traces/CELL_RECENT_early_L250_P0.json.gz')
            new = zread(controlpath)
            fields = ('final','events','raw_receipts','net_pairing','residual_cost','unresolved','active_outcomes','negative_floor_area','worst_floor')
            equal = {k: old[k] == new[k] for k in fields}
            equal['shared_decisions'] = [{k:v for k,v in row.items() if k not in ('responsibility_observer','attempt_observer')}
                                         for row in new['decisions']] == old['decisions']
            atomic_json(out/'EXACT_CONTROL.json', dict(equal=equal, native_control_or_verified_cache=True))
            if not all(equal.values()):
                raise ValueError('Observer-only replay changed the frozen baseline')
            while len([r for r in study.trials if r['state']=='COMPLETE']) < args.through:
                trial = study.ask()
                if trial['number'] >= args.through:
                    break
                study.begin(trial['number'])
                rows = {}
                for case in SCREEN:
                    rows[suffix(case)], _ = evaluate(trial['params'], case)
                comparison = compare(rows, references)
                metrics = comparison['candidate']
                study.tell(trial['number'], [metrics[k] for k in spec['objectives']], comparison)
                progress('TRIAL_COMPLETE', trial=trial['number'], sampling=trial['sampling'],
                    parent_trial=trial['parent_trial'], objectives=study.trials[trial['number']]['objectives'])
                if (trial['number']+1) % 4 == 0:
                    atomic_json(out/f'BATCH_{trial["number"]+1:03}.json', dict(
                        completed=trial['number']+1, pareto_trials=[r['number'] for r in pareto(study.trials)],
                        trials=study.trials, not_independent_markets=True))
            shortlist = study.shortlist(args.shortlist)
            atomic_json(out/'FROZEN_SHORTLIST.json', dict(trials=shortlist,
                screen_only_selection=True, study_sha256=file_hash(out/'STUDY.json')))
            evaluations = {}
            for number in shortlist:
                trial = study.trials[number]
                rows = {suffix(case): evaluate(trial['params'], case)[0] for case in ALL_CASES}
                comparison = compare(rows, references)
                gates = continuation_gates(comparison)
                evaluations[str(number)] = dict(params=trial['params'], comparison=comparison,
                    continuation_gates=gates, continuation_candidate=all(gates.values()),
                    promoted=False, not_OOS=True)
                atomic_json(out/'ROBUSTNESS.json', evaluations)
                progress('ROBUSTNESS_COMPLETE', trial=number, gates=gates)
            for name, expected in parents.items():
                if file_hash(Path(name)) != expected:
                    raise ValueError('Retained weights/Adam changed during the job')
            atomic_bytes(out/'REPORT_ZH.md', human_report(study, evaluations, actual_native).encode('utf-8'))
            artifacts = {str(p.relative_to(out)).replace('\\','/'):file_hash(p) for p in out.rglob('*')
                         if p.is_file() and p.name not in ('.writer.lock','PROGRESS.json') and not p.name.endswith('.tmp')}
            result = dict(status='COMPLETE_UNPROMOTED', manifest_sha256=file_hash(P/'MANIFEST.json'),
                spec_sha256=digest(spec), completed_trials=sum(r['state']=='COMPLETE' for r in study.trials),
                budget=spec['trials'], native_this_job=actual_native, shortlist=shortlist, evaluations=evaluations,
                artifacts=artifacts, components=components, parents_unchanged=parents,
                unique_markets=1, consumed_development=True, neural_updates=0, live_changes=0,
                capital_cap=None, promoted=False, seconds=time.perf_counter()-started,
                limitations=['ONE_CONSUMED_MARKET_NOT_OOS','CONDITIONAL_200BPS','SOURCE_CLOCK_UNKNOWN',
                             'SOFT_PASSIVE_ACQUISITION_OVERLAY_NOT_NN_LEARNING','NO_AUTOMATIC_PROMOTION'])
            atomic_json(out/'RESULT.json', result)
            progress('COMPLETE_UNPROMOTED')
        except Exception as exc:
            atomic_json(out/'FAILURE.json', dict(error=repr(exc), traceback=traceback.format_exc(),
                current=current, native_this_job=actual_native, no_automatic_retry=True))
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--through', type=int, default=12)
    parser.add_argument('--shortlist', type=int, default=3, choices=range(1, 5))
    parser.add_argument('--resume-from', default=None)
    run_study(parser.parse_args())
