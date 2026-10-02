"""One bounded max4 job; isolated single-thread processes, no retry or fit."""
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS'):os.environ[key]='1'
import argparse,hashlib,json,platform,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from pathlib import Path
P=Path(__file__).resolve().parent
def save(p,v):p.write_text(json.dumps(v,indent=2,allow_nan=False),encoding='utf8')
def command(j,out=None,check=False):
    env=os.environ.copy()
    for k in list(env):
        if k.startswith(('V12','EOF_')):env.pop(k,None)
    env.update(j['env_v12']);env.update(OML_MARKET=str(j['market_id']),OML_QUEUE=j['queue'],OML_LATENCY=str(j['latency_ms']))
    if out:env['BTC5M_LAN_RESULT_DIR']=str(out)
    if check:env.pop('BTC5M_LAN_RESULT_DIR',None)
    return [sys.executable,str(P/'single_path.py')]+(['--check-only'] if check else []),env
def path_job(j,out):
    arm=out/'arms'/j['name'];arm.mkdir(parents=True,exist_ok=False)
    argv,env=command(j,arm);save(arm/'EXECUTION.json',{'job':j,'argv':argv,'env_v12':j['env_v12'],'fits':0})
    with (arm/'stdout.log').open('xb') as so,(arm/'stderr.log').open('xb') as se:
        rc=subprocess.run(argv,cwd=P,env=env,stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW).returncode
    raw=json.loads((arm/'result.json').read_text()) if (arm/'result.json').exists() else {'status':'MISSING_RESULT'}
    gate=raw.get('safety_gate',{})
    legacy_only=(raw['status']=='COMPLETE' and rc==2 and gate.get('active_matches_opportunity') is False and all(v for k,v in gate.items() if k not in ('pass','active_matches_opportunity')))
    valid=raw['status']=='COMPLETE' and (rc==0 or legacy_only)
    clock=json.loads((arm/'execution_clock.json').read_text()) if (arm/'execution_clock.json').exists() else {}
    valid=valid and clock.get('capture_error') is None and not clock.get('invalid') and clock.get('frames')==clock.get('source_updates')==clock.get('producer_calls')
    row=dict(name=j['name'],market=j['market_id'],arm=j['arm'],rc=rc,status=raw['status'],execution_complete=valid,legacy_active_matches_opportunity=gate.get('active_matches_opportunity'),all_old_gates_passed=False)
    save(arm/'ORCHESTRATION_AUDIT.json',row)
    return row
def main(check):
    assert platform.node().upper()=='DESKTOP-JIERAGF'
    manifest=json.loads((P/'MANIFEST.json').read_text());jobs=json.loads((P/'JOBS.json').read_text())
    for n,h in manifest['files'].items():assert hashlib.sha256((P/n).read_bytes()).hexdigest()==h,n
    candidate=json.loads((P/'CANDIDATE.json').read_text());assert hashlib.sha256((Path(candidate['backend'])/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==candidate['sha256']
    reused=json.loads((P/'REUSED.json').read_text())
    reusedroot=Path('C:/BTC5M-worker/.lan_worker_v1/results')/reused['job_id']/reused['path']
    for n,h in reused['files'].items():assert hashlib.sha256((reusedroot/n).read_bytes()).hexdigest()==h,n
    if check:
        for j in jobs:assert not (Path('C:/BTC5M-worker/.tmp')/('target_core_cycle_active_v8_'+j['name'])).exists(),j['name']
        for queue in ('risk','log'):
            j=next(j for j in jobs if j['queue']==queue);argv,env=command(j,check=True)
            cp=subprocess.run(argv,cwd=P,env=env,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
            assert cp.returncode==0,(cp.stdout[-1000:],cp.stderr[-4000:])
        print(json.dumps({'status':'LOAD_ONLY_PASS','hashes':len(manifest['files']),'native_executed':0,'fits':0,'reused_first_BASE_hashes_verified':True}),flush=True);return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);assert not (out/'RESULT.json').exists()
    began=time.monotonic();completed=[];started=[];iterator=iter(jobs);failed=False
    with ThreadPoolExecutor(max_workers=4) as pool:
        active={}
        def launch():
            j=next(iterator,None)
            if j is not None:started.append(j['name']);active[pool.submit(path_job,j,out)]=j
        for _ in range(4):launch()
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                j=active.pop(future)
                try:row=future.result()
                except Exception as exc:row={'name':j['name'],'market':j['market_id'],'arm':j['arm'],'status':'ORCHESTRATION_EXCEPTION','error':repr(exc),'execution_complete':False}
                completed.append(row);failed=failed or not row['execution_complete']
                save(out/'PROGRESS.json',{'completed':len(completed),'expected_new':len(jobs),'reused':1,'last':row,'running':[j['name'] for j in active.values()],'elapsed_seconds':time.monotonic()-began})
                print(json.dumps(row),flush=True)
            if not failed:
                while len(active)<4:
                    before=len(active);launch()
                    if len(active)==before:break
    save(out/'RESULT.json',{'status':'STOPPED_EXECUTION_ERROR' if failed else 'COMPLETE','job_id':os.environ['BTC5M_LAN_WORKER_JOB_ID'],'completed':completed,'reused':reused,'not_started':[j['name'] for j in jobs if j['name'] not in started],'elapsed_seconds':time.monotonic()-began,'fits':0,'live_changes':0,'all_old_gates_passed':False})
    if failed:raise SystemExit(2)
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');main(ap.parse_args().check_only)
