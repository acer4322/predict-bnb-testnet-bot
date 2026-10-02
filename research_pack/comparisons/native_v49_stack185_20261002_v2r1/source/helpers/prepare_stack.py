"""Stage only existing runtime source and new frozen-rule producer; no engine run."""
import hashlib,json,shutil
from pathlib import Path

P=Path(__file__).resolve().parent
R=P.parents[2]
REPRO=R/'data/research/hft244_v49_repro_20261002_v1/release'
DEST=P/'stage/native_engine_stack185_20261002_v1'
assert not DEST.exists()
DEST.mkdir(parents=True)
source=REPRO/'strategy/runtime_scratch_CG1AT_2671717'
source_hashes={}
for f in source.rglob('*.py'):
    rel=f.relative_to(source)
    dest=DEST/'runtime'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dest)
    source_hashes[rel.as_posix()]=hashlib.sha256(f.read_bytes()).hexdigest()
for n in ('stack_replay.py','comparison_policy.py'):
    shutil.copy2(P/n,DEST/n)
shutil.copy2(REPRO/'strategy/packages/CG1AT_fresh100a/eof_runtime.py',DEST/'eof_runtime.py')
platform=P/'stage/native_engine_platform185_20261002_v1'
for n in ('CANDIDATE.json','LABELS.json','INPUTS.json','EXPECTED_BACKEND.json','INPUT_AUDIT.json','SPEC_ZH.md'):
    shutil.copy2(platform/n,DEST/n)
(DEST/'META').mkdir()
batchpaths={'feed70':'hft244_feed_batch_20261002_v1/release','batch01':'hft244_fresh100_after2807162_20261002_v1/release_batch01','batch02':'hft244_fresh100_after2807162_20261002_v1/release_batch02','batch03':'hft244_fresh85_after2807162_20261002_v1/release'}
for row in json.loads((platform/'INPUTS.json').read_text())['markets']:
    m=R/'data/research'/batchpaths[row['batch']]/'markets'/str(row['market_id'])/'META.json'
    assert hashlib.sha256(m.read_bytes()).hexdigest()==row['meta_sha256']
    shutil.copy2(m,DEST/'META'/f"{row['market_id']}.json")
(DEST/'SOURCE_PARENT.json').write_text(json.dumps({'code_commit':'3bf09cf645e1d83284825c32196e483294fcd182','runtime_files':source_hashes,'unchanged_runtime':True,'new_producer_only':True,'original_CG1AT_alpha_replayed':False,'source_policy_scope':'V49 whole-plan native execution, OpenFunding ownership, receipt and EOF kernel with frozen spec alpha producer'},indent=2))
files={f.relative_to(DEST).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(DEST.rglob('*')) if f.is_file()}
(DEST/'MANIFEST.json').write_text(json.dumps({'files':files,'job_id':'native-engine-stack-fav-under-185-20261002-v1','markets':185,'max_threads':4,'parallel_paths':1,'fixtures_remote':r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_platform185_20261002_v1\fixtures','model_fits':0,'strategies':['FAV_TAKER','UNDER_TAKER'],'rv_dependent_strategies':'NOT_RUN_MISSING_REQUIRED_INPUT'},indent=2))
print(json.dumps({'files':len(files),'bytes':sum(f.stat().st_size for f in DEST.rglob('*') if f.is_file()),'runtime_files':len(source_hashes),'native_executed':0,'fits':0}))
