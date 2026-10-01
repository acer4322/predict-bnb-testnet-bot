"""CG1AT fresh-100a (FULL / CG1AT; FULL reference from this job); from ATBID insample40 r3 (engine-order self-cross replay: lower manager passive, drop other crossing NEW); from ATBID insample40 r2 (final plan own-cross pass); from ATBID insample40 r1 (own-cross-safe passive price); from ATBID insample40 (V58 passive at best bid, 40 consumed markets); from CG3 WL mechanism check r1 (V8 maintenance skips ladder keys, owned_map excludes ladder pending); from CG3 WL mechanism check (10 consumed high-conviction markets, WL60/WL90 on CG1, 14 parallel); from CG2 B + small-dose A (AB20 / AB30H, 38 paths); from CG2-B resume r1 (12 never-started B paths; no-chase-aware confirm_audit) of CG2 in-sample (A insurance / B no-chase) on f40d high-conviction markets; from V72 low-conviction cap 10/25/50% on fresh-40b (FULL reused from V68/r1/r2); infrastructure from V68r2 resume (CENSORED rows non-blocking; 2642273 censored) of V68r1 resume (work_audit fix; 2642007 censored) of V68 size validation on fresh-40b (FULL / 0.5 / 0.2); inherited: V67 passive zone mirror (alpha .5/1) + half-size arm on fresh-40; from V66 zone-mirror hedge-ratio sweep (alpha 0.5 / 1.0) on fresh-40; from V65r1 uncertain-zone mirror (CONFIRM/BPR/FLIP_EXIT OFF); inherited docstring: confirmation-paced chosen-side expansion (BPR OFF, FLIP_EXIT OFF): V58 (original scale + STOP290) plus bounded partial repair; 10 fixed markets and one inert control."""
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
from bpr_audit import audit as bpr_audit

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
    base=STAGE/'btc5m_cg1at_fresh100a_20260930/base'
    for n,h in read(base/'manifest.json')['files'].items():assert sha(base/n)==h,n
    plan=read(P/'PROTOCOL.json')
    assert len(plan['markets'])==100 and len(plan['jobs'])==200 and read(P/'overlay/actor_contract.json')['baseline_manifest_sha256']==sha(base/'manifest.json') and 2629444 not in plan['markets']
    assert plan['parallel_paths']==plan['max_threads']==14 and plan['native_threads_per_path']==1
    for b in plan['baseline'].values():
        for n,h in b['files'].items():assert sha(W/'.lan_worker_v1/results'/b['remote_relative']/n)==h,n
    for j in plan['jobs']:
        assert not (W/'.tmp'/f"target_core_cycle_active_v8_c100_{j['arm']}_{j['market']}").exists()
    candidate=read(P/'CANDIDATE.json')
    assert sha(Path(candidate['backend'])/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd')==candidate['sha256']
    from test_sizing import run
    assert run()['status']=='PASS'
    from test_demand_scale import run as scale_tests
    assert scale_tests()['status']=='PASS'
    from test_hard_stop import run as stop_tests
    assert stop_tests()['status']=='PASS'
    cp3=subprocess.run([sys.executable,str(P/'test_zone_mirror.py')],cwd=P,capture_output=True,text=True,encoding='utf8',creationflags=subprocess.CREATE_NO_WINDOW)
    assert cp3.returncode==0 and json.loads(cp3.stdout.strip().splitlines()[-1])['status']=='PASS',(cp3.stdout[-2000:],cp3.stderr[-2000:])
    cp2=subprocess.run([sys.executable,str(P/'test_flip_exit.py')],cwd=P,capture_output=True,text=True,encoding='utf8',creationflags=subprocess.CREATE_NO_WINDOW)
    assert cp2.returncode==0 and json.loads(cp2.stdout.strip().splitlines()[-1])['status']=='PASS',(cp2.stdout[-2000:],cp2.stderr[-2000:])
    cp=subprocess.run([sys.executable,str(P/'test_bpr.py')],cwd=P,capture_output=True,text=True,encoding='utf8',creationflags=subprocess.CREATE_NO_WINDOW)
    assert cp.returncode==0 and json.loads(cp.stdout.strip().splitlines()[-1])['status']=='PASS',(cp.stdout[-2000:],cp.stderr[-2000:])
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

def wait_audit(arm_name,market,limit=5400):
    q=OUT/'arms'/f"c100_{arm_name}_{market}"/'AUDIT.json';t0=time.monotonic()
    while not q.exists() and time.monotonic()-t0<limit:time.sleep(2)
    time.sleep(.5);return read(q) if q.exists() else None

def path_job(job,plan):
    arm=OUT/'arms'/f"c100_{job['arm']}_{job['market']}";arm.mkdir(parents=True,exist_ok=False)
    argv,env=command(job);env['BTC5M_LAN_RESULT_DIR']=str(arm)
    save(arm/'EXECUTION.json',dict(argv=argv,env_v12=job['env_v12'],resource_env={k:env[k] for k in ('OMP_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS')}))
    start=time.monotonic()
    with (arm/'stdout.log').open('xb') as so,(arm/'stderr.log').open('xb') as se:
        rc=subprocess.run(argv,cwd=W,env=env,stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW).returncode
    save(arm/'PROCESS.json',dict(rc=rc,seconds=time.monotonic()-start))
    try:
        b_=plan['baseline'].get(str(job['market']))
        baseline=arm if job['arm']=='FULL' else OUT/'arms'/f"c100_FULL_{job['market']}"   # V68r1: FULL from V68 or from this job
        r0_=read(arm/'result.json')
        if r0_.get('status')!='COMPLETE' and 'EOF_CENSORED_BEFORE_SOURCE' in str(r0_.get('error')):
            row=dict(status='CENSORED',error=str(r0_.get('error'))[:300]);save(arm/'AUDIT.json',row);return dict(arm=job['arm'],market=job['market'],rc=rc,**row)
        if job['arm']!='FULL':
            fa=wait_audit('FULL',job['market']);assert fa is not None and fa.get('status')=='PASS',('FULL_REFERENCE_NOT_PASS',None if fa is None else fa.get('status'))
        row=audit_path(arm,None);assert row['status']=='PASS'
        assert rc in (0,2) and ((rc==0)==(not row['failed_checks']))
        row.update(work_audit(arm,'SERVICE'));row.update(risk_audit(arm,None));row.update(intent_audit(arm,'OFF'))
        row.update(qualified_audit(arm,baseline,job['env_v12']['V12G_QUALIFIED_CONTINUATION']))
        row.update(governor_audit(arm,baseline,job['mode'],job['cap'],compare_preflip=False))
        assert row['passive_ticket']==job['ticket']
        tr=read(arm/'clock_trace.json.gz')
        passive=[o for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW' and o['route']=='PASSIVE']
        assert passive and all(abs(o['qty']-job['ticket'])<1e-8 for o in passive)
        raw=read(arm/'result.json');assert raw['clock_smoke']['passive_ticket']==job['ticket']
        assert raw['v53_sizing']['active_pay_ticket']==float(job['env_v12'].get('V12G_PAY_QTY') or 15.) and raw['v53_sizing']['gross_decision_threshold']==float(job['env_v12']['V12G_GROSS'])
        from scale_audit import audit as scale_audit
        row.update(scale_audit(arm,job))
        from stop_audit import audit as stop_audit
        row.update(stop_audit(arm,baseline,job))
        row.update(bpr_audit(arm,job))
        from flip_exit_audit import audit as flip_exit_audit
        from confirm_audit import audit as confirm_audit
        from zone_mirror_audit import audit as zone_mirror_audit
        row.update(zone_mirror_audit(arm,job))
        from cg2_audit import audit as cg2_audit
        row.update(cg2_audit(arm,job))
        from cg3_audit import audit as cg3_audit
        row.update(cg3_audit(arm,job))
        row.update(confirm_audit(arm,job))
        row.update(flip_exit_audit(arm,job))
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
        print(json.dumps(dict(status='PASS',native_executed=0,model_fits=0,paths=200,parallel_paths=14)));return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and not (OUT/'RESULT.json').exists()
    start=time.monotonic();result=dict(status='IN_PROGRESS',paths=[],resource_samples=[],model_fits=0,live_changes=0,parallel_paths=4)
    completed={};started=set();pending={};blocked=None;last_report=0.;next_index=1
    try:
        progress('INERT_CONTROL',market=plan['jobs'][0]['market']);started.add(0)
        control=path_job(plan['jobs'][0],plan);completed[0]=control;result['paths']=[control];save(OUT/'PARTIAL.json',result)
        if control['status'] not in ('PASS','CENSORED'):blocked='INERT_CONTROL_FAILED'
        with ThreadPoolExecutor(max_workers=14) as pool:
            while pending or (next_index<len(plan['jobs']) and not blocked):
                free=psutil.virtual_memory().available/1024**3
                if free<6:blocked='RESOURCE_AVAILABLE_MEMORY_BELOW_6GB'
                while not blocked and next_index<len(plan['jobs']) and len(pending)<14:
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
                    if row['status'] not in ('PASS','CENSORED'):blocked='STOP_'+row['status']
                if done:
                    result['paths']=[completed[i] for i in sorted(completed)];save(OUT/'PARTIAL.json',result)
        result['status']='COMPLETE_ATV_200' if len(completed)==200 and not blocked else 'STOPPED'
        if blocked:result['error']=blocked
    except Exception as exc:result.update(status='STOPPED',error=str(exc),traceback=traceback.format_exc(limit=10))
    result.update(elapsed_seconds=time.monotonic()-start,not_started=[j for i,j in enumerate(plan['jobs']) if i not in started])
    save(OUT/'RESULT.json',result);progress('TERMINAL',status=result['status'],completed_paths=len(result['paths']))
    if result['status']!='COMPLETE_ATV_200':raise SystemExit(2)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check-only',action='store_true');main(parser.parse_args().check_only)
