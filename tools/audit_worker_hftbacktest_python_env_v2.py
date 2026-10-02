from __future__ import annotations
import json,subprocess,sys,shutil
from pathlib import Path
root=Path(r'C:\BTC5M-worker')
cands=[]
for p in [
 root/'.venv'/'Scripts'/'python.exe',root/'.venv38'/'Scripts'/'python.exe',root/'venv'/'Scripts'/'python.exe',root/'py38'/'python.exe',root/'Python38'/'python.exe']:
 if p.exists(): cands.append(str(p))
for pat in ['*venv*/Scripts/python.exe','*/Scripts/python.exe','python.exe']:
 for p in root.glob(pat):
  s=str(p)
  if s not in cands:cands.append(s)
try:
 cp=subprocess.run(['where.exe','python'],capture_output=True,text=True,timeout=5)
 for s in cp.stdout.splitlines():
  s=s.strip()
  if s and s not in cands:cands.append(s)
except Exception:pass
rows=[]
for exe in cands[:12]:
 try:
  cp=subprocess.run([exe,'-c','import sys;print(sys.version);import hftbacktest;print(hftbacktest.__file__)'],capture_output=True,text=True,timeout=8)
  rows.append({'exe':exe,'rc':cp.returncode,'stdout':cp.stdout.strip(),'stderr':cp.stderr.strip()[-500:]})
 except Exception as e:rows.append({'exe':exe,'error':repr(e)})
print(json.dumps({'self':sys.executable,'rootChildren':[p.name for p in list(root.iterdir())[:80]],'candidates':rows},ensure_ascii=False),flush=True)
