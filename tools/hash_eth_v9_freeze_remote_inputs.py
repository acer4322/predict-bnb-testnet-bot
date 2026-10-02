from pathlib import Path
import hashlib,json,os
paths=[
 r'C:\BTC5M-worker\.lan_worker_v1\staging\eth_repair_exam_v2_dagger_cache.joblib',
 r'C:\BTC5M-worker\.lan_worker_v1\staging\model.pt',
]
out={}
for s in paths:
 p=Path(s)
 out[s]={'exists':p.exists(),'size':p.stat().st_size if p.exists() else None,'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None}
rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); rd.mkdir(parents=True,exist_ok=True); (rd/'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2),flush=True)
