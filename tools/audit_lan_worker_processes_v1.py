from __future__ import annotations
import json,time,os
from pathlib import Path
import psutil
ROOT=Path.cwd()
JOBS=ROOT/'.lan_worker_v1'/'jobs'
# prime cpu counters
procs=[]
for proc in psutil.process_iter(['pid','ppid','name','create_time','cmdline']):
    try:
        if 'python' in (proc.info.get('name') or '').lower():
            proc.cpu_percent(None); procs.append(proc)
    except Exception:
        pass
time.sleep(1.0)
rows=[]
for proc in procs:
    try:
        info=proc.as_dict(attrs=['pid','ppid','name','create_time','cmdline','memory_info'])
        rows.append({'pid':info['pid'],'ppid':info['ppid'],'name':info['name'],'create_time':info['create_time'],'age_sec':time.time()-info['create_time'],'cpu_pct':proc.cpu_percent(None),'rss_mb':info['memory_info'].rss/1024/1024,'cmdline':info.get('cmdline') or []})
    except Exception as e:
        pass
job_rows=[]
if JOBS.exists():
    for fp in JOBS.rglob('*.json'):
        try:
            d=json.loads(fp.read_text(encoding='utf-8'))
            if isinstance(d,dict) and ('job_id' in d or 'state' in d or 'pid' in d):
                job_rows.append({'file':str(fp.relative_to(ROOT)),'job_id':d.get('job_id'),'state':d.get('state'),'pid':d.get('pid'),'return_code':d.get('return_code'),'started_at':d.get('started_at'),'ended_at':d.get('ended_at')})
        except Exception:
            pass
active_pids={int(j['pid']) for j in job_rows if str(j.get('state')).lower()=='running' and j.get('pid') is not None}
terminal_pids={int(j['pid']) for j in job_rows if str(j.get('state')).lower() in {'succeeded','failed','cancelled','canceled'} and j.get('pid') is not None}
for r in rows:
    r['mapped_active_job']=r['pid'] in active_pids
    r['mapped_terminal_job']=r['pid'] in terminal_pids
    r['orphan_candidate']=not r['mapped_active_job'] and any('BTC5M-worker' in str(x) or '.lan_worker_v1' in str(x) for x in r['cmdline'])
out={'host':os.environ.get('COMPUTERNAME'),'python_processes':sorted(rows,key=lambda r:r['cpu_pct'],reverse=True),'jobs':job_rows,'orphan_candidates':[r for r in rows if r['orphan_candidate']]}
print(json.dumps(out,ensure_ascii=False,indent=2))
