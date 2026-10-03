import base64,hashlib,importlib.util,json,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;R=P.parents[2]
s=importlib.util.spec_from_file_location('old_lan',P.parent/'native_engine_comparison_20261002_v1/lan_actions.py');lan=importlib.util.module_from_spec(s);s.loader.exec_module(lan)
jobs=sys.argv[1:] or [f'original-maker-layer-btc185-20261002-v1r{i}' for i in (1,2,3,4)]
code="""import hashlib,json
from pathlib import Path
jobs=JOBS
root=Path(r'C:\\BTC5M-worker\\.lan_worker_v1\\results')
print(json.dumps({job:{f.relative_to(root/job).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in (root/job).rglob('*') if f.is_file()} for job in jobs}))
""".replace('JOBS',repr(jobs))
enc=base64.b64encode(code.encode()).decode();ps="& 'C:\\BTC5M-worker\\.venv\\Scripts\\python.exe' -c \"exec(__import__('base64').b64decode('"+enc+"'))\""
command='powershell -NoProfile -EncodedCommand '+base64.b64encode(ps.encode('utf-16le')).decode()
# A terminal ETH collection contains thousands of files and many GiB. This
# only reads hashes; the task supervisor emits progress every 45 seconds while
# the one SSH request runs. A timeout never submits or replays anything.
cp=lan.strict('btc5m-worker',command,timeout=300);assert cp.returncode==0,(cp.stdout[:200],cp.stderr[:700])
remote=json.loads(cp.stdout);out={}
for job in jobs:
    root=R/'data/research/lan_worker_returns'/job
    local={f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in root.rglob('*') if f.is_file() and f.name!='OML_METRICS.json'}
    assert local==remote[job],job
    out[job]={'status':'PASS','files':len(local),'sha256':local,'bytes':sum((root/n).stat().st_size for n in local)}
(P/'COLLECTION_AUDIT.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:{f:v for f,v in x.items() if f!='sha256'} for k,x in out.items()}))
