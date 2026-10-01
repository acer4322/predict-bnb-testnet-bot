"""One named nine-path native job, max4 paths, no BASE rerun, no fit or live access."""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS'):os.environ[k]='1'
import argparse,hashlib,json,socket,subprocess,sys,time,traceback
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from analyze import read
P=Path(__file__).resolve().parent;W=Path('C:/BTC5M-worker');STAGE=W/'.lan_worker_v1/staging'
OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR',str(P/'not-dispatched')))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
def verify():
 assert socket.gethostname().upper()=='DESKTOP-JIERAGF' and P.parent==STAGE
 for n,h in read(P/'MANIFEST.json')['files'].items():assert sha(P/n)==h,n
 for n,h in read(P/'SOURCE_PARENT.json')['source_hashes'].items():assert sha(STAGE/n)==h,n
 base=STAGE/'btc5m_cg1at_fresh100a_20260930/base'
 for n,h in read(base/'manifest.json')['files'].items():assert sha(base/n)==h,n
 assert sha(base/'manifest.json')==read(P/'SOURCE_PARENT.json')['base_manifest_sha256']
 plan=read(P/'PROTOCOL.json');assert len(plan['jobs'])==len(plan['markets'])==9 and plan['max_threads']==plan['parallel_paths']==4
 proof=read(P/'SOURCE_PARENT.json');original=W/'.lan_worker_v1/results'/proof['original_job']
 assert sha(original/'RESULT.json')==proof['original_result_sha256']
 assert read(original/'RESULT.json')['not_started']==plan['markets']
 assert all(not (original/'arms'/f'deep1_DEEP_{i}'/'result.json').exists() for i in plan['markets'])
 from deep_audit import audit
 first=audit(original/'arms/deep1_DEEP_2671717',W/'.lan_worker_v1/results/btc5m-cg1at-fresh100a-20260930/arms/c100_CG1AT_2671717')
 assert first['status']=='PASS' and all(first['mandatory'].values())
 for mid,b in plan['baseline'].items():
  for n,h in b['files'].items():assert sha(W/'.lan_worker_v1/results'/b['remote_relative']/n)==h,(mid,n)
 for j in plan['jobs']:
  assert not (W/'.tmp'/f"target_core_cycle_active_v8_deep1r9_DEEP_{j['market']}").exists()
 candidate=read(P/'CANDIDATE.json');assert sha(Path(candidate['backend'])/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd')==candidate['sha256']
 from test_deep_layer import run
 assert run(inert=False)['status']=='PASS'
 return plan
def command(j,check=False):
 env=os.environ.copy()
 for key in list(env):
  if key.startswith(('V12','EOF_')):env.pop(key,None)
 env.update(j['env_v12'])
 argv=[sys.executable,str(P/'overlay/run_variant.py'),'--variant','PREPARE','--market-id',str(j['market']),'--mode','NO_DIRECTION','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE','--direction-rule','LEGACY']
 if check:argv.append('--check-only');env.pop('BTC5M_LAN_RESULT_DIR',None)
 return argv,env
def path_job(j,plan):
 arm=OUT/'arms'/f"deep1r9_DEEP_{j['market']}";arm.mkdir(parents=True,exist_ok=False)
 argv,env=command(j);env['BTC5M_LAN_RESULT_DIR']=str(arm)
 save(arm/'EXECUTION.json',dict(argv=argv,env_v12=j['env_v12'],resource_env={k:env[k] for k in ('OMP_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS')}))
 t=time.monotonic()
 with (arm/'stdout.log').open('xb') as so,(arm/'stderr.log').open('xb') as se:rc=subprocess.run(argv,cwd=W,env=env,stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW).returncode
 save(arm/'PROCESS.json',dict(rc=rc,seconds=time.monotonic()-t))
 try:
  from deep_audit import audit
  b=W/'.lan_worker_v1/results'/plan['baseline'][str(j['market'])]['remote_relative'];row=audit(arm,b)
  save(arm/'PARITY.json',row['parity'])
  assert rc in (0,2) and ((rc==0)==(not row['failed_checks'])),rc
 except Exception as exc:row=dict(status='PATH_ERROR',error=repr(exc),traceback=traceback.format_exc(limit=12),path_valid=False)
 save(arm/'AUDIT.json',row)
 return dict(market=j['market'],rc=rc,**row)
def main(check):
 plan=verify()
 if check:
  argv,env=command(plan['jobs'][0],True);cp=subprocess.run(argv,cwd=W,env=env,capture_output=True,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
  assert cp.returncode==0,(cp.stdout[-3000:],cp.stderr[-3000:])
  print(json.dumps(dict(status='PASS',native_executed=0,model_fits=0,paths=9,parallel_paths=4)));return
 assert os.environ.get('BTC5M_LAN_RESULT_DIR') and not (OUT/'RESULT.json').exists()
 t=time.monotonic();rows=[]
 first=path_job(plan['jobs'][0],plan);rows.append(first);save(OUT/'PARTIAL.json',dict(paths=rows))
 # A fatal first-path engine failure preserves evidence and prevents dispatch of later markets.
 if first['status']!='PATH_ERROR':
  with ThreadPoolExecutor(max_workers=4) as pool:
   fs={pool.submit(path_job,j,plan):j for j in plan['jobs'][1:]}
   for f in as_completed(fs):
    rows.append(f.result());save(OUT/'PARTIAL.json',dict(paths=rows));print(json.dumps(dict(completed=len(rows),market=rows[-1]['market'],status=rows[-1]['status'])),flush=True)
 rows.sort(key=lambda r:plan['markets'].index(r['market']))
 result=dict(status='COMPLETE_DEEP_STAGE1_REST9' if len(rows)==9 else 'STOPPED_FIRST_PATH_ERROR',paths=rows,model_fits=0,live_changes=0,parallel_paths=4,elapsed_seconds=time.monotonic()-t,not_started=[j['market'] for j in plan['jobs'] if j['market'] not in [r['market'] for r in rows]])
 save(OUT/'RESULT.json',result);print(json.dumps(dict(status=result['status'],completed=len(rows))),flush=True)
 if len(rows)!=9:raise SystemExit(2)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');main(ap.parse_args().check_only)
