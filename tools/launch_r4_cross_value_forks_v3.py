import subprocess,os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root/'data/research/r4_v0/hourly/r4_cross_value_forks_v3.stdout.log'; err=root/'data/research/r4_v0/hourly/r4_cross_value_forks_v3.stderr.log'
with out.open('w') as fo,err.open('w') as fe:
 p=subprocess.Popen(['python','tools/r4_collect_paired_forks_from_scan_v3.py'],cwd=root,stdout=fo,stderr=fe,env={**os.environ,'PYTHONWARNINGS':'ignore'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));print(p.pid)
