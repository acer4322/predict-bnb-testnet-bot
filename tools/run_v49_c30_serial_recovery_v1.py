from __future__ import annotations
import json,time,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import btc5m_lan_dispatch_v1 as d

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research'
HOST='btc5m-worker'
PKG='.lan_worker_v1/staging/v49_generalization_c30_20260914_v2'
PROGRESS=R/'BTC5M_V49_GENERALIZATION_C30_20260914_SERIAL_RECOVERY_PROGRESS.json'
LOCK=R/'BTC5M_V49_GENERALIZATION_C30_20260914_SERIAL_RECOVERY.lock'
MARKETS=[2021315,2021302,2020663,2019427,2019008,2018991,2018988,2018854,2018847,2018839,2018666,2018407,2018404]

def jid(mid): return f'v49-c30-{mid}-20260914-v3-serial'
def argv(mid): return ['.venv/Scripts/python.exe','money_runner.py','--market-id',str(mid),'--mode','NO_DIRECTION','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE','--repair-route','NONE','--direction-rule','INVENTORY']
def write(status,completed,current=None,extra=None):
    payload={'version':'BTC5M_V49_C30_SERIAL_RECOVERY_V1','status':status,'markets':MARKETS,'completed':completed,'current':current,'updated_at':time.time()}
    if extra: payload.update(extra)
    tmp=PROGRESS.with_suffix('.tmp'); tmp.write_text(json.dumps(payload,indent=2),encoding='utf-8'); os.replace(tmp,PROGRESS)
def active_heavy():
    xs=d.cmd_status(HOST,None)
    return [x for x in xs if x.get('state') in ('queued','running')]
def main():
    if LOCK.exists(): raise RuntimeError('serial recovery lock already exists')
    LOCK.write_text(json.dumps({'pid':os.getpid(),'created_at':time.time()}),encoding='utf-8')
    completed=[]
    try:
        write('STARTED',completed)
        for mid in MARKETS:
            job=jid(mid)
            st=d.cmd_status(HOST,job)
            if st.get('state')=='succeeded':
                d.cmd_collect(HOST,job)
                completed.append({'market_id':mid,'job_id':job,'state':'succeeded','attached_existing':True,'started_at':st.get('started_at'),'ended_at':st.get('ended_at')})
                write('RUNNING',completed)
                continue
            if st.get('state') in ('running','queued'):
                pass
            elif st.get('state')=='missing':
                # Strict global serial gate. No submit while any other worker job is queued/running.
                while True:
                    a=active_heavy()
                    if not a: break
                    write('WAIT_GLOBAL_IDLE',completed,current={'market_id':mid,'job_id':job},extra={'active_jobs':[x.get('job_id') for x in a]})
                    time.sleep(1)
                sub=d.cmd_submit(HOST,argv(mid),PKG,job,4,8.0,90.0,True,'cpu',0,4096,70.0,False,False,None)
                if not sub.get('accepted'): raise RuntimeError(f'submit rejected {job}: {sub}')
            else:
                raise RuntimeError(f'existing non-retryable state {job}: {st}')
            write('RUNNING',completed,current={'market_id':mid,'job_id':job})
            while True:
                st=d.cmd_status(HOST,job)
                state=st.get('state')
                if state=='succeeded': break
                if state in ('failed','runner_error','cancelled','launch_failed'):
                    raise RuntimeError(f'job failed {job}: {st}')
                # Assert no other heavy job is simultaneously active.
                a=active_heavy()
                other=[x for x in a if x.get('job_id')!=job]
                if other: raise RuntimeError(f'GLOBAL_SERIAL_VIOLATION while {job}: {[x.get("job_id") for x in other]}')
                time.sleep(1)
            col=d.cmd_collect(HOST,job)
            completed.append({'market_id':mid,'job_id':job,'state':'succeeded','started_at':st.get('started_at'),'ended_at':st.get('ended_at'),'elapsed_seconds':st.get('elapsed_seconds'),'collected':col.get('local_path')})
            write('RUNNING',completed)
        # Final pairwise non-overlap audit for this recovery set.
        ordered=sorted(completed,key=lambda x:x.get('started_at') or 0)
        overlaps=[]
        for a,b in zip(ordered,ordered[1:]):
            if a.get('ended_at') is not None and b.get('started_at') is not None and b['started_at'] < a['ended_at']:
                overlaps.append([a['job_id'],b['job_id'],a['ended_at']-b['started_at']])
        if overlaps: raise RuntimeError(f'final recovery overlap audit failed: {overlaps}')
        write('COMPLETE',completed,extra={'overlaps':[]})
        print(json.dumps({'status':'COMPLETE','count':len(completed),'overlaps':[],'jobs':[x['job_id'] for x in completed]},indent=2))
    finally:
        LOCK.unlink(missing_ok=True)
if __name__=='__main__': main()
