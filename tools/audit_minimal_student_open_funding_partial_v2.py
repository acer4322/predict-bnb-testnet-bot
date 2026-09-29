"""Audit the completed episode of an intentionally stopped training batch.
No new native run, no fit, no reclassification of partial data as complete.
"""
from pathlib import Path
import gzip
import hashlib
import importlib.util
import json
import os
import statistics
import time
import traceback

JOB='minimal-student-open-funding-train-logfix-20260911-v2'
BASE=Path('C:/BTC5M-worker/.lan_worker_v1')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);started=time.monotonic()
    result=dict(version='OPEN_FUNDING_COMPLETED_EPISODE_AUDIT_V2',new_native_runs=0,new_fits=0)
    try:
        status=json.loads((BASE/'jobs'/JOB/'status.json').read_text(encoding='utf-8'))
        assert status['state']=='cancelled'
        p=BASE/'results'/JOB/'COMPACT.json';src=json.loads(p.read_text(encoding='utf-8'))
        assert src['native_complete']==1 and src['joint_parameter_updates']==0
        assert src['native_attempts']==2 and len(src['evaluations'])==1
        module=BASE/'staging/audit_minimal_student_open_funding_v1.py'
        sp=importlib.util.spec_from_file_location('independent_audit',module)
        audit=importlib.util.module_from_spec(sp);sp.loader.exec_module(audit)
        pack=BASE/'staging/minimal_student_open_funding_train_logfix_20260911_v2'
        manifest=json.loads((pack/'MANIFEST.json').read_text(encoding='utf-8'))
        sources={}
        for mid in (2022527,2022538):
            name=f'input_{mid}.json.gz';f=pack/name
            assert sha(f)==manifest['files'][name]['sha256']
            with gzip.open(f,'rb') as stream:data=stream.read(8*1024**2+1)
            assert len(data)<=8*1024**2;sources[mid]=audit.source_path(json.loads(data))
        unit=float(statistics.median(u+d for paths in sources.values() for _,u,d,c in paths))
        assert unit==src['fixed_train_share_unit']
        row=audit.audit_one(src['evaluations'][0],sources[2022527],unit)
        assert row['resource_censor_events']==51 and row['unresolved_owners']==20
        result.update(verdict='COMPLETED_EPISODE_ACCOUNTING_AND_NONBINDING_FUNDING_VERIFIED_TRAINING_NOT_COMPLETE',
            actual_job_state='cancelled',last_compact_verdict_is_stale_running=True,
            source_result_sha256=sha(p),status_sha256=sha(BASE/'jobs'/JOB/'status.json'),
            completed_native_episodes=1,model_updates=0,frozen_candidate_exists=False,
            audited_episode=row,unit_test_count=src['unit_tests']['passed'],
            funding_mode=src['funding_mode'],capital_cap=None,
            trace_terminal_records=src['evaluations'][0]['trace']['terminal_owner_records'],
            pure_cost_reward_removed=True,train_share_unit_not_a_cap=unit,
            no_economic_promotion=True,
            stop_reason='Resource32 hit51times and20owners lacked terminal outcome at source end; first completed result censored. Monetary demand/loss size was NOT a stop rule.',
            partial_second_episode_not_scored=True,
            retired_log_failure_not_counted_as_complete=True)
    except Exception as e:
        result.update(verdict='PARTIAL_AUDIT_ERROR',error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(limit=8))
    result['elapsed_seconds']=time.monotonic()-started
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result),flush=True)
    if result['verdict']=='PARTIAL_AUDIT_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
