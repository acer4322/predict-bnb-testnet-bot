"""One pinned native repair build, synthetic contracts, then eight sequential paths."""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import traceback
import zipfile

from patches import rust_clock
from analyze import read, native_contract, audit_path

P=Path(__file__).resolve().parent
W=Path('C:/BTC5M-worker')
R=W/'.tmp/v12g_eof_execution_repair_20260927_v33'
SOURCE=R/'.tmp/hft244_accounting_source_v1'
BUILD=R/'.tmp/hft244_accounting_build_v1'
V4=W/'.tmp/hft244_receipts_v4_20260910'
OLD_BUILD=V4/'.tmp/hft244_accounting_build_v1'
OLD_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR',str(P/'not-dispatched')))


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p,d):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def progress(phase,**kwargs):
    save(OUT/'PROGRESS.json',dict(phase=phase,**kwargs));print(json.dumps(dict(phase=phase,**kwargs)),flush=True)


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def baseline(job, materialize=False):
    old=W/job['old_relative']
    if old.exists():
        for name,h in job['baseline_hashes'].items():assert sha(old/name)==h,(job['arm'],name)
        return old
    # Historical worker archives remove their expanded arms; reuse their frozen bytes.
    dest=P/'baseline'/old.name
    with zipfile.ZipFile(old.parent.parent/'arms.zip') as archive:
        for name,h in job['baseline_hashes'].items():
            raw=archive.read('arms/'+old.name+'/'+name)
            assert hashlib.sha256(raw).hexdigest()==h,(job['arm'],name)
            if materialize:
                dest.mkdir(parents=True,exist_ok=True)
                if (dest/name).exists():assert sha(dest/name)==h
                else:(dest/name).write_bytes(raw)
    return dest


def run_process(label,argv,cwd=None,env=None):
    start=time.monotonic()
    with (OUT/(label+'.stdout.log')).open('xb') as so,(OUT/(label+'.stderr.log')).open('xb') as se:
        cp=subprocess.run(argv,cwd=cwd or W,env=env or os.environ.copy(),stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW)
    save(OUT/(label+'.process.json'),dict(rc=cp.returncode,seconds=time.monotonic()-start,argv=argv))
    return cp.returncode


def verify():
    assert socket.gethostname().upper()=='DESKTOP-JIERAGF'
    assert P.parent==W/'.lan_worker_v1/staging'
    for name,h in read(P/'MANIFEST.json')['files'].items():assert sha(P/name)==h,name
    old_manifest=read(P/'DISCOVERY.json')['manifest']['files']
    for name,h in old_manifest.items():assert sha(V4/'.tmp/hft244_accounting_source_v1'/name)==h,name
    assert sha(OLD_BUILD/'candidate_python/hftbacktest/_hftbacktest.cp313-win_amd64.pyd')==OLD_SHA
    base=W/'.lan_worker_v1/staging/v12g_fresh30_20260927_v24/base'
    contract=read(P/'overlay/actor_contract.json')
    assert sha(base/'manifest.json')==contract['baseline_manifest_sha256']
    for name,h in read(base/'manifest.json')['files'].items():assert sha(base/name)==h,name
    for job in read(P/'PROTOCOL.json')['jobs']:baseline(job)
    return dict(source_files=len(old_manifest),base_verified=True,native_sha256=OLD_SHA)


