from __future__ import annotations
import json,itertools,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/TARGET_CORE_CYCLE_FAST_SURROGATE_V1_AGGREGATE_20260912.json'
files=[B/f'target-core-fast-surrogate-v1-shard{i}-20260912/result.json' for i in range(4)]
rows=[];trials=0
for p in files:
 d=json.loads(p.read_text(encoding='utf-8'));rows+=d['rows'];trials+=d['trials']
assert len(rows)==720 and len({r['architecture_id'] for r in rows})==720 and trials==36000
rows=sorted(rows,key=lambda r:r['architecture_id'])

def matched_delta(field,a,b,filter_fn=lambda c:True):
 by={}
 dims=list(rows[0]['config'])
 other=[x for x in dims if x!=field]
 for r in rows:
  c=r['config']
  if not filter_fn(c):continue
  key=tuple((k,c[k]) for k in other);by.setdefault(key,{})[c[field]]=r
 ds=[]
 for pair in by.values():
  if a in pair and b in pair:ds.append(pair[b]['mean_similarity']-pair[a]['mean_similarity'])
 return dict(n=len(ds),mean_delta=statistics.fmean(ds) if ds else None,median_delta=statistics.median(ds) if ds else None,positive_fraction=sum(x>0 for x in ds)/len(ds) if ds else None,min=min(ds) if ds else None,max=max(ds) if ds else None)

contrasts={
 'clock_TIME_to_SOURCE_EVENT':matched_delta('clock','TIME','SOURCE_EVENT'),
 'own_OFF_to_ON':matched_delta('own_feedback',0,1),
 'residual_FRESH_to_PERSIST':matched_delta('residual','FRESH','PERSIST'),
 'progress_REVERSE_to_FORWARD':matched_delta('progression','REVERSE','FORWARD'),
 'progress_FLAT_to_FORWARD':matched_delta('progression','FLAT','FORWARD'),
 'rearm_FILL_to_TERMINAL':matched_delta('rearm','FILL_ONLY','TERMINAL'),
 'rearm_FILL_to_ALL':matched_delta('rearm','FILL_ONLY','ALL'),
 'market_direction_ON_to_OFF':matched_delta('market_direction',1,0),
}
# Objective contrasts need hold all other dimensions fixed.
for x in ['REPAIR_ONLY','EXPAND_ONLY','ALTERNATE','SERIAL_FLOOR_FIRST']:
 contrasts[f'objective_{x}_to_JOINT']=matched_delta('objective',x,'JOINT')

# Conditional contrasts around the native-discovered core neighborhood.
def core_neighborhood(c):return c['progression']=='FORWARD' and c['residual']=='PERSIST' and c['rearm'] in ('TERMINAL','ALL')
conditional={
 'own_OFF_to_ON_core':matched_delta('own_feedback',0,1,core_neighborhood),
 'clock_TIME_to_SOURCE_core':matched_delta('clock','TIME','SOURCE_EVENT',core_neighborhood),
 'market_direction_ON_to_OFF_core':matched_delta('market_direction',1,0,core_neighborhood),
}
# Top one-field neighbor jumps.
index={tuple(sorted(r['config'].items())):r for r in rows};jumps=[]
for r in rows:
 c=r['config']
 for field,val in c.items():
  options=sorted({x['config'][field] for x in rows},key=str)
  for alt in options:
   if alt==val:continue
   cc=dict(c);cc[field]=alt;rr=index.get(tuple(sorted(cc.items())))
   if rr and rr['mean_similarity']>r['mean_similarity']:
    jumps.append(dict(field=field,from_value=val,to_value=alt,delta=rr['mean_similarity']-r['mean_similarity'],from_score=r['mean_similarity'],to_score=rr['mean_similarity'],from_id=r['architecture_id'],to_id=rr['architecture_id'],context={k:v for k,v in c.items() if k!=field}))
jumps=sorted(jumps,key=lambda x:x['delta'],reverse=True)

top=sorted(rows,key=lambda r:r['mean_similarity'],reverse=True)[:30]
# Native-consistent archetype: source event + own feedback + persist + forward + terminal + joint + no market-direction coupling.
core_cfg=dict(clock='SOURCE_EVENT',own_feedback=1,residual='PERSIST',progression='FORWARD',rearm='TERMINAL',objective='JOINT',market_direction=0)
core=index[tuple(sorted(core_cfg.items()))]
# Negative controls analogous to native tests.
def get(**kw):
 c=dict(core_cfg);c.update(kw);return index[tuple(sorted(c.items()))]
controls={
 'core':core,
 'reverse_progression':get(progression='REVERSE'),
 'fresh_residual':get(residual='FRESH'),
 'fill_only_rearm':get(rearm='FILL_ONLY'),
 'repair_only':get(objective='REPAIR_ONLY'),
 'expand_only':get(objective='EXPAND_ONLY'),
 'alternate':get(objective='ALTERNATE'),
 'serial_floor_first':get(objective='SERIAL_FLOOR_FIRST'),
 'market_direction_on':get(market_direction=1),
 'time_clock':get(clock='TIME'),
 'own_feedback_off':get(own_feedback=0),
}
# Surrogate is directionally trustworthy only if core beats the decisive native negative controls.
required=['reverse_progression','fresh_residual','fill_only_rearm','repair_only','expand_only','alternate','serial_floor_first']
directional={k:core['mean_similarity']-controls[k]['mean_similarity'] for k in required}
pass_native_direction=all(v>0 for v in directional.values())
out=dict(version='TARGET_CORE_CYCLE_FAST_SURROGATE_V1_AGGREGATE',researchOnly=True,total_architectures=len(rows),total_trials=trials,seeds_per_architecture=50,top=top,contrasts=contrasts,conditional_contrasts=conditional,strongest_one_field_positive_jumps=jumps[:40],native_core_config=core_cfg,native_core_result=core,native_analog_controls=controls,native_direction_deltas=directional,passes_native_direction_sanity=pass_native_direction,boundary=['Fast execution is OUR-native surrogate, not HFT certification.','Target path is scoring-only.','Architecture search is discrete; no coefficient/threshold/sizing optimization.','Any candidate mechanism must return unchanged to native HFT.'])
OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'ok':True,'architectures':len(rows),'trials':trials,'top_id':top[0]['architecture_id'],'top_score':top[0]['mean_similarity'],'top_config':top[0]['config'],'native_core_score':core['mean_similarity'],'native_direction_deltas':directional,'passes_native_direction_sanity':pass_native_direction,'key_contrasts':{k:contrasts[k] for k in ['residual_FRESH_to_PERSIST','progress_REVERSE_to_FORWARD','rearm_FILL_to_TERMINAL','objective_REPAIR_ONLY_to_JOINT','objective_EXPAND_ONLY_to_JOINT','objective_ALTERNATE_to_JOINT','objective_SERIAL_FLOOR_FIRST_to_JOINT']}},ensure_ascii=False),flush=True)
