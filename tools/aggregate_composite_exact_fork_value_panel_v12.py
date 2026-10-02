from __future__ import annotations
import json, math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/lan_worker_returns'
GEOM={
 2022527:'target-core-checkpoint-geometry-v11-2022527-20260912-v2',
 2022538:'target-core-checkpoint-geometry-v11-2022538-20260912-v1',
 2022602:'target-core-checkpoint-geometry-v11-2022602-20260912-v1',
}
PAIRS=[
 (2022527,'UP_160','v10a-panel-up160-a-20260912','v10a-panel-up160-c-20260912'),
 (2022527,'UP_205','v10a-panel-up205-a-20260912','v10a-panel-up205-c-20260912'),
 (2022527,'UP_481','v10a-panel-up481-a-20260912','v10a-panel-up481-c-20260912'),
 (2022527,'DOWN_252','v10a-panel-down252-a-20260912','v10a-panel-down252-c-20260912'),
 (2022527,'DOWN_450','v10a-panel-down450-a-20260912','v10a-panel-down450-c-20260912'),
 (2022527,'DOWN_499','target-core-composite-v10a-2022527-down499-clamped-20260912-v1','target-core-composite-v10a-2022527-down499-composite-20260912-v1'),
 (2022538,'UP_140','v10a-2538-up140-a-20260912','v10a-2538-up140-c-20260912'),
 (2022538,'UP_594','v10a-2538-up594-a-20260912','v10a-2538-up594-c-20260912'),
 (2022602,'DOWN_311','v10a-2602-down311-a-20260912','v10a-2602-down311-c-20260912'),
 (2022602,'UP_247','v10a-2602-up247-a-20260912','v10a-2602-up247-c-20260912'),
 (2022602,'UP_464','v10a-2602-up464-a-20260912','v10a-2602-up464-c-20260912'),
]

def load(j):return json.loads((R/j/'result.json').read_text(encoding='utf-8'))
def ranks(v):
 o=sorted(range(len(v)),key=lambda i:v[i]);r=[0.]*len(v);i=0
 while i<len(o):
  j=i+1
  while j<len(o) and v[o[j]]==v[o[i]]:j+=1
  x=(i+j-1)/2+1
  for k in o[i:j]:r[k]=x
  i=j
 return r
def spear(a,b):
 if len(a)<3:return None
 x=ranks(a);y=ranks(b);mx=sum(x)/len(x);my=sum(y)/len(y);num=sum((u-mx)*(v-my) for u,v in zip(x,y));dx=sum((u-mx)**2 for u in x);dy=sum((v-my)**2 for v in y)
 return num/math.sqrt(dx*dy) if dx>0 and dy>0 else None

geoms={}
for m,j in GEOM.items():
 d=load(j)
 geoms[m]={(x['source_key'],int(x['t'])):x for x in d['candidate_checkpoints']}
rows=[]
for m,key,ja,jc in PAIRS:
 A=load(ja);C=load(jc);h=(A.get('exact_trigger_hits') or [{}])[0];g=geoms[m].get((key,int(h['t'])))
 if g is None:raise RuntimeError(f'missing geometry {m} {key} {h.get("t")}')
 al=C.get('composite_allocation') or {};ov=float(al.get('overflow') or 0)
 if ov<=1e-9:raise RuntimeError(f'not crossing {m} {key}')
 inv=g['trigger_inventory'];u=float(inv['UP']);d=float(inv['DOWN']);cost=float(g['trigger_cost']);floor=min(u,d)-cost;best=max(u,d)-cost
 raw=1.2*(2*float(g['mid'])-1)+.3*float(g['depth_imbalance']);align=raw if g['side']=='UP' else -raw
 pfA=A.get('post_fork_metrics') or {};pfC=C.get('post_fork_metrics') or {}
 delta_floor=float(C['terminal_worst'])-float(A['terminal_worst']);delta_best=float(pfC['terminal_best'])-float(pfA['terminal_best']);delta_score=float(C['core_similarity'])-float(A['core_similarity']);path_improve=float(A['path_mse'])-float(C['path_mse'])
 if delta_floor>1e-9 and delta_best>1e-9:cls='BOTH_IMPROVE'
 elif delta_floor>1e-9 and delta_best<-1e-9:cls='FLOOR_UP_BEST_DOWN'
 elif delta_floor<-1e-9 and delta_best>1e-9:cls='FLOOR_DOWN_BEST_UP'
 elif delta_floor<-1e-9 and delta_best<-1e-9:cls='BOTH_WORSE'
 else:cls='MIXED_NEAR_ZERO'
 opp=g['opposite_side'];opp_def=float(g['deficit'][opp]);own_net=u-d
 rows.append(dict(market=m,key=key,t=int(h['t']),side=g['side'],role=g['role'],progress=float(g['progress']),wall_phase=float(g['wall_phase']),source_fill_fraction=float(g['source_fill_fraction']),residual=float(g['residual']),source_unfilled=float(g['source_unfilled']),residual_fraction=float(g['residual'])/float(g['source_unfilled']),overflow=ov,overflow_to_residual=ov/max(float(g['residual']),1e-12),active_price=float((C.get('active_births') or [{}])[0].get('price') or 0),trigger_floor=floor,trigger_best=best,abs_net=abs(own_net),signed_net=own_net,pending_count=int(g['pending_count']),depth_imbalance=float(g['depth_imbalance']),mid=float(g['mid']),thesis_alignment=align,opposite_deficit=opp_def,opposite_deficit_to_overflow=opp_def/max(ov,1e-12),delta_floor=delta_floor,delta_best=delta_best,delta_score=delta_score,path_improve=path_improve,delta_events=int(C['our_profile']['event_batches'])-int(A['our_profile']['event_batches']),value_class=cls,prefix_equal=(h.get('prefix_digest')==(C.get('exact_trigger_hits') or [{}])[0].get('prefix_digest')),safety=bool(A['safety_gate']['pass'] and C['safety_gate']['pass'])))
features=['progress','wall_phase','source_fill_fraction','residual_fraction','overflow','overflow_to_residual','active_price','trigger_floor','trigger_best','abs_net','signed_net','pending_count','depth_imbalance','mid','thesis_alignment','opposite_deficit','opposite_deficit_to_overflow']
outcomes=['delta_floor','delta_best','delta_score','path_improve']
corr={f:{o:spear([r[f] for r in rows],[r[o] for r in rows]) for o in outcomes} for f in features}
out={'version':'COMPOSITE_EXACT_FORK_VALUE_PANEL_V12','researchOnly':True,'rows':rows,'class_counts':dict(Counter(r['value_class'] for r in rows)),'spearman_exploratory_only':corr,'guards':{'all_prefix_equal':all(r['prefix_equal'] for r in rows),'all_safety_pass':all(r['safety'] for r in rows),'target_action_used_for_selection':False,'parameter_threshold_fit':False},'interpretation_boundary':['Exact-fork outcome vector is diagnostic discovery evidence, not a deployment reward.','Spearman on this tiny selected panel is exploratory only; no threshold or selector may be promoted from it.','Checkpoints originate from OUR passive causal states, not Target action timestamps.']}
op=ROOT/'data/research/COMPOSITE_EXACT_FORK_VALUE_PANEL_V12_20260912.json';op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(op.relative_to(ROOT)),'n':len(rows),'classes':out['class_counts'],'guards':out['guards'],'top_abs_corr':sorted([(abs(v[o] or 0),f,o,v[o]) for f,v in corr.items() for o in outcomes],reverse=True)[:16]},ensure_ascii=False,indent=2))