"""Coordinate eight already frozen remote jobs, one at a time; never host native."""
import json
import time
from prepare_btc5m_transfer_structural_v1 import R, STEM, read, dump
import run_btc5m_transfer_structural_worker_v1 as worker
from verify_btc5m_transfer_structural_v1 import inspect_job


def main():
    assert read(R/(STEM+'_PARITY.json'))['status']=='PASS'
    completed=[]
    for index, job in enumerate(worker.jobs()[1:],1):
        state=worker.dispatch.cmd_status(worker.HOST,job['job_id'])
        if state['state']=='missing':
            accepted=worker.submit(index)
            assert accepted.get('accepted'),accepted
            print(json.dumps(dict(event='SUBMITTED',index=index,job_id=job['job_id'])),flush=True)
        elif worker.artifact(job,'AUDIT').exists():
            existing=read(worker.artifact(job,'AUDIT'))
            assert existing['execution_status']=='PASS' and state['state']=='succeeded'
            completed.append(job['job_id']);continue
        dump('PROGRESS',dict(status='RUNNING',current_job=job['job_id'],completed=completed,paired_total=8))
        while True:
            state=worker.dispatch.cmd_status(worker.HOST,job['job_id'])
            if state['state'] in ('succeeded','failed','runner_error','cancelled'):break
            assert state['state'] in ('running','queued'),state
            time.sleep(15)
        worker.collect(index)
        audit=inspect_job(index)
        print(json.dumps(dict(event='AUDITED',index=index,**audit)),flush=True)
        if audit['execution_status']!='PASS':
            dump('PROGRESS',dict(status='STOPPED_EXECUTION_FAILURE',current_job=job['job_id'],completed=completed,
                remaining='NOT_DISPATCHED',failure=audit));raise SystemExit(2)
        completed.append(job['job_id'])
    dump('PROGRESS',dict(status='COMPLETE',completed=completed,paired_total=8,paired_completed=8))


if __name__=='__main__':main()
