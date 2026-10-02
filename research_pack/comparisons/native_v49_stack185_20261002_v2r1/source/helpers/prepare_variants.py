"""New follow-on package for actual v1/v2 native replay, fixed supplied RV table."""
import difflib,hashlib,json,math,shutil
from pathlib import Path

P=Path(__file__).resolve().parent;ROOT=P.parents[2]
PARENT=P/'stage/native_engine_stack185_20261002_v1'
DEST=P/'stage/native_engine_stack_variants185_20261002_v1'
assert not DEST.exists()
shutil.copytree(PARENT,DEST,ignore=shutil.ignore_patterns('__pycache__','MANIFEST.json'))
rvpath=ROOT/'docs/research_specs/results/SPOT_RV5_TABLE_20261002.json'
shutil.copy2(rvpath,DEST/'RV5.json')
rv=json.loads(rvpath.read_bytes());labels=json.loads((DEST/'LABELS.json').read_bytes())['records']
for r in labels:
    mid=r['market_id'];row=rv[str(mid)];meta=json.loads((DEST/'META'/f'{mid}.json').read_bytes())
    assert row['window_start_s']*1000==meta['window_start_ms']
    assert math.isfinite(row['rv_5m']) and row['rv_5m']>=0
before=(PARENT/'stack_replay.py').read_text(encoding='utf-8')
after=before
def replace(old,new):
    global after
    assert after.count(old)==1,(old,after.count(old))
    after=after.replace(old,new)
replace('def run_market(k,fixtures,mid,strategy,winner,out):','def run_market(k,fixtures,mid,strategy,winner,out,rv_5m=None):')
replace('producer=Producer(strategy);trace=Trace();sim=None','producer=Producer(strategy,rv_5m);trace=Trace();sim=None')
replace("assert self_test()['status']=='PASS'", "assert self_test()['status']=='PASS'\n    rv=json.loads((P/'RV5.json').read_text())\n    for row in json.loads((P/'LABELS.json').read_text())['records']:\n        mid=row['market_id'];r=rv[str(mid)];meta=json.loads((P/'META'/f'{mid}.json').read_text())\n        assert r['window_start_s']*1000==meta['window_start_ms']\n        assert math.isfinite(r['rv_5m']) and r['rv_5m']>=0")
replace("rows={s:[] for s in ('FAV_TAKER','UNDER_TAKER')};began=time.monotonic()", "rows={s:[] for s in ('V1_SWITCH','V2_THROTTLE')};began=time.monotonic()\n    rv=json.loads((P/'RV5.json').read_text())")
replace("run_market(k,fixtures,r['market_id'],strategy,r['winner'],arm)", "run_market(k,fixtures,r['market_id'],strategy,r['winner'],arm,rv[str(r['market_id'])]['rv_5m'])")
replace("'status':'COMPLETE_STACK_FAV_UNDER185'", "'status':'COMPLETE_STACK_SWITCH_THROTTLE185'")
replace("'V1_SWITCH':'NOT_RUN_MISSING_REQUIRED_RV5','V2_THROTTLE':'NOT_RUN_MISSING_REQUIRED_RV5'", "'V1_SWITCH':'COMPLETE','V2_THROTTLE':'COMPLETE','rv_threshold':3.1834e-05")
replace("print('COMPLETE_STACK_FAV_UNDER185',flush=True)", "print('COMPLETE_STACK_SWITCH_THROTTLE185',flush=True)")
compile(after,str(DEST/'stack_replay.py'),'exec')
(DEST/'stack_replay.py').write_text(after,encoding='utf-8')
(DEST/'FOLLOW_ON.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='FAV_UNDER_parent/stack_replay.py',tofile='SWITCH_THROTTLE_child/stack_replay.py')),encoding='utf-8')
(DEST/'FOLLOW_ON.json').write_text(json.dumps({'parent_package':PARENT.name,'parent_script_sha256':hashlib.sha256((PARENT/'stack_replay.py').read_bytes()).hexdigest(),'child_script_sha256':hashlib.sha256((DEST/'stack_replay.py').read_bytes()).hexdigest(),'only_semantic_change':'predeclared per-market RV strategy selection; same replay, price, ownership and receipt semantics','RV_source_commit':'72095db1','RV_source_path':'docs/research_specs/results/SPOT_RV5_TABLE_20261002.json','RV_sha256':hashlib.sha256(rvpath.read_bytes()).hexdigest(),'RV_rows':len(rv),'selected_markets':185,'high_RV':sum(rv[str(r['market_id'])]['rv_5m']>=3.1834e-05 for r in labels),'raw_spot_bar_independent_reconstruction':'NOT_PROVIDED_BY_TABLE; supplied preregistered exogenous input','model_fits':0},indent=2))
files={f.relative_to(DEST).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(DEST.rglob('*')) if f.is_file()}
(DEST/'MANIFEST.json').write_text(json.dumps({'files':files,'job_id':'native-engine-stack-switch-throttle-185-20261002-v1','markets':185,'max_threads':4,'parallel_paths':1,'fixtures_remote':r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_platform185_20261002_v1\fixtures','strategies':['V1_SWITCH','V2_THROTTLE'],'rv_threshold':3.1834e-05,'model_fits':0},indent=2))
print(json.dumps({'files':len(files),'bytes':sum(f.stat().st_size for f in DEST.rglob('*') if f.is_file()),'RV_markets':185,'high_RV':114,'low_RV':71,'native_executed':0,'fits':0}))
