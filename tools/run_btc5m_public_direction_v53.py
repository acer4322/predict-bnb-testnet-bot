"""Explicit single-job actions on the verified second PC only."""
import argparse
import json
import subprocess
import run_btc5m_transfer_structural_worker_v1 as w
from prepare_btc5m_public_direction_v53 import PACKAGE, R, STEM, dump, sha


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def jobs():return [dict(j,arm='public') for j in read(R/(STEM+'_WAVE.json'))['jobs']]
w.PACKAGE=PACKAGE
w.STEM=STEM
w.jobs=jobs


def preflight():
    identity=w.old.identity();probe=w.dispatch.cmd_probe(w.HOST);state=w.idle()
    assert probe['memory']['free_gb']>=6 and probe['cpu_pct']<90 and not probe['robocopy_active']
    m=read(PACKAGE/'manifest.json');assert all(sha(PACKAGE/n)==h for n,h in m['files'].items())
    assert read(R/(STEM+'_COMPONENT.json'))['status']=='PASS'
    exact=[w.dispatch.cmd_status(w.HOST,j['job_id']) for j in jobs()]
    assert all(s['state']=='missing' for s in exact)
    remote='C:/BTC5M-worker/.lan_worker_v1/staging/'+PACKAGE.name
    exists=w.old.remote_code("from pathlib import Path;import json;print(json.dumps({'exists':Path("+repr(remote)+").exists()}))")
    assert not exists['exists'], 'Do not overwrite an existing stage'
    stage=w.dispatch.cmd_stage(w.HOST,str(PACKAGE))
    argv=[w.dispatch.REMOTE_PY,stage['remote_absolute']+'\\money_runner.py',*jobs()[0]['argv'][2:],'--check-only']
    checked=w.dispatch.parse_json_output(w.dispatch.ssh_raw(w.HOST,subprocess.list2cmdline(argv),timeout=30))
    assert checked['status']=='PASS' and checked['worker']=='DESKTOP-JIERAGF'
    out=dict(status='PASS',identity=identity,probe=probe,global_state=state,exact=exact,stage=stage,
        load_only=checked,manifest_sha256=sha(PACKAGE/'manifest.json'))
    dump(R/(STEM+'_PREFLIGHT.json'),out)
    return dict(status='PASS',files=len(m['files']),worker=checked['worker'])


def submit(index):
    j=jobs()[index]
    w.old.identity();w.idle()
    assert read(R/(STEM+'_PREFLIGHT.json'))['status']=='PASS'
    assert not w.artifact(j,'SUBMIT').exists(), 'Already attempted; use status'
    if index:
        assert read(w.artifact(jobs()[index-1],'AUDIT'))['execution_status']=='PASS'
    assert w.dispatch.cmd_status(w.HOST,j['job_id'])['state']=='missing'
    w.save(j,'SUBMIT',dict(status='ATTEMPT_IN_PROGRESS',job_id=j['job_id'],attempts=1))
    out=w.dispatch.cmd_submit(w.HOST,j['argv'],'.',j['job_id'],4,6,90,auto_collect=False)
    w.save(j,'SUBMIT',out)
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('preflight','submit','status','collect'));p.add_argument('--index',type=int,default=0);a=p.parse_args()
    if a.action=='preflight':out=preflight()
    elif a.action=='submit':out=submit(a.index)
    elif a.action=='collect':out=w.collect(a.index)
    else:out=w.dispatch.cmd_status(w.HOST,jobs()[a.index]['job_id'])
    print(json.dumps(out))
