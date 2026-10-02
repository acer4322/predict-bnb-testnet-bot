"""Restricted transport for user-requested research runs, never live services.
Uses the existing worker agent; strict host key, deterministic IDs, no blind retry.
"""
from __future__ import annotations
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess,sys,threading,time
from settings import validate_request
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
STORE=ROOT/'data/research/strategy_playground_v1_20260921'
sys.path.insert(0,str(ROOT/'tools'))
import btc5m_lan_dispatch_v1 as d
HOST='btc5m-worker';LOCK=threading.Lock();TERMINAL={'succeeded','failed','runner_error','cancelled','launch_failed'}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def save(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');os.replace(tmp,p)
def strict(command,timeout=25):
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',HOST,command],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
def agent(host,args,timeout=25):
    if host!=HOST:raise ValueError('Unknown worker')
    return strict(subprocess.list2cmdline([d.REMOTE_PY,d.REMOTE_AGENT,*args]),timeout)
# No insecure host-key fallback exists in this route.
d.remote=agent

def py(code,timeout=25):
    return d.parse_json_output(strict(subprocess.list2cmdline([d.REMOTE_PY,'-c',code]),timeout))
def identity():
    cp=strict('hostname',12)
    if cp.returncode or cp.stdout.strip().upper()!='DESKTOP-JIERAGF':raise RuntimeError('Worker identity/SSH verification failed')
    return cp.stdout.strip()
def active():
    rows=d.cmd_status(HOST,None)
    if not isinstance(rows,list):raise RuntimeError('Worker global state unknown')
    return [{'job_id':r['job_id'],'state':r['state']} for r in rows if r['state'] in ('queued','running')]
def probe():
    identity();p=d.cmd_probe(HOST);p['active_jobs']=active();return p

def scp(source,dest,recursive=False):
    argv=['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8']
    if recursive:argv.append('-r')
    cp=subprocess.run([*argv,str(source),str(dest)],capture_output=True,text=True,encoding='utf-8',timeout=45,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if cp.returncode:raise RuntimeError('Research file transfer failed: '+cp.stderr[-800:])

def stage_package():
    man=read(P/'MANIFEST.json');digest=sha(P/'MANIFEST.json');name='pgv1_code_'+digest[:16]
    for f,h in man['files'].items():
        if sha(P/f)!=h:raise RuntimeError('Local adapter differs from manifest: '+f)
    local=STORE/'packages'/name
    if not local.exists():
        local.mkdir(parents=True)
        for f in [*man['files'],'MANIFEST.json']:shutil.copyfile(P/f,local/f)
    remote='C:/BTC5M-worker/.lan_worker_v1/staging/'+name
    exists=py('import pathlib,json;print(json.dumps(pathlib.Path('+repr(remote)+').exists()))')
    if not exists:scp(local,HOST+':C:/BTC5M-worker/.lan_worker_v1/staging/',True)
    return remote,digest

def check_id(job_id):
    if not isinstance(job_id,str) or not re.fullmatch(r'pgv1-[bc]-[a-f0-9]{20}',job_id):raise ValueError('Invalid Playground job ID')
    folder=STORE/'jobs'/job_id
    if not (folder/'request.json').is_file():raise ValueError('Unknown Playground job')
    return folder

def submit(kind,body):
    if kind not in ('BASELINE','CANDIDATE'):raise ValueError('Unsupported request')
    request=validate_request(body)
    catalog=read(STORE/'catalog.json')
    if request['market'] not in {r['market'] for r in catalog['markets']}:raise ValueError('Market not in catalog')
    manifest=sha(P/'MANIFEST.json')
    # UI display speed, scrub cursor and old chart contents cannot enter this packet.
    packet={'kind':kind,'request':request,'adapter_manifest_sha256':manifest}
    key=hashlib.sha256(json.dumps(packet,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:20]
    jid='pgv1-'+('b' if kind=='BASELINE' else 'c')+'-'+key
    folder=STORE/'jobs'/jid
    with LOCK:
        folder.mkdir(parents=True,exist_ok=True)
        if not (folder/'request.json').exists():save(folder/'request.json',packet)
        if (folder/'RESULT.json').exists():return {'job_id':jid,'state':'succeeded','cached':True}
        identity();state=d.cmd_status(HOST,jid)
        if state.get('state')!='missing':return {'job_id':jid,'state':state['state'],'attached_existing':True}
        if (folder/'SUBMIT_ATTEMPT.json').exists():raise RuntimeError('先前派送狀態未證實；已保留 job ID，禁止自動重送：'+jid)
        p=probe()
        if p['active_jobs']:raise RuntimeError('第二台目前有研究工作，未排程新工作：'+json.dumps(p['active_jobs']))
        if p['memory']['free_gb']<6 or p['cpu_pct']>85 or p['robocopy_active']:raise RuntimeError('第二台資源不足；未派送。')
        remote,digest=stage_package()
        remote_request='C:/BTC5M-worker/.lan_worker_v1/staging/'+jid+'.json'
        scp(folder/'request.json',HOST+':'+remote_request)
        checked=d.parse_json_output(strict(subprocess.list2cmdline([d.REMOTE_PY,remote+'/worker.py','--request',remote_request,'--check-only']),40))
        if checked.get('status')!='PASS':raise RuntimeError('Native load-only preflight failed')
        save(folder/'PREFLIGHT.json',{'probe':p,'load_only':checked,'manifest_sha256':digest})
        if active():raise RuntimeError('Preflight 後第二台出現其他研究工作；未派送。')
        save(folder/'SUBMIT_ATTEMPT.json',{'job_id':jid,'state':'ATTEMPT_STARTED','created_at':time.time()})
        try:
            result=d.cmd_submit(HOST,[d.REMOTE_PY,remote+'/worker.py','--request',remote_request],'.',jid,4,6,85,auto_collect=False)
        except Exception as e:
            save(folder/'SUBMIT_UNKNOWN.json',{'job_id':jid,'error':repr(e),'retry':False});raise
        save(folder/'SUBMIT.json',result)
        return {'job_id':jid,'state':'submitted' if result.get('accepted') else 'not_accepted','worker_result':result}

def collect(job_id):
    folder=check_id(job_id)
    if (folder/'RESULT.json').exists():return read(folder/'RESULT.json')
    base='C:/BTC5M-worker/.lan_worker_v1/results/'+job_id
    # Copy compact output files only. Full native traces remain immutable on worker.
    with LOCK:
        if (folder/'RESULT.json').exists():return read(folder/'RESULT.json')
        tmp=folder/'RESULT.download.json';scp(HOST+':'+base+'/RESULT.json',tmp);result=read(tmp)
        if result.get('status')!='COMPLETE':raise RuntimeError('Result not complete')
        if result['adapter_manifest_sha256']!=read(folder/'request.json')['adapter_manifest_sha256']:raise RuntimeError('Result source mismatch')
        for name,h in result['files'].items():
            if name not in ('baseline.json','candidate.json'):raise RuntimeError('Unexpected worker output')
            target=folder/(name+'.download');scp(HOST+':'+base+'/'+name,target)
            if sha(target)!=h:raise RuntimeError('Output transfer hash mismatch')
            os.replace(target,folder/name)
        os.replace(tmp,folder/'RESULT.json')
    return result

def status(job_id):
    folder=check_id(job_id)
    if (folder/'RESULT.json').exists():return {'job_id':job_id,'state':'succeeded','collected':True,'result':read(folder/'RESULT.json')}
    try:
        identity();st=d.cmd_status(HOST,job_id)
        if st['state']=='succeeded':return {'job_id':job_id,'state':'succeeded','collected':True,'result':collect(job_id)}
        result={'job_id':job_id,'state':st['state'],'worker':st}
        if st['state']=='running':
            base='C:/BTC5M-worker/.lan_worker_v1/results/'+job_id
            code='import pathlib,json; a=pathlib.Path('+repr(base)+'); p=a/("candidate_"+a.name)/"PLAYBACK_PROGRESS.json"; q=a/"PROGRESS.json"; print((p if p.exists() else q).read_text(encoding="utf-8") if p.exists() or q.exists() else "{}")'
            try:result['progress']=py(code,12)
            except Exception:result['progress_unknown']=True
        elif st['state'] in TERMINAL:
            result['stderr']=d.cmd_tail(HOST,job_id,'stderr',4000)
        save(folder/'LAST_STATUS.json',result);return result
    except Exception as e:
        return {'job_id':job_id,'state':'CONNECTION_UNKNOWN','error':str(e),'retry_submitted':False}

def playback(job_id,which):
    folder=check_id(job_id)
    if which not in ('baseline','candidate') or not (folder/'RESULT.json').exists():raise ValueError('Playback not ready')
    return read(folder/(which+'.json'))

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['probe','submit','status']);ap.add_argument('--kind',default='CANDIDATE');ap.add_argument('--request');ap.add_argument('--job-id');a=ap.parse_args()
    result=probe() if a.action=='probe' else submit(a.kind,read(a.request)) if a.action=='submit' else status(a.job_id)
    print(json.dumps(result,ensure_ascii=False))
