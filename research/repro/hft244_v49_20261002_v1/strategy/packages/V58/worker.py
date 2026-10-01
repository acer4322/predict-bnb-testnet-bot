"""Original V50 scale plus STOP290; 29 consumed pairs and one inert control."""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS'):os.environ[k]='1'
import argparse,hashlib,json,socket,subprocess,sys,time,traceback
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from pathlib import Path
import psutil
from analyze import read,audit_path
from work_audit import audit as work_audit
from risk_audit import audit as risk_audit
from intent_audit import audit as intent_audit
from qualified_audit import audit as qualified_audit
from governor_audit import audit as governor_audit

P=Path(__file__).resolve().parent;W=Path('C:/BTC5M-worker');STAGE=W/'.lan_worker_v1/staging'
OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR',str(P/'not-dispatched')))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
def progress(phase,**kwargs):
    row=dict(phase=phase,**kwargs);save(OUT/'PROGRESS.json',row);print(json.dumps(row),flush=True)

def verify():
    assert socket.gethostname().upper()=='DESKTOP-JIERAGF' and P.parent==STAGE
    for n,h in read(P/'MANIFEST.json')['files'].items():assert sha(P/n)==h,n
    proof=read(P/'SOURCE_PARENT.json')
    for n,h in proof['source_hashes'].items():assert sha(STAGE/n)==h,n
    base=STAGE/'v12g_fresh30_generalization_20260927_v45/base'
    for n,h in read(base/'manifest.json')['files'].items():assert sha(base/n)==h,n
    plan=read(P/'PROTOCOL.json')
    assert len(plan['markets'])==29 and len(plan['jobs'])==30 and 2629444 not in plan['markets']
    assert plan['parallel_paths']==plan['max_threads']==4 and plan['native_threads_per_path']==1
    for b in plan['baseline'].values():
        for n,h in b['files'].items():assert sha(W/'.lan_worker_v1/results'/b['remote_relative']/n)==h,n
    for j in plan['jobs']:
        assert not (W/'.tmp'/f"target_core_cycle_active_v8_v12g58_{j['arm']}_{j['market']}").exists()
    candidate=read(P/'CANDIDATE.json')
    assert sha(Path(candidate['backend'])/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd')==candidate['sha256']
    from test_sizing import run
    assert run()['status']=='PASS'
    from test_demand_scale import run as scale_tests
    assert scale_tests()['status']=='PASS'
    from test_hard_stop import run as stop_tests
    assert stop_tests()['status']=='PASS'
    return plan

def command(job,check=False):
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('V12','EOF_')):env.pop(key,None)
    env.update(job['env_v12'])
    argv=[sys.executable,str(P/'overlay/run_variant.py'),'--variant','PREPARE','--market-id',str(job['market']),
          '--mode','NO_DIRECTION','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR',
          '--retention','0','--opportunity-mode','ONE_ACTIVE','--direction-rule','LEGACY']
    if check:argv.append('--check-only');env.pop('BTC5M_LAN_RESULT_DIR',None)
    return argv,env

