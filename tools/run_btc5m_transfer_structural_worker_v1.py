"""Single-submit sequential transfer jobs via the existing verified LAN dispatcher."""
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
import run_btc5m_commitment_repair_worker_v1 as old
from prepare_btc5m_transfer_structural_v1 import *

HOST=old.HOST
dispatch=old.dispatch


def jobs():return read(R/(STEM+'_WAVE.json'))['jobs']
def artifact(j,tag):return R/(STEM+'_'+str(j['market'])+'_'+j['arm']+'_'+tag+'.json')
def save(j,tag,value):artifact(j,tag).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


def idle():
    state=old.global_state()
    assert not any(x['pid_exists'] for x in state['nonterminal']) and not state['other_processes'],state
    return state


def preflight():
    identity=old.identity();probe=dispatch.cmd_probe(HOST);global_state=idle()
    assert probe['memory']['free_gb']>=6 and probe['cpu_pct']<90 and not probe['robocopy_active']
    assert read(R/(STEM+'_COMPONENT.json'))['status']=='PASS'
    m=read(PACKAGE/'manifest.json');assert all(sha(PACKAGE/n)==h for n,h in m['files'].items())
    rows=jobs()
    with ThreadPoolExecutor(max_workers=4) as pool:
        states=list(pool.map(lambda j:dispatch.cmd_status(HOST,j['job_id']),rows))
    assert all(s['state']=='missing' for s in states),states
    exists=old.remote_code("from pathlib import Path;import json;print(json.dumps({'exists':Path("+repr('C:/BTC5M-worker/.lan_worker_v1/staging/'+PACKAGE.name)+").exists()}))")
    assert not exists['exists'],'Existing stage; verify, do not overwrite'
    stage=dispatch.cmd_stage(HOST,str(PACKAGE));checks=[]
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):
        argv=[dispatch.REMOTE_PY,stage['remote_absolute']+'\\money_runner.py',*rows[0]['argv'][2:],'--check-only']
        argv[argv.index('--mode')+1]=mode
        c=dispatch.parse_json_output(dispatch.ssh_raw(HOST,subprocess.list2cmdline(argv),timeout=30))
        assert c['status']=='PASS' and c['worker']=='DESKTOP-JIERAGF';checks.append(c)
    out=dict(status='PASS',identity=identity,probe=probe,global_state=global_state,exact_states=states,
             stage=stage,checks=checks,manifest_sha256=sha(PACKAGE/'manifest.json'))
    dump('PREFLIGHT',out);return dict(status='PASS',load_only_modes=3,files=len(m['files']))


def submit(index):
    j=jobs()[index];old.identity();idle()
    assert read(R/(STEM+'_PREFLIGHT.json'))['status']=='PASS'
    assert not artifact(j,'SUBMIT').exists(),'Already attempted: status lookup only'
    assert dispatch.cmd_status(HOST,j['job_id'])['state']=='missing'
    if index:
        assert read(R/(STEM+'_PARITY.json'))['status']=='PASS'
        assert read(artifact(jobs()[index-1],'AUDIT'))['execution_status']=='PASS'
    save(j,'SUBMIT',dict(status='ATTEMPT_IN_PROGRESS',job_id=j['job_id'],attempts=1))
    out=dispatch.cmd_submit(HOST,j['argv'],'.',j['job_id'],4,6,90,auto_collect=False)
    save(j,'SUBMIT',out);return out


def collect(index):
    j=jobs()[index];state=dispatch.cmd_status(HOST,j['job_id'])
    assert state['state'] in ('succeeded','failed','runner_error','cancelled'),'Terminal only'
    out=dispatch.cmd_collect(HOST,j['job_id']);save(j,'COLLECT',dict(status=state,collected=out))
    # Reuse established pinned binary/private/shared sizing postcheck without mutating its globals.
    import types
    ns=dict(old.global_state.__globals__,PACKAGE=PACKAGE,JOB=j['job_id'])
    check=types.FunctionType(old.global_state.__code__,ns)(True)
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert check['native_sha256']==read(PACKAGE/'manifest.json')['native_sha256']
    assert all(check['private_files'][n]==p['after'] and sha(ROOT/'tools'/n)==p['before'] for n,p in pins.items())
    assert not any(x['pid_exists'] for x in check['nonterminal']) and not check['other_processes']
    check.update(status='PASS',shared_files_unchanged=True);save(j,'POSTCHECK',check)
    return dict(status=state['state'],elapsed_seconds=state.get('elapsed_seconds'),postcheck='PASS',collected=out)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('preflight','submit','status','collect'));p.add_argument('--index',type=int,default=0);a=p.parse_args()
    if a.action=='preflight':out=preflight()
    elif a.action=='status':out=dispatch.cmd_status(HOST,jobs()[a.index]['job_id'])
    else:out=globals()[a.action](a.index)
    print(json.dumps(out))
