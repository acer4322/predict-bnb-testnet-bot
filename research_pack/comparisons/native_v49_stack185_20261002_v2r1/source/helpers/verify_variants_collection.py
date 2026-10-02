"""Read-only terminal return and fixture SHA verification over strict SSH."""
import argparse, base64, hashlib, json
from pathlib import Path
import lan_actions as lan

P=Path(__file__).resolve().parent;ROOT=P.parents[2]
JOB='native-engine-stack-switch-throttle-185-20261002-v1'
STAGEDIR='native_engine_stack_variants185_20261002_v1';PREFIX='VARIANTS_';EXPECTED='COMPLETE_STACK_SWITCH_THROTTLE185'
ap=argparse.ArgumentParser();ap.add_argument('--strict-stack',action='store_true');ap.add_argument('--order-audit',action='store_true');args=ap.parse_args()
if args.strict_stack:
    JOB='native-engine-stack-strict-four185-20261002-v2r1'
    STAGEDIR='native_engine_stack_strict185_20261002_v2r1';PREFIX='STRICT_R1_';EXPECTED='COMPLETE_STACK_STRICT_FOUR_ARM185'
if args.order_audit:
    assert not args.strict_stack
    JOB='native-engine-platform-order-audit185-20261002-v1'
    STAGEDIR='native_engine_platform_order_audit185_20261002_v1';PREFIX='ORDER_AUDIT_';EXPECTED='COMPLETE_PLATFORM_ORDER_AUDIT185'
RET=ROOT/'data/research/lan_worker_returns'/JOB
status=json.loads((P/(PREFIX+'STATUS.json')).read_bytes())
assert status['state']=='succeeded' and status['return_code']==0
assert json.loads((RET/'RESULT.json').read_bytes())['status']==EXPECTED
script=r'''
import hashlib,json
from pathlib import Path
r=Path(r'C:\BTC5M-worker\.lan_worker_v1\results\native-engine-stack-switch-throttle-185-20261002-v1')
fx=Path(r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_platform185_20261002_v1\fixtures')
inputs=json.loads(Path(r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_stack_variants185_20261002_v1\INPUTS.json').read_bytes())
print(json.dumps({'files':{p.relative_to(r).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*') if p.is_file()},'fixtures':{str(x['market_id']):hashlib.sha256((fx/str(x['market_id'])/'events.npz').read_bytes()).hexdigest() for x in inputs['markets']}}))
'''
script=script.replace('native-engine-stack-switch-throttle-185-20261002-v1',JOB).replace('native_engine_stack_variants185_20261002_v1',STAGEDIR)
code=base64.b64encode(script.encode()).decode()
ps="& 'C:\\BTC5M-worker\\.venv\\Scripts\\python.exe' -c \"exec(__import__('base64').b64decode('"+code+"'))\""
command='powershell -NoProfile -EncodedCommand '+base64.b64encode(ps.encode('utf-16le')).decode()
cp=lan.strict('btc5m-worker',command,timeout=60)
assert cp.returncode==0,(cp.stdout[:500],cp.stderr[:1000])
remote=json.loads(cp.stdout)
local={p.relative_to(RET).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in RET.rglob('*') if p.is_file()}
assert local==remote['files'],'Remote/local result mismatch'
inputs=json.loads((P/'stage'/STAGEDIR/'INPUTS.json').read_bytes())
assert remote['fixtures']=={str(r['market_id']):r['events_sha256'] for r in inputs['markets']}
audit={'status':'PASS_REMOTE_LOCAL_HASH_AND_EXACT_FIXTURE_IDENTITY','job_id':JOB,'files':len(local),'sha256':local,'fixtures_verified':len(remote['fixtures']),'bytes':sum(p.stat().st_size for p in RET.rglob('*') if p.is_file())}
(P/(PREFIX+'COLLECTION_AUDIT.json')).write_text(json.dumps(audit,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in audit.items() if k!='sha256'}))
