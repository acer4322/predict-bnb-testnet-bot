"""Strict-host-key calls for this one named job. No retry of submit."""
import argparse
import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

P=Path(__file__).resolve().parent
ROOT=P.parents[2]
PACKAGE=P/'stage/native_engine_platform185_20261002_v1'
JOB='native-engine-platform-185-20261002-v1'
REMOTE=r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_platform185_20261002_v1'
PREFIX=''
RUNNER='platform_worker.py'
spec=importlib.util.spec_from_file_location('dispatch',ROOT/'tools/btc5m_lan_dispatch_v1.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)

def strict(host,command,timeout=30):
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15',host,command],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,**d._background_run_kwargs())

d.ssh_raw=strict
d.remote=lambda host,args,timeout=30:strict(host,' '.join([d.REMOTE_PY,d.REMOTE_AGENT,*args]),timeout)

def save(name,x):
    (P/(PREFIX+name)).write_text(json.dumps(x,indent=2,allow_nan=False),encoding='utf-8')

def main(action):
    if action=='stage':
        assert not (P/(PREFIX+'STAGE_RECEIPT.json')).exists()
        d.ensure_remote_transfer_dirs('btc5m-worker')
        cp=subprocess.run(['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-r',str(PACKAGE),'btc5m-worker:C:/BTC5M-worker/.lan_worker_v1/staging/'],capture_output=True,text=True,encoding='utf-8',errors='replace',**d._background_run_kwargs())
        assert cp.returncode==0,(cp.stdout,cp.stderr)
        x={'status':'STAGED','remote':REMOTE,'local':str(PACKAGE)}
        save('STAGE_RECEIPT.json',x)
    elif action=='check':
        cp=strict('btc5m-worker',subprocess.list2cmdline([d.REMOTE_PY,REMOTE+'\\'+RUNNER,'--check-only']),timeout=60)
        assert cp.returncode==0,(cp.stdout,cp.stderr)
        x=json.loads(cp.stdout.strip().splitlines()[-1]);assert x['status']=='LOAD_ONLY_PASS'
        save('LOAD_ONLY.json',x)
    elif action=='submit':
        assert json.loads((P/(PREFIX+'LOAD_ONLY.json')).read_text())['status']=='LOAD_ONLY_PASS'
        assert not (P/(PREFIX+'SUBMIT_INTENT.json')).exists(), 'Already attempted: query status, never submit again.'
        exact=d.cmd_status('btc5m-worker',JOB)
        assert exact['state']=='missing',exact
        global_state=d.cmd_status('btc5m-worker',None)
        jobs=global_state if isinstance(global_state,list) else global_state['jobs']
        assert not [r for r in jobs if r['state'] in ('running','queued')]
        probe=d.cmd_probe('btc5m-worker');assert probe['hostname']=='DESKTOP-JIERAGF' and not probe['robocopy_active']
        save('DISPATCH_PREFLIGHT.json',{'exact':exact,'nonterminal':[],'probe':probe})
        save('SUBMIT_INTENT.json',{'job_id':JOB,'attempts':1,'max_threads':4,'parallel_paths':1,'timeout_is_not_failure':True})
        x=d.cmd_submit('btc5m-worker',['python',REMOTE+'\\'+RUNNER],REMOTE,JOB,4,8.0,80.0,False,'cpu',0,4096,70.0,False,True)
        save('SUBMITTED.json',x)
    elif action=='status':
        x=d.cmd_status('btc5m-worker',JOB);save('STATUS.json',x)
    elif action=='tail':
        x={'stdout':d.cmd_tail('btc5m-worker',JOB,'stdout',5000),'stderr':d.cmd_tail('btc5m-worker',JOB,'stderr',3000)}
    elif action=='collect':
        x=d.cmd_collect('btc5m-worker',JOB)
    print(json.dumps(x,ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('stage','check','submit','status','tail','collect'));ap.add_argument('--stack',action='store_true')
    a=ap.parse_args()
    if a.stack:
        PACKAGE=P/'stage/native_engine_stack185_20261002_v1'
        JOB='native-engine-stack-fav-under-185-20261002-v1'
        REMOTE=r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_stack185_20261002_v1'
        PREFIX='STACK_';RUNNER='stack_replay.py'
    main(a.action)
