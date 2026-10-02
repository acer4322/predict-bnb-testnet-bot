"""Create the explicitly authorized successor from hash-verified frozen sources."""
import hashlib,json,shutil
from pathlib import Path
P=Path(__file__).resolve().parent; ROOT=P.parents[2]
parent=P.parent/'btc5m_cg1at_fresh100a_20260930'
transport=P.parent/'btc5m_floor_ab_stage2_rest_20261001_v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,x): (P/n).write_text(json.dumps(x,indent=2,ensure_ascii=False),encoding='utf-8')
def patch(n,a,b):
 p=P/n;s=p.read_text(encoding='utf-8');assert s.count(a)==1,(n,a,s.count(a));p.write_text(s.replace(a,b,1),encoding='utf-8',newline='\n')
assert not (P/'PROTOCOL.json').exists()
manifest=json.loads((parent/'MANIFEST.json').read_text())
sources={}
for n,h in manifest['files'].items():
 assert sha(parent/n)==h,n
 sources[f'{parent.name}/{n}']=h
 if n in ('PROTOCOL.json','SOURCE_PARENT.json','worker.py','dispatch.py','discover.py'):continue
 dst=P/n;assert not dst.exists(),n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(parent/n,dst)
for n in ('dispatch.py','discover.py'):
 shutil.copy2(transport/n,P/n)
patch('discover.py','btc5m-floor-ab-stage2-rest-20261001-v1','btc5m-deep-layer-stage1-20261002-v1')
patch('sizing.py',"    source=__import__('w_ladder').instrument(source)\\n", "    source=__import__('w_ladder').instrument(source)\\n    source=__import__('deep_layer').instrument(source)\\n")
patch('patches.py','target_core_cycle_active_v8_c100_','target_core_cycle_active_v8_deep1_')
patch('overlay/run_variant.py','import w_ladder','import w_ladder\nimport deep_layer')
patch('overlay/run_variant.py',"ops=__import__('passive_offset').resolve(f, ops)\\n", "ops=__import__('passive_offset').resolve(f, ops)\\n\"+indent+\"ops=__import__('deep_layer').on_plan(f, ops)\\n")
patch('overlay/run_variant.py','module.load=load;sys.argv=',
 "deep_layer.install(ctx,module.roles,v12g_lowfreeze,lambda st,s,p,q:governor.quantity(st,s,p,q,'PASSIVE','DEEP_LAYER'),lambda st,s,p,q:risk_guard.quantity(st,s,p,q,'PASSIVE','DEEP_LAYER'),with_plan)\nmodule.load=load;sys.argv=")
patch('overlay/run_variant.py',"r['cg3_w_ladder']=w_ladder.finish(out)", "r['cg3_w_ladder']=w_ladder.finish(out)\n        r['deep_layer']=deep_layer.finish(out)")
spec=ROOT/'docs/research_specs/DEEP_LAYER_SPEC_20261001_ZH.md'
markets=ROOT/'docs/research_specs/DEEP_LAYER_MARKETS_20261001.json'
ids=json.loads(markets.read_text(encoding='utf-8'))['stage1'];assert len(ids)==10
old=json.loads((parent/'PROTOCOL.json').read_text())
jobs=[];baseline={}
for mid in ids:
 j=next(j for j in old['jobs'] if j['market']==mid and j['arm']=='CG1AT')
 env=dict(j['env_v12']);env['V12G_DEEP_LAYER']='ON';jobs.append(dict(j,arm='DEEP',env_v12=env))
 b=ROOT/'data/research/lan_worker_returns/btc5m-cg1at-fresh100a-20260930/arms'/f'c100_CG1AT_{mid}'
 assert json.loads((b/'result.json').read_text())['status']=='COMPLETE'
 assert json.loads((b/'EXECUTION.json').read_text())['env_v12']==j['env_v12']
 baseline[str(mid)]=dict(remote_relative=f'btc5m-cg1at-fresh100a-20260930/arms/c100_CG1AT_{mid}',files={n:sha(b/n) for n in ('result.json','clock_trace.json.gz','execution_clock.json','AUDIT.json','EXECUTION.json')})
save('PROTOCOL.json',dict(id='DEEP_LAYER_STAGE1_V1',job_id='btc5m-deep-layer-stage1-20261002-v1',markets=ids,jobs=jobs,baseline=baseline,
 parallel_paths=4,max_threads=4,native_threads_per_path=1,model_fits=0,capital_cap=None,spec_sha256=sha(spec),markets_sha256=sha(markets),economics=False,no_replacement=True))
save('SOURCE_PARENT.json',dict(parent=parent.name,source_hashes=sources,base_manifest_sha256=sha(parent/'base/manifest.json')))
save('OVERLAP.json',dict(status='NOT_EQUIVALENT',spec_sha256=sha(spec),distinct='two physical sides; one per side; bid minus two ticks; unfilled TTL2000ms',
 prior_modules=['w_ladder_r1','s_boost','cheap_insurance','zone_mirror','passive_offset_r3'],prior_negative='WL60 ~-30 and WL90 ~-36 on consumed40; V58 deeper quotes and BTC15M ladder negative. Stage1 is mechanism only.',
 references=['docs/agents/RESEARCH_CURRENT.md:109','docs/handoffs/CLAUDE_CG1AT_HANDOFF_20260929_ZH.md:69','data/research/btc5m_cg1at_handoff_20260929/modules/w_ladder_r1.py']))
print(json.dumps(dict(status='BUILT',paths=10,baseline_reused=True,parent_files_verified=len(sources))))
