"""Unchanged A lab, return-frame observation adds local order count/time evidence."""
import difflib,hashlib,json,shutil
from pathlib import Path
P=Path(__file__).resolve().parent
OLD=P/'stage/native_engine_platform185_20261002_v1'
NEW=P/'stage/native_engine_platform_order_audit185_20261002_v1'
assert not NEW.exists()
shutil.copytree(OLD,NEW,ignore=shutil.ignore_patterns('__pycache__','fixtures','MANIFEST.json'))
shutil.copytree(P/'stage/native_engine_stack185_20261002_v1/META',NEW/'META')
before=(OLD/'platform_worker.py').read_text(encoding='utf-8');after=before
def replace(old,new):
    global after
    assert after.count(old)==1,(old,after.count(old))
    after=after.replace(old,new)
replace("OUT = Path(os.environ.get('BTC5M_LAN_RESULT_DIR', str(P/'not-dispatched')))","OUT = Path(os.environ.get('BTC5M_LAN_RESULT_DIR', str(P/'not-dispatched')))\nFX = Path(r'C:\\BTC5M-worker\\.lan_worker_v1\\staging\\native_engine_platform185_20261002_v1\\fixtures')")
replace("fx = P/'fixtures'/str(r['market_id'])","fx = FX/str(r['market_id'])")
replace("str(P/'fixtures')","str(FX)")
replace('    completed=[]','    completed=[]; order_summaries=[]')
old="            completed.append({'strategy':frame.f_locals['strat'], **arg})"
new=old+"\n            mid=arg['market']; strategy=frame.f_locals['strat']\n            meta=json.loads((P/'META'/f'{mid}.json').read_text())\n            os=[{'order_sequence':o['n'],'place_ms':o['t'],'official_window_seconds':(o['t']-meta['window_start_ms'])/1000,'lab_window_seconds':(o['t']-frame.f_locals['start'])/1000,'side':o['side'],'qty':o['qty']} for o in frame.f_locals['orders'].values()]\n            order_summaries.append({'market_id':mid,'strategy':strategy,'orders':os,'order_count':len(os),'average_official_window_seconds':sum(o['official_window_seconds'] for o in os)/len(os) if os else None,'sum_order_seconds':sum(o['official_window_seconds'] for o in os),'observer':'sys.setprofile run return-frame read-only; original lab bytecode unchanged'})"
replace(old,new)
replace("    result = json.loads((OUT/'local.json').read_text())","    save(OUT/'A_ORDER_SUMMARIES.json', {'rows':order_summaries,'observer':'return-frame only; no advance, submit, cancel or strategy replacement in observer'})\n    result = json.loads((OUT/'local.json').read_text())")
replace("'status':'COMPLETE_PLATFORM_LOCAL185'","'status':'COMPLETE_PLATFORM_ORDER_AUDIT185'") if after.count("'status':'COMPLETE_PLATFORM_LOCAL185'")==1 else None
# There are two explicit status strings (receipt and console); same diagnostic rename.
after=after.replace('COMPLETE_PLATFORM_LOCAL185','COMPLETE_PLATFORM_ORDER_AUDIT185')
compile(after,str(NEW/'platform_worker.py'),'exec');(NEW/'platform_worker.py').write_text(after,encoding='utf-8')
(NEW/'FOLLOW_ON.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='A_parent/platform_worker.py',tofile='A_order_observer/platform_worker.py')),encoding='utf-8')
(NEW/'FOLLOW_ON.json').write_text(json.dumps({'parent':OLD.name,'strategy_lab_sha256_unchanged':hashlib.sha256((OLD/'strategy_lab.py').read_bytes()).hexdigest(),'change':'Only read original run() locals at return for order count/time; no lab code or engine mutation. Same existing fixtures referenced.','fits':0},indent=2),encoding='utf-8')
assert (OLD/'strategy_lab.py').read_bytes()==(NEW/'strategy_lab.py').read_bytes()
files={f.relative_to(NEW).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(NEW.rglob('*')) if f.is_file()}
(NEW/'MANIFEST.json').write_text(json.dumps({'files':files,'job_id':'native-engine-platform-order-audit185-20261002-v1','markets':185,'max_threads':4,'parallel_paths':1,'fixtures_remote':str(r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_platform185_20261002_v1\fixtures'),'model_fits':0},indent=2),encoding='utf-8')
print(json.dumps({'package':NEW.name,'files':len(files),'native_executed':0,'fits':0}))
