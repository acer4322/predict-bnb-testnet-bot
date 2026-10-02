import subprocess,os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root/'data/research/r4_v0/hourly/r4_cross_value_batch_v2.stdout.log'; err=root/'data/research/r4_v0/hourly/r4_cross_value_batch_v2.stderr.log'
env={**os.environ,'PYTHONWARNINGS':'ignore'}
with out.open('w') as fo, err.open('w') as fe:
 p=subprocess.Popen(['cmd','/c','tools\\run_r4_cross_value_batch_v2.cmd'],cwd=root,stdout=fo,stderr=fe,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 print(p.pid)
