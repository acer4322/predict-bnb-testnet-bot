from __future__ import annotations
import argparse, os, runpy, shutil, sys, zipfile
from pathlib import Path


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--runtime-zip',required=True)
    ap.add_argument('--entry',required=True)
    ap.add_argument('entry_args',nargs=argparse.REMAINDER)
    a=ap.parse_args()
    worker_root=Path(r'C:\BTC5M-worker').resolve()
    runtime_zip=Path(a.runtime_zip)
    if not runtime_zip.is_absolute(): runtime_zip=(worker_root/runtime_zip).resolve()
    entry_args=list(a.entry_args)
    if entry_args and entry_args[0]=='--': entry_args=entry_args[1:]
    norm=[]
    for x in entry_args:
        sx=str(x)
        if sx.startswith('.lan_worker_v1/') or sx.startswith('.lan_worker_v1\\'):
            sx=str((worker_root/Path(sx.replace('\\','/'))).resolve())
        norm.append(sx)
    result_dir=Path(os.environ['BTC5M_LAN_RESULT_DIR']).resolve()
    runtime=result_dir/'isolated_runtime'
    if runtime.exists(): shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(runtime_zip) as z: z.extractall(runtime)
    os.chdir(result_dir)
    sys.path[:]=[str(runtime)]+[p for p in sys.path if Path(p or '.').resolve()!=worker_root]
    sys.path.append(str(worker_root))
    sys.argv=[a.entry,*norm]
    runpy.run_module(a.entry,run_name='__main__')

if __name__=='__main__': main()
