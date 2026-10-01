"""Strict-key, exact-name one-shot worker transport; no host-side native work."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from discover import t, JOB, P

# Force every dispatcher SSH operation to the verified strict transport as well.
def strict_remote(host,args,timeout=30):
    assert host==t.HOST
    return t.strict(' '.join([t.d.REMOTE_PY,t.d.REMOTE_AGENT,*args]),timeout)
t.d.remote=strict_remote
t.d.ssh_raw=lambda host,command,timeout=30:t.strict(command,timeout)


def write(name,value):
    p=P/name;assert not p.exists(),name
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def preflight():
    identity=t.identity();probe=t.d.cmd_probe(t.HOST);exact=t.d.cmd_status(t.HOST,JOB);glob=t.global_state()
    assert exact['state']=='missing' and not glob['nonterminal'] and not glob['other_processes']
    assert probe['memory']['free_gb']>=12 and probe['cpu_pct']<=60 and not probe['robocopy_active']
    exists=t.remote('import pathlib,json;print(json.dumps((pathlib.Path("C:/BTC5M-worker/.lan_worker_v1/staging")/'+repr(P.name)+').exists()))')
    if exists:
        staged_sha=t.remote('import pathlib,json,hashlib;print(json.dumps(hashlib.sha256((pathlib.Path("C:/BTC5M-worker/.lan_worker_v1/staging")/'+repr(P.name)+'/"MANIFEST.json").read_bytes()).hexdigest()))')
        assert staged_sha==t.sha(P/'MANIFEST.json'),'staged manifest differs; reconcile before reuse'
        stage=dict(remote_absolute='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+P.name,reused_verified_stage=True)
    else:stage=t.d.cmd_stage(t.HOST,str(P))
    cmd=subprocess.list2cmdline([t.d.REMOTE_PY,stage['remote_absolute']+'\\worker.py','--check-only'])
    loaded=t.d.parse_json_output(t.strict(cmd,60))
    assert loaded['status']=='PASS' and loaded['native_executed']==0
    value=dict(identity=identity,probe=probe,exact=exact,global_state=glob,stage=stage,load_only=loaded,manifest_sha256=t.sha(P/'MANIFEST.json'))
    write('PREFLIGHT.json',value);return value


def submit():
    pre=json.loads((P/'PREFLIGHT.json').read_text());assert pre['manifest_sha256']==t.sha(P/'MANIFEST.json')
    t.identity();glob=t.global_state();assert not glob['nonterminal'] and not glob['other_processes']
    assert t.d.cmd_status(t.HOST,JOB)['state']=='missing'
    write('SUBMIT_INTENT.json',dict(job_id=JOB,attempts=1))
    # Root actively waits and collects this bounded job; no background watcher.
    value=t.d.cmd_submit(t.HOST,[t.d.REMOTE_PY,pre['stage']['remote_absolute']+'\\worker.py'],'.',JOB,14,8,80,auto_collect=False)
    write('SUBMIT.json',value);return value


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['preflight','submit','status','tail','collect']);a=ap.parse_args()
    if a.action=='status':value=t.d.cmd_status(t.HOST,JOB)
    elif a.action=='tail':value=t.d.cmd_tail(t.HOST,JOB,'stdout',5000)
    elif a.action=='collect':value=t.d.cmd_collect(t.HOST,JOB)
    else:value=globals()[a.action]()
    print(json.dumps(value,ensure_ascii=False,indent=2),flush=True)
