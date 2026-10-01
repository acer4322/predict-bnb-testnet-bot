"""Authorized resume: only the nine never-started DEEP markets, policy unchanged."""
import hashlib,json,shutil
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parents[2];OLD=P.parent/'btc5m_deep_layer_stage1_20261002_v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
def patch(n,a,b):
 p=P/n;s=p.read_text(encoding='utf-8');assert s.count(a)==1,(n,a,s.count(a));p.write_text(s.replace(a,b,1),encoding='utf-8',newline='\n')
assert not (P/'PROTOCOL.json').exists()
old= json.loads((OLD/'PROTOCOL.json').read_text());result=ROOT/'data/research/lan_worker_returns'/old['job_id']/'RESULT.json';ended=json.loads(result.read_text())
assert ended['status']=='STOPPED_FIRST_PATH_ERROR' and [r['market'] for r in ended['paths']]==[2671717]
ids=old['markets'][1:];assert ended['not_started']==ids and len(ids)==9
assert not (ROOT/'data/research/lan_worker_returns/btc5m-deep-layer-stage1-rest9-20261002-v1').exists()
sources={}
for n,h in json.loads((OLD/'MANIFEST.json').read_text())['files'].items():
 assert sha(OLD/n)==h,n;sources[f'{OLD.name}/{n}']=h
 if n in ('PROTOCOL.json','SOURCE_PARENT.json','LOCAL_TESTS.json','build.py'):continue
 dst=P/n;assert not dst.exists(),n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/n,dst)
shutil.copy2(OLD/'.gitattributes',P/'.gitattributes')
patch('discover.py',old['job_id'],'btc5m-deep-layer-stage1-rest9-20261002-v1')
patch('worker.py','ten-path','nine-path')
patch('worker.py',"len(plan['jobs'])==len(plan['markets'])==10", "len(plan['jobs'])==len(plan['markets'])==9")
patch('worker.py','paths=10','paths=9')
patch('worker.py',"len(rows)==10", "len(rows)==9")
patch('worker.py',"len(rows)!=10", "len(rows)!=9")
patch('worker.py','COMPLETE_DEEP_STAGE1_10','COMPLETE_DEEP_STAGE1_REST9')
# Scratch namespace is the only runtime-source delta; it does not alter policy or inputs.
for n in ('worker.py','patches.py'):patch(n,'target_core_cycle_active_v8_deep1_','target_core_cycle_active_v8_deep1r9_')
patch('worker.py','deep1_DEEP_', 'deep1r9_DEEP_')
patch('deep_audit.py',"r['final_pending_cash_direct'].values()", "r['clock_smoke']['final_pending_cash_direct'].values()")
patch('deep_audit.py'," public=read(__import__('pathlib').Path('C:/BTC5M-worker/.lan_worker_v1/staging/btc5m_cg1at_fresh100a_20260930/base/inputs')/f\"public_{r['market_id']}.json.gz\")",
 " inputs=__import__('pathlib').Path('C:/BTC5M-worker/.lan_worker_v1/staging/btc5m_cg1at_fresh100a_20260930/base/inputs')\n if not inputs.exists():inputs=__import__('pathlib').Path(__file__).resolve().parents[3]/'data/research/btc5m_cg1at_fresh100a_20260930/base/inputs'\n public=read(inputs/f\"public_{r['market_id']}.json.gz\")")
proof=dict(parent=OLD.name,source_hashes=sources,base_manifest_sha256=json.loads((OLD/'SOURCE_PARENT.json').read_text())['base_manifest_sha256'],original_job=old['job_id'],original_result_sha256=sha(result),reused_first_market=2671717,
 runtime_policy_sha256=sha(P/'deep_layer.py'),policy_unchanged=sha(P/'deep_layer.py')==sha(OLD/'deep_layer.py'),audit_only_fix='Read clock_smoke.final_pending_cash_direct; portable public lookup for local fixture audit',never_started=ids)
assert proof['policy_unchanged']
plan=dict(old,job_id='btc5m-deep-layer-stage1-rest9-20261002-v1',id='DEEP_LAYER_STAGE1_REST9_V1',markets=ids,jobs=[j for j in old['jobs'] if j['market'] in ids],baseline={str(i):old['baseline'][str(i)] for i in ids},resume_of=old['job_id'],reused_first_market=2671717)
save('PROTOCOL.json',plan);save('SOURCE_PARENT.json',proof)
print(json.dumps(dict(status='BUILT',markets=ids,policy_unchanged=True,first_market_rerun=False)))
