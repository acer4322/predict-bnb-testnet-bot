"""Named V24 dispatch through the existing LAN dispatcher, no host replay."""
import argparse
import base64
import json
import subprocess
from datetime import datetime,timezone
import btc5m_lan_dispatch_v1 as dispatch
from prepare_btc5m_commitment_repair_probe_v1 import ROOT,R,PACKAGE,JOB,STEM,sha,read,dump

HOST='btc5m-worker'


def remote_code(code):
    encoded=base64.b64encode(code.encode()).decode()
    command=subprocess.list2cmdline([dispatch.REMOTE_PY,'-c',"exec(__import__('base64').b64decode('"+encoded+"'))"])
    return dispatch.parse_json_output(dispatch.ssh_raw(HOST,command,timeout=30))


def identity():
    p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',HOST,'hostname'],capture_output=True,text=True,timeout=12)
    assert p.returncode==0 and p.stdout.strip().upper()=='DESKTOP-JIERAGF',(p.stdout,p.stderr)
    return dict(worker=p.stdout.strip(),strict_host_key=True,at=datetime.now(timezone.utc).isoformat())


def global_state(post=False):
    code='''import pathlib,json,collections,psutil,os,socket,hashlib
root=pathlib.Path('C:/BTC5M-worker'); rows=[json.loads(p.read_text(encoding='utf-8')) for p in (root/'.lan_worker_v1/jobs').glob('*/status.json')]
nonterminal=[dict(job_id=r['job_id'],state=r['state'],pid=r.get('pid'),pid_exists=bool(r.get('pid')) and psutil.pid_exists(int(r['pid']))) for r in rows if r['state'] in ('running','queued')]
other=[]
for proc in psutil.process_iter(['pid','name']):
 if proc.info['pid'] not in (os.getpid(),os.getppid()) and proc.info['name'] and any(x in proc.info['name'].lower() for x in ('python','robocopy')):other.append(proc.info)
out=dict(worker=socket.gethostname(),count=len(rows),states=dict(collections.Counter(r['state'] for r in rows)),nonterminal=nonterminal,other_processes=other)
'''
    if post:
        manifest=read(PACKAGE/'manifest.json')
        code += 'binary=pathlib.Path('+repr(manifest['backend']+'/hftbacktest/_hftbacktest.cp313-win_amd64.pyd')+')\n'
        code += 'private=root/'+repr('.tmp/target_core_cycle_active_v8_'+JOB+'/tools')+'\n'
        code += "out.update(native_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),private_files={n:hashlib.sha256((private/n).read_bytes()).hexdigest() for n in ['pair_core_asset_route_sizing_v2.py','minimal_student_quantity_seam_v1.py']})\n"
    else:
        code += 'out["stage_exists"]=(root/'+repr('.lan_worker_v1/staging/'+PACKAGE.name)+').exists()\n'
    return remote_code(code+'print(json.dumps(out))')


def preflight():
    idcheck=identity();probe=dispatch.cmd_probe(HOST);exact=dispatch.cmd_status(HOST,JOB);glob=global_state()
    assert probe['hostname']=='DESKTOP-JIERAGF' and exact['state']=='missing'
    assert not any(x['pid_exists'] for x in glob['nonterminal']) and not glob['other_processes']
    assert probe['memory']['free_gb']>=6 and probe['cpu_pct']<90 and not probe['robocopy_active']
    manifest=read(PACKAGE/'manifest.json');assert all(sha(PACKAGE/n)==h for n,h in manifest['files'].items())
    assert read(R/(STEM+'_LOCAL_PREFLIGHT.json'))['status']=='PASS'
    assert not glob['stage_exists'],'Existing stage: verify manually; do not overwrite'
    stage=dispatch.cmd_stage(HOST,str(PACKAGE))
    wave=read(R/'commitment_repair_wave_20260913.json')['jobs'][0]
    check=[dispatch.REMOTE_PY,stage['remote_absolute']+'\\money_runner.py',*wave['argv'][2:],'--check-only']
    checked=dispatch.parse_json_output(dispatch.ssh_raw(HOST,subprocess.list2cmdline(check),timeout=30))
    assert checked['status']=='PASS' and checked['worker']=='DESKTOP-JIERAGF'
    out=dict(status='PASS',identity=idcheck,probe=probe,exact=exact,global_status=glob,stage=stage,
             load_only=checked,manifest_sha256=sha(PACKAGE/'manifest.json'))
    dump('PREFLIGHT',out);return dict(status='PASS',job=JOB,load_only=checked,global_states=glob['states'])


def submit():
    identity();assert read(R/(STEM+'_PREFLIGHT.json'))['status']=='PASS'
    assert not (R/(STEM+'_SUBMIT.json')).exists(),'Already submitted or attempted; status only'
    assert dispatch.cmd_status(HOST,JOB)['state']=='missing'
    glob=global_state();assert not any(x['pid_exists'] for x in glob['nonterminal']) and not glob['other_processes']
    wave=read(R/'commitment_repair_wave_20260913.json')['jobs'][0]
    dump('SUBMIT',dict(status='ATTEMPT_IN_PROGRESS',job_id=JOB,attempts=1))
    out=dispatch.cmd_submit(HOST,wave['argv'],wave['cwd'],JOB,4,6,90,auto_collect=False)
    dump('SUBMIT',out);return out


def collect():
    exact=dispatch.cmd_status(HOST,JOB)
    assert exact['state'] in ('succeeded','failed','runner_error','cancelled'),'Terminal only'
    collected=dispatch.cmd_collect(HOST,JOB);dump('COLLECT',dict(status=exact,collected=collected))
    glob=global_state(post=True)
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert glob['native_sha256']==read(PACKAGE/'manifest.json')['native_sha256']
    assert all(glob['private_files'][n]==p['after'] and sha(ROOT/'tools'/n)==p['before'] for n,p in pins.items())
    assert not any(x['pid_exists'] for x in glob['nonterminal']) and not glob['other_processes']
    glob.update(status='PASS',shared_files_unchanged=True);dump('POSTCHECK',glob)
    return dict(status=exact['state'],elapsed_seconds=exact.get('elapsed_seconds'),collected=collected,postcheck='PASS')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('preflight','submit','status','collect'));a=p.parse_args()
    result=dispatch.cmd_status(HOST,JOB) if a.action=='status' else globals()[a.action]()
    print(json.dumps(result))
