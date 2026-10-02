#!/usr/bin/env python3
"""Project entry point. Read README_BATCH_SEARCH_ZH.md before native dispatch.

self-test and prepare are local/lightweight. run/continue use the project's
existing authenticated second-PC dispatcher and pinned interpreter only.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from btc5m_batch_search_lib.engine import atomic_json, file_hash, read_json
from btc5m_batch_search_lib.prepare import DEFAULT_RUN, R87, prepare, verify_package

DEFAULT_JOB = 'btc5m-batch-search-20260921-r88'


def transport(repo):
    path=repo/R87/'dispatch.py'
    spec=importlib.util.spec_from_file_location('_btc5m_r87_transport',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_job(job):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',job):
        raise ValueError('Invalid job identifier')
    return job


def no_other_work(t):
    state=t.global_state()
    if state['nonterminal'] or state['other_processes']:
        raise RuntimeError('Worker has live/queued processes; attach/inspect instead of adding another heavy job: '+json.dumps(state))
    return state


def verify_collected(repo, directory, job):
    returns=repo/'data/research/lan_worker_returns'/job
    result=read_json(returns/'RESULT.json')
    if result['manifest_sha256']!=file_hash(directory/'MANIFEST.json'):
        raise ValueError('Returned package identity mismatch')
    if result['status']!='COMPLETE_UNPROMOTED':
        raise RuntimeError('Study is not complete; preserve and inspect partial results')
    for name,sha in result['artifacts'].items():
        path=(returns/name).resolve()
        if not path.is_relative_to(returns.resolve()) or file_hash(path)!=sha:
            raise ValueError('Returned artifact hash mismatch: '+name)
    check=read_json(returns/'EXACT_CONTROL.json')
    if not all(check['equal'].values()):
        raise ValueError('Observer control did not reproduce')
    atomic_json(directory/(job+'_COLLECTION_VERIFIED.json'),dict(status='PASS',job_id=job,
        result_sha256=file_hash(returns/'RESULT.json'),artifacts=len(result['artifacts']),
        completed_trials=result['completed_trials'],native_this_job=result['native_this_job'],promoted=False))
    return dict(status='COLLECTED_AND_HASH_VERIFIED',job_id=job,result_dir=str(returns),
        completed_trials=result['completed_trials'],native_this_job=result['native_this_job'],
        shortlisted=result['shortlist'],passed_continuation=[k for k,v in result['evaluations'].items() if v['continuation_candidate']],
        unique_markets=1,not_OOS=True,promoted=False,neural_updates=0,live_changes=0)


def dispatch(repo,directory,job,through,from_job=None):
    valid_job(job);manifest=verify_package(directory)
    spec=read_json(directory/'SEARCH_SPEC.json')
    if not 1<=through<=spec['trials']:
        raise ValueError('Trial count outside the frozen budget')
    if from_job is None and through>12:
        raise ValueError('Run the initial <=12-trial batch first; then use an explicit continuation job')
    t=transport(repo);identity=t.identity()
    exact=t.d.cmd_status(t.HOST,job)
    if exact['state']!='missing':
        return dict(status='ATTACHED_EXISTING_JOB_NOT_RESUBMITTED',job=exact)
    if (repo/'data/research/lan_worker_returns'/job).exists():
        raise RuntimeError('Local result directory already exists; reconcile instead of replacing it')
    submit_marker=directory/(job+'_SUBMIT.json')
    if submit_marker.exists():
        raise RuntimeError('A submit was already attempted. Inspect exact status; no automatic duplicate submission')
    global_before=no_other_work(t);probe=t.d.cmd_probe(t.HOST)
    if probe['hostname']!='DESKTOP-JIERAGF' or probe['memory']['free_gb']<8 or probe['cpu_pct']>80 or probe['robocopy_active']:
        raise RuntimeError('Worker identity/capacity preflight failed')
    expected_python=r'C:\BTC5M-worker\.venv\Scripts\python.exe'
    if t.d.REMOTE_PY.lower()!=expected_python.lower():
        raise ValueError('Dispatcher interpreter differs from the pinned worker interpreter')
    source=None
    if from_job:
        valid_job(from_job)
        if from_job==job:raise ValueError('Continuation requires a new job ID')
        old=t.d.cmd_status(t.HOST,from_job)
        if old['state']!='succeeded':
            raise RuntimeError('Continue only from a reconciled completed batch; failed/UNKNOWN needs forensic recovery')
        source=old['result_dir']
    remote_root='C:/BTC5M-worker/.lan_worker_v1/staging/'+directory.name
    exists=t.remote('import pathlib,json; print(json.dumps(pathlib.Path('+repr(remote_root)+').exists()))')
    if exists:
        stage=dict(remote_absolute=remote_root.replace('/','\\'),reused_without_overwrite=True)
    else:
        stage=t.d.cmd_stage(t.HOST,str(directory))
    check_cmd=subprocess.list2cmdline([t.d.REMOTE_PY,stage['remote_absolute']+'\\worker.py','--check-only','--through',str(through)])
    load=t.d.parse_json_output(t.strict(check_cmd,45))
    if load.get('status')!='PASS' or load.get('native_executed')!=0:
        raise RuntimeError('Worker load-only checks failed')
    atomic_json(directory/(job+'_PREFLIGHT.json'),dict(status='PASS',identity=identity,probe=probe,
        global_before=global_before,exact_before=exact,stage=stage,load_only=load,
        package_sha256=file_hash(directory/'MANIFEST.json'),through=through,from_job=from_job))
    # Recheck directly before the single intentional submission.
    t.identity();no_other_work(t)
    if t.d.cmd_status(t.HOST,job)['state']!='missing':
        raise RuntimeError('Named job appeared during preflight; attach instead')
    command=[t.d.REMOTE_PY,stage['remote_absolute']+'\\worker.py','--through',str(through)]
    if source:command+=['--resume-from',source]
    atomic_json(submit_marker,dict(status='ATTEMPT_IN_PROGRESS',attempts=1,job_id=job,through=through))
    # The dispatcher owns actual process execution/collection, not the chat.
    accepted=t.d.cmd_submit(t.HOST,command,'.',job,4,8,80,auto_collect=True)
    atomic_json(submit_marker,accepted)
    return dict(status='SUBMIT_RESPONSE',job_id=job,response=accepted,through=through,
        max_threads=4,neural_updates=0,live_changes=0,promoted=False)


def self_test(repo):
    suite=unittest.defaultTestLoader.discover(str(repo/'tests'),pattern='test_btc5m_batch_search_v1.py')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun>0 else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['self-test','prepare','run','continue','status','collect'])
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--run-dir',default=DEFAULT_RUN)
    parser.add_argument('--spec',type=Path)
    parser.add_argument('--job-id',default=DEFAULT_JOB)
    parser.add_argument('--from-job')
    parser.add_argument('--through',type=int,default=12)
    args=parser.parse_args();repo=args.repo.resolve()
    if args.action=='self-test':return self_test(repo)
    directory=(repo/args.run_dir).resolve()
    if not directory.is_relative_to((repo/'data/research').resolve()):
        raise ValueError('Run directory must be inside project research')
    valid_job(args.job_id)
    if args.action=='prepare':
        directory=prepare(repo,args.run_dir,args.spec)
        answer=dict(status='PREPARED_NO_NATIVE',run_dir=str(directory),manifest_sha256=file_hash(directory/'MANIFEST.json'))
    elif args.action in ('run','continue'):
        if args.action=='continue' and not args.from_job:
            raise ValueError('--from-job is required for continuation')
        if args.action=='run' and args.from_job:
            raise ValueError('Use the explicit continue action')
        directory=prepare(repo,args.run_dir,args.spec)
        answer=dispatch(repo,directory,args.job_id,args.through,args.from_job)
    elif args.action=='status':
        t=transport(repo);t.identity();answer=t.d.cmd_status(t.HOST,args.job_id)
    else:
        verify_package(directory);t=transport(repo);t.identity();state=t.d.cmd_status(t.HOST,args.job_id)
        if state['state'] not in ('succeeded','failed','runner_error','cancelled'):
            raise RuntimeError('Job is not terminal; attach rather than collect/replace')
        collected=t.d.cmd_collect(t.HOST,args.job_id)
        atomic_json(directory/(args.job_id+'_COLLECT.json'),dict(state=state,collected=collected))
        answer=verify_collected(repo,directory,args.job_id) if state['state']=='succeeded' else dict(status='FAILED_OUTPUTS_PRESERVED',job=state,collected=collected)
    print(json.dumps(answer,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(dict(status='STOPPED_NOT_RETRIED',error=repr(exc)),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