def path_job(job,plan):
    arm=OUT/'arms'/f"v12g58_{job['arm']}_{job['market']}";arm.mkdir(parents=True,exist_ok=False)
    argv,env=command(job);env['BTC5M_LAN_RESULT_DIR']=str(arm)
    save(arm/'EXECUTION.json',dict(argv=argv,env_v12=job['env_v12'],resource_env={k:env[k] for k in ('OMP_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS')}))
    start=time.monotonic()
    with (arm/'stdout.log').open('xb') as so,(arm/'stderr.log').open('xb') as se:
        rc=subprocess.run(argv,cwd=W,env=env,stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW).returncode
    save(arm/'PROCESS.json',dict(rc=rc,seconds=time.monotonic()-start))
    try:
        baseline=W/'.lan_worker_v1/results'/plan['baseline'][str(job['market'])]['remote_relative']
        row=audit_path(arm,baseline if job['arm']=='INERT' else None);assert row['status']=='PASS'
        assert rc in (0,2) and ((rc==0)==(not row['failed_checks']))
        row.update(work_audit(arm,'SERVICE'));row.update(risk_audit(arm,None));row.update(intent_audit(arm,'OFF'))
        baseline=W/'.lan_worker_v1/results'/plan['baseline'][str(job['market'])]['remote_relative']
        row.update(qualified_audit(arm,baseline,job['env_v12']['V12G_QUALIFIED_CONTINUATION']))
        row.update(governor_audit(arm,baseline,job['mode'],job['cap'],compare_preflip=False))
        assert row['passive_ticket']==job['ticket']
        tr=read(arm/'clock_trace.json.gz')
        passive=[o for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW' and o['route']=='PASSIVE']
        assert passive and all(abs(o['qty']-job['ticket'])<1e-8 for o in passive)
        raw=read(arm/'result.json');assert raw['clock_smoke']['passive_ticket']==job['ticket']
        assert raw['v53_sizing']['active_pay_ticket']==15. and raw['v53_sizing']['gross_decision_threshold']==float(job['env_v12']['V12G_GROSS'])
        from scale_audit import audit as scale_audit
        row.update(scale_audit(arm,job))
        from stop_audit import audit as stop_audit
        row.update(stop_audit(arm,baseline,job))
        row['passive_size_verified_orders']=len(passive)
        pol=read(arm/'governor_trace.json.gz')
        if job['cap'] is None:
            assert pol['cap'] is None and pol['mode']=='BASE' and not pol['rows']
            assert 'V12G_MARKET_CAP' not in job['env_v12']
        else:assert pol['cap']==300.
        row['cap_configuration_and_runtime_verified']=True
    except Exception as exc:
        row=dict(status='PATH_ERROR',error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(limit=10))
    save(arm/'AUDIT.json',row)
    return dict(arm=job['arm'],market=job['market'],rc=rc,**row)

def main(check):
    plan=verify()
    if check:
        for j in plan['jobs'][:2]:
            argv,env=command(j,True);cp=subprocess.run(argv,cwd=W,env=env,capture_output=True,text=True,encoding='utf8',creationflags=subprocess.CREATE_NO_WINDOW)
            assert cp.returncode==0,(j['arm'],cp.stdout,cp.stderr)
        print(json.dumps(dict(status='PASS',native_executed=0,model_fits=0,paths=30,parallel_paths=4)));return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and not (OUT/'RESULT.json').exists()
    start=time.monotonic();result=dict(status='IN_PROGRESS',paths=[],resource_samples=[],model_fits=0,live_changes=0,parallel_paths=4)
    completed={};started=set();pending={};blocked=None;last_report=0.;next_index=2
    try:
        progress('INERT_CONTROL',market=plan['jobs'][0]['market']);started.add(0)
        control=path_job(plan['jobs'][0],plan);completed[0]=control;result['paths']=[control];save(OUT/'PARTIAL.json',result)
        if control['status']!='PASS':blocked='INERT_CONTROL_FAILED'
        if not blocked:
            progress('STOP290_SMOKE',market=plan['jobs'][1]['market']);started.add(1)
            smoke=path_job(plan['jobs'][1],plan);completed[1]=smoke;result['paths']=[completed[i] for i in sorted(completed)];save(OUT/'PARTIAL.json',result)
            if smoke['status']!='PASS':blocked='STOP290_SMOKE_FAILED'
        with ThreadPoolExecutor(max_workers=4) as pool:
            while pending or (next_index<len(plan['jobs']) and not blocked):
                free=psutil.virtual_memory().available/1024**3
                if free<6:blocked='RESOURCE_AVAILABLE_MEMORY_BELOW_6GB'
                while not blocked and next_index<len(plan['jobs']) and len(pending)<4:
                    i=next_index;next_index+=1;started.add(i);pending[pool.submit(path_job,plan['jobs'][i],plan)]=i
                now=time.monotonic()
                if now-last_report>=10:
                    sample=dict(t=time.time(),free_gb=round(free,3),cpu_pct=psutil.cpu_percent(),running=len(pending),completed=len(completed))
                    result['resource_samples'].append(sample)
                    progress('CANDIDATE_PATHS',completed_paths=len(completed),running=[dict(index=i,market=plan['jobs'][i]['market']) for i in sorted(pending.values())],blocked=blocked,resources=sample);last_report=now
                if not pending:break
                done,_=wait(pending,timeout=2,return_when=FIRST_COMPLETED)
                for f in done:
                    i=pending.pop(f)
                    try:row=f.result()
                    except Exception as exc:row=dict(arm=plan['jobs'][i]['arm'],market=plan['jobs'][i]['market'],status='PATH_ERROR',error=repr(exc),traceback=traceback.format_exc(limit=10))
                    row['schedule_index']=i;completed[i]=row
                    if row['status']!='PASS':blocked='STOP_'+row['status']
                if done:
                    result['paths']=[completed[i] for i in sorted(completed)];save(OUT/'PARTIAL.json',result)
        result['status']='COMPLETE_V58_30' if len(completed)==30 and not blocked else 'STOPPED'
        if blocked:result['error']=blocked
    except Exception as exc:result.update(status='STOPPED',error=str(exc),traceback=traceback.format_exc(limit=10))
    result.update(elapsed_seconds=time.monotonic()-start,not_started=[j for i,j in enumerate(plan['jobs']) if i not in started])
    save(OUT/'RESULT.json',result);progress('TERMINAL',status=result['status'],completed_paths=len(result['paths']))
    if result['status']!='COMPLETE_V58_30':raise SystemExit(2)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check-only',action='store_true');main(parser.parse_args().check_only)
