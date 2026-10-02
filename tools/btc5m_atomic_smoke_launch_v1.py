from __future__ import annotations
import argparse,json,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DISPATCH=ROOT/'tools'/'btc5m_lan_dispatch_v1.py'
TERMINAL={'succeeded','failed','runner_error','cancelled'}

def run(args, timeout=60):
    cp=subprocess.run([sys.executable,str(DISPATCH),*args],cwd=str(ROOT),capture_output=True,text=True,timeout=timeout,encoding='utf-8',errors='replace')
    if cp.returncode!=0:
        raise RuntimeError(f"dispatch rc={cp.returncode}: {cp.stderr.strip() or cp.stdout.strip()}")
    lines=[x.strip() for x in cp.stdout.splitlines() if x.strip()]
    if not lines: raise RuntimeError('dispatch returned no output')
    return json.loads('\n'.join(lines)) if lines[0].startswith('{') and len(lines)>1 else json.loads(lines[-1])

def compile_py(paths):
    checked=[]
    for p in paths:
        pp=(ROOT/p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        if pp.suffix.lower()=='.py':
            cp=subprocess.run([sys.executable,'-m','py_compile',str(pp)],cwd=str(ROOT),capture_output=True,text=True,encoding='utf-8',errors='replace')
            if cp.returncode!=0: raise RuntimeError(f'compile failed {pp}: {cp.stderr.strip()}')
            checked.append(str(pp))
    return checked

def main():
    ap=argparse.ArgumentParser(description='Atomic compile->stage->submit->confirm launcher; forbids PREPARED-without-submit idle state.')
    ap.add_argument('--job-id',required=True)
    ap.add_argument('--stage',action='append',default=[])
    ap.add_argument('--cwd',default=r'C:\BTC5M-worker')
    ap.add_argument('--max-threads',type=int,default=4)
    ap.add_argument('--min-free-ram-gb',type=float,default=6.0)
    ap.add_argument('--max-start-cpu-pct',type=float,default=95.0)
    ap.add_argument('--resource',choices=['cpu','gpu'],default='cpu')
    ap.add_argument('--gpu-index',type=int,default=0)
    ap.add_argument('--min-free-vram-mib',type=int,default=2048)
    ap.add_argument('--max-start-gpu-pct',type=float,default=95.0)
    ap.add_argument('--confirm-polls',type=int,default=4)
    ap.add_argument('argv',nargs=argparse.REMAINDER)
    a=ap.parse_args(); argv=a.argv[1:] if a.argv and a.argv[0]=='--' else a.argv
    if not argv: raise SystemExit('missing remote command after --')
    out={'version':'BTC5M_ATOMIC_SMOKE_LAUNCH_V1','jobId':a.job_id,'state':'STARTED','compiled':[],'staged':[],'submitted':None,'confirmedStatus':None}
    try:
        out['compiled']=compile_py(a.stage)
        for p in a.stage:
            st=run(['stage',p],timeout=90);out['staged'].append(st)
        submit_args=['submit','--cwd',a.cwd,'--job-id',a.job_id,'--max-threads',str(a.max_threads),'--min-free-ram-gb',str(a.min_free_ram_gb),'--max-start-cpu-pct',str(a.max_start_cpu_pct),'--resource',a.resource,'--gpu-index',str(a.gpu_index),'--min-free-vram-mib',str(a.min_free_vram_mib),'--max-start-gpu-pct',str(a.max_start_gpu_pct),'--protect-cancel','--',*argv]
        sub=run(submit_args,timeout=45);out['submitted']=sub
        if not sub.get('accepted'):
            out['state']='NOT_ACCEPTED';print(json.dumps(out,indent=2,ensure_ascii=False));return 2
        last=None
        for _ in range(max(1,a.confirm_polls)):
            time.sleep(.5);last=run(['status',a.job_id],timeout=30)
            if last.get('state') in TERMINAL or last.get('state')=='running':break
        out['confirmedStatus']=last
        state=(last or {}).get('state')
        if state=='running': out['state']='RUNNING_CONFIRMED'
        elif state in TERMINAL:
            out['state']='TERMINAL_CONFIRMED'
            try: out['stdout']=run(['tail',a.job_id,'--stream','stdout','--max-bytes','8192'],timeout=30)
            except Exception as ex: out['stdoutError']=f'{type(ex).__name__}:{ex}'
            try: out['stderr']=run(['tail',a.job_id,'--stream','stderr','--max-bytes','8192'],timeout=30)
            except Exception as ex: out['stderrError']=f'{type(ex).__name__}:{ex}'
        else:
            out['state']='SUBMITTED_BUT_STATE_UNCONFIRMED'
        print(json.dumps(out,indent=2,ensure_ascii=False))
        return 0 if out['state'] in {'RUNNING_CONFIRMED','TERMINAL_CONFIRMED'} else 3
    except Exception as ex:
        out['state']='FAILED_BEFORE_CONFIRMED_SUBMIT';out['error']=f'{type(ex).__name__}:{ex}'
        print(json.dumps(out,indent=2,ensure_ascii=False));return 1

if __name__=='__main__': raise SystemExit(main())
