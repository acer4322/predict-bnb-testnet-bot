import subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
logdir=root/'logs'; logdir.mkdir(exist_ok=True)
for expert in ['formation_arbitration_build_vs_allow','safe_crossing']:
    out=open(logdir/f'r3_{expert}_ebm_full_v1.log','w',encoding='utf-8')
    err=open(logdir/f'r3_{expert}_ebm_full_v1.err','w',encoding='utf-8')
    p=subprocess.run([sys.executable,str(root/'tools/train_r3_formation_ebm_full_v1.py'),expert],cwd=root,stdout=out,stderr=err)
    out.close(); err.close()
    if p.returncode!=0: raise SystemExit(p.returncode)
print('DONE')
