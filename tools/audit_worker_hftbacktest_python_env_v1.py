from __future__ import annotations
import json,subprocess,sys
from pathlib import Path
roots=[Path(r'C:\BTC5M-worker'),Path(r'C:\Users')]
seen=[]
for root in roots:
    if not root.exists(): continue
    try:
        for p in root.rglob('python.exe'):
            s=str(p)
            if s not in seen: seen.append(s)
            if len(seen)>=40: break
    except Exception: pass
    if len(seen)>=40: break
rows=[]
for exe in seen:
    try:
        cp=subprocess.run([exe,'-c','import sys;print(sys.version);import hftbacktest;print(hftbacktest.__file__)'],capture_output=True,text=True,timeout=12)
        rows.append({'exe':exe,'rc':cp.returncode,'stdout':cp.stdout.strip(),'stderr':cp.stderr.strip()[-1000:]})
    except Exception as e: rows.append({'exe':exe,'error':repr(e)})
print(json.dumps({'self':sys.executable,'selfVersion':sys.version,'candidates':rows},ensure_ascii=False),flush=True)
