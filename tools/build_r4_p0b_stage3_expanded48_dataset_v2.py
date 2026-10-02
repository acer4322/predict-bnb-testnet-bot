from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
ROLES={'REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'}
OUT=P/'r4_p0b_stage3_expanded48_dataset_v2.csv'; META=P/'r4_p0b_stage3_expanded48_dataset_v2.json'
# 1) collect frozen known-role branch rows
rows={}
for pat in ['r4_p0b_role_group_branches_*_v1.json','r4_p0b_role_anatomy_extension_branches_*_v1.json','r4_p0b_objective_grouping_context_replication_branches_*_v1.json']:
 for f in P.glob(pat):
  try:d=json.loads(f.read_text(encoding='utf-8'))
  except Exception:continue
  rr=d.get('rows',[]) if isinstance(d,dict) else []
  if not isinstance(rr,list):continue
  for r in rr:
   role=r.get('role')
   if role not in ROLES:continue
   key=(int(r['marketId']),str(r['candidateKey']))
   z=rows.setdefault(key,{'marketId':key[0],'candidateKey':key[1],'knownRole':role,'candidate':None,'context':None,'final':None,'sources':[]})
   if z['knownRole']!=role:raise RuntimeError(f'label conflict {key}')
   z['sources'].append(f.name)
   if isinstance(r.get('candidate'),dict):z['candidate']=r['candidate']
   if isinstance(r.get('context'),dict):z['context']=r['context']
   if isinstance(r.get('final'),dict):z['final']=r['final']
# 2) candidate snapshots from grouping discovery for rows missing candidate
for f in P.glob('r4_p0b_objective_grouping_replication_discovery_*_v1.json'):
 d=json.loads(f.read_text(encoding='utf-8'))
 for m in d.get('rows',[]):
  for op in m.get('opportunities',[]):
   key=(int(m['marketId']),str(op.get('candidateKey')))
   if key in rows and rows[key]['candidate'] is None: rows[key]['candidate']=op
# 3) lane-F counterfactual table enriches original 31 candidate/final
cf=json.loads((P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json').read_text(encoding='utf-8'))
for r in cf.get('rows',[]):
 key=(int(r['marketId']),str(r['candidateKey']))
 if key not in rows:continue
 if rows[key]['candidate'] is None and isinstance(r.get('candidateStrictPast'),dict): rows[key]['candidate']=r['candidateStrictPast']
 if isinstance(r.get('finalBranches'),dict):rows[key]['final']=r['finalBranches']
# 4) replayed lane-F group contexts
ctxfile=P/'r4_p0b_stage3_group_context_v1.json'
if ctxfile.exists():
 d=json.loads(ctxfile.read_text(encoding='utf-8'))
 for r in d.get('rows',[]):
  key=(int(r['marketId']),str(r['candidateKey']))
  if key in rows and isinstance(r.get('context'),dict): rows[key]['context']=r['context']
assert len(rows)==48,len(rows)
assert all(z['candidate'] is not None for z in rows.values())
assert all(z['context'] is not None for z in rows.values())
assert all(z['final'] is not None for z in rows.values())
out=[]
for key,z in sorted(rows.items()):
 c=z['candidate']; g=z['context']; f=z['final']; base=f.get('REJECT_NO_ACTION') or f.get('RESERVATION'); add=f.get('ADDITIVE'); cred=f.get('CREDIT')
 if not all(isinstance(x,dict) for x in [base,add,cred]):raise RuntimeError(f'bad final {key}')
 q={'marketId':z['marketId'],'candidateKey':z['candidateKey'],'knownRole':z['knownRole'],'sourceArtifacts':'|'.join(sorted(set(z['sources'])))}
 for k,v in c.items():
  if isinstance(v,(int,float,str)) or v is None:q[k]=v
 for k,v in g.items():
  if isinstance(v,(int,float,str)) or v is None:q[k]=v
 q.update({'target_add_floor_gain':float(add['floor'])-float(base['floor']),'target_add_absnet_gain':float(base['absNet'])-float(add['absNet']),'target_credit_floor_gain':float(cred['floor'])-float(base['floor']),'target_credit_absnet_gain':float(base['absNet'])-float(cred['absNet'])})
 out.append(q)
df=pd.DataFrame(out);df.to_csv(OUT,index=False)
meta={'version':'R4_P0B_STAGE3_EXPANDED48_DATASET_V2','researchOnly':True,'actionAuthority':False,'rows':len(df),'markets':int(df.marketId.nunique()),'roleCounts':df.knownRole.value_counts().to_dict(),'candidateStateCoverage':48,'groupContextCoverage':48,'branchEconomicsCoverage':48,'labelConflictCount':0,'guards':['Frozen role rule reused exactly','No threshold or utility sweep','Future branch economics are labels only','Runtime candidate/group features are strict-past','No R3/8781/Echtgeld changes']}
META.write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(meta,indent=2,ensure_ascii=False))
