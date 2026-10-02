"""Sequential isolated markets, one named submission, no retries."""
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS'):os.environ[key]='1'
import argparse,hashlib,json,platform,subprocess,sys,time
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
def main(check):
    assert platform.node().upper()=='DESKTOP-JIERAGF'
    manifest=json.loads((P/'MANIFEST.json').read_text());jobs=json.loads((P/'JOBS.json').read_text())
    for n,h in manifest['files'].items():assert hashlib.sha256((P/n).read_bytes()).hexdigest()==h,n
    candidate=json.loads((P/'CANDIDATE.json').read_text())
    assert hashlib.sha256((Path(candidate['backend'])/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==candidate['sha256']
    if check:
        for j in jobs:assert not (Path('C:/BTC5M-worker/.tmp')/('target_core_cycle_active_v8_'+j['name'])).exists(), j['name']
        for queue in ('risk','log'):
            j=next(j for j in jobs if j['queue']==queue);argv,env=command(j,check=True)
            cp=subprocess.run(argv,cwd=P,env=env,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
            assert cp.returncode==0,(cp.stdout[-1000:],cp.stderr[-4000:])
        print(json.dumps({'status':'LOAD_ONLY_PASS','hashes':len(manifest['files']),'native_executed':0,'fits':0}),flush=True);return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);assert not (out/'RESULT.json').exists()
    began=time.monotonic();completed=[]
    for j in jobs:
        arm=out/'arms'/j['name'];arm.mkdir(parents=True,exist_ok=False)
        argv,env=command(j,arm);save(arm/'EXECUTION.json',{'job':j,'argv':argv,'env_v12':j['env_v12'],'fits':0})
        with (arm/'stdout.log').open('xb') as so,(arm/'stderr.log').open('xb') as se:
            rc=subprocess.run(argv,cwd=P,env=env,stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW).returncode
        raw=json.loads((arm/'result.json').read_text()) if (arm/'result.json').exists() else {'status':'MISSING_RESULT'}
        completed.append(dict(name=j['name'],market=j['market_id'],arm=j['arm'],rc=rc,status=raw['status']))
        save(out/'PROGRESS.json',{'completed':len(completed),'expected':len(jobs),'last':completed[-1],'elapsed_seconds':time.monotonic()-began})
        print(json.dumps(completed[-1]),flush=True)
        if rc or raw['status']!='COMPLETE':
            save(out/'RESULT.json',{'status':'STOPPED_PATH_ERROR','completed':completed,'not_started':[r['name'] for r in jobs[len(completed):]],'elapsed_seconds':time.monotonic()-began,'fits':0})
            raise SystemExit(2)
    save(out/'RESULT.json',{'status':'COMPLETE','job_id':os.environ['BTC5M_LAN_WORKER_JOB_ID'],'completed':completed,'elapsed_seconds':time.monotonic()-began,'fits':0,'live_changes':0})
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');main(ap.parse_args().check_only)