def build():
    assert not R.exists() and not (P/'CANDIDATE.json').exists()
    shutil.copytree(V4/'.tmp/hft244_accounting_source_v1',SOURCE,ignore=shutil.ignore_patterns('__pycache__'))
    f=SOURCE/'hftbacktest/src/backtest/mod.rs';f.write_text(rust_clock(f.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
    manifest={str(p.relative_to(SOURCE)).replace('\\','/'):sha(p) for p in SOURCE.rglob('*') if p.is_file()}
    save(OUT/'CANDIDATE_SOURCE_MANIFEST.json',manifest)
    shutil.copytree(OLD_BUILD/'candidate_python',BUILD/'candidate_python',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(V4/'tools',R/'tools',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(OLD_BUILD/'v31-smoke-reference.json',BUILD/'v31-smoke-reference.json')
    # Reuse installed offline compiler/libraries; outputs use a new target directory.
    builder=load(W/'.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py','builder')
    env=builder.environment();env['CARGO_TARGET_DIR']=str(R/'target');env['CARGO_BUILD_JOBS']='4'
    rc=run_process('build',[str(builder.ROOT/'rust/bin/cargo.exe'),'build','--locked','--offline','-p','py-hftbacktest','-j','4'],SOURCE,env)
    assert rc==0,'BUILD_FAILED'
    binary=BUILD/'candidate_python/hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    shutil.copy2(R/'target/debug/hftbacktest.dll',binary)
    candidate=dict(backend=str(BUILD/'candidate_python'),sha256=sha(binary),old_sha256=OLD_SHA)
    assert candidate['sha256']!=OLD_SHA
    save(P/'CANDIDATE.json',candidate);save(OUT/'CANDIDATE.json',candidate)
    return candidate


def path_job(job):
    arm=OUT/'arms'/f"v12g33_{job['arm']}_{job['market']}";arm.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy()
    for k in list(env):
        if k.startswith(('V12','EOF_')):env.pop(k,None)
    env.update(job['env_v12']);env['BTC5M_LAN_RESULT_DIR']=str(arm)
    cmd=[sys.executable,str(P/'overlay/run_variant.py'),'--variant','PREPARE','--market-id',str(job['market']),
         '--mode',job['mode'],'--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0',
         '--opportunity-mode','ONE_ACTIVE','--direction-rule',job['rule']]
    save(arm/'EXECUTION.json',dict(argv=cmd,env_v12=job['env_v12'],old_relative=job['old_relative']))
    t=time.monotonic()
    with (arm/'stdout.log').open('xb') as so,(arm/'stderr.log').open('xb') as se:
        rc=subprocess.run(cmd,cwd=W,env=env,stdout=so,stderr=se,creationflags=subprocess.CREATE_NO_WINDOW).returncode
    save(arm/'PROCESS.json',dict(rc=rc,seconds=time.monotonic()-t))
    try:
        verdict=audit_path(arm,baseline(job,materialize=True),recovery=job.get('recovery',False))
        if job['arm'] in ('V12_CONTROL','K415_CONTROL'):assert not verdict['failed_checks'] and rc==0
        else:assert rc in (0,2)
    except Exception as ex:
        verdict=dict(status='PATH_ERROR',error=type(ex).__name__+': '+str(ex),traceback=traceback.format_exc(limit=10))
    save(arm/'AUDIT.json',verdict)
    return dict(arm=job['arm'],market=job['market'],rc=rc,**verdict)


def main(check_only=False):
    proof=verify()
    if check_only:
        assert not R.exists()
        sys.path.insert(0,str(OLD_BUILD/'candidate_python'))
        import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(OLD_BUILD/'candidate_python/hftbacktest').resolve()
        print(json.dumps(dict(status='PASS',native_executed=0,model_fits=0,**proof)),flush=True);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and not (OUT/'RESULT.json').exists()
    plan=read(P/'PROTOCOL.json');result=dict(status='IN_PROGRESS',paths=[],model_fits=0,live_changes=0,planned_paths=len(plan['jobs']))
    try:
        progress('BUILD');candidate=build();result['candidate']=candidate
        progress('SYNTHETIC_CONTRACT')
        for label,backend in [('old',OLD_BUILD/'candidate_python'),('new',Path(candidate['backend']))]:
            rc=run_process('eof-'+label,[sys.executable,str(P/'native_contract.py'),'--backend',str(backend),'--output',str(OUT/(label+'_eof.json'))])
            assert rc==0,('SYNTHETIC_CAPTURE_FAILED',label)
        contract=native_contract(read(OUT/'old_eof.json'),read(OUT/'new_eof.json'));save(OUT/'EOF_CONTRACT.json',contract)
        progress('RECEIPT_CONTRACT')
        rc=run_process('receipt-contract',[sys.executable,str(R/'tools/check_hft244_receipts_v4.py')])
        for n in ('v4-receipt-contract.json','v4-progress.json'):
            if (BUILD/n).exists():shutil.copy2(BUILD/n,OUT/n)
        assert rc==0 and read(OUT/'v4-receipt-contract.json')['verdict']=='RECEIPT_ADAPTER_SYNTHETIC_CONTRACT_PASS','RECEIPT_CONTRACT_FAILED'
        for i,job in enumerate(plan['jobs']):
            progress('MARKET_PATH',index=i,arm=job['arm'],market=job['market'])
            row=path_job(job);result['paths'].append(row);save(OUT/'PARTIAL.json',result)
            if row['status']!='PASS':raise RuntimeError('STOP_'+row['status'])
        result['status']='EXECUTION_REPAIR_VALIDATED'
    except Exception as ex:
        result.update(status='STOPPED',error=type(ex).__name__+': '+str(ex),traceback=traceback.format_exc(limit=12))
    result['not_started']=plan['jobs'][len(result['paths']):]
    result['original_native_unchanged']=sha(OLD_BUILD/'candidate_python/hftbacktest/_hftbacktest.cp313-win_amd64.pyd')==OLD_SHA
    assert result['original_native_unchanged']
    save(OUT/'RESULT.json',result);progress('TERMINAL',status=result['status'],completed_paths=len(result['paths']))
    if result['status']!='EXECUTION_REPAIR_VALIDATED':raise SystemExit(2)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');main(ap.parse_args().check_only)
