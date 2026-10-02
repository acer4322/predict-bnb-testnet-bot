from pathlib import Path
import json, numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES={
 'DEV':[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json'],
 'ONESHOT20':[P/'r4_management_v6_market_disjoint20_objective_memory_v1.json'],
 'FORMAL20_CONSUMED':[P/'r4_management_v61_formal_cohort20_objective_memory_v1.json']}
H=[5,15,30]; ECON={'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'}
def load(ps):
 out=[]
 for p in ps: out += json.loads(p.read_text(encoding='utf-8'))['rows']
 return out
def classify(r,h,rel):
 es=[e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000]
 opens={str(e.get('responsibilityId')):int(e['dtMs']) for e in es if e['eventType']=='RESPONSIBILITY_OPENED' and e.get('responsibilityId')}
 flags={'existingMaker':0,'newMaker':0,'taker':0}
 for e in es:
  if e['eventType'] not in ECON or e.get('sideRelation')!=rel: continue
  if e['eventType']=='TAKER_EXECUTION': flags['taker']=1; continue
  rid=e.get('responsibilityId'); op=opens.get(str(rid)) if rid else None
  if op is not None and op<int(e['dtMs']): flags['newMaker']=1
  else: flags['existingMaker']=1
 return flags
def main():
 rep={'version':'R4_MANAGEMENT_V6_2_NEW_STREAM_COMPOSITION_DIAGNOSTIC_V1','researchOnly':True,'promotionEvidence':False,'cohorts':{}}
 for name,ps in FILES.items():
  rows=load(ps); hh={}
  for h in H:
   z={}
   for rel in ('WEAK','DOMINANT'):
    fs=[classify(r,h,rel) for r in rows]
    z[rel]={'existingMakerRate':float(np.mean([x['existingMaker'] for x in fs])),'newMakerRate':float(np.mean([x['newMaker'] for x in fs])),'takerRate':float(np.mean([x['taker'] for x in fs])),'anyNewRate':float(np.mean([x['newMaker'] or x['taker'] for x in fs])),'newRows':sum(x['newMaker'] or x['taker'] for x in fs),'newMakerOnlyRows':sum(x['newMaker'] and not x['taker'] for x in fs),'takerOnlyRows':sum(x['taker'] and not x['newMaker'] for x in fs),'bothNewMakerAndTakerRows':sum(x['taker'] and x['newMaker'] for x in fs)}
   hh[str(h)]=z
  rep['cohorts'][name]={'roots':len(rows),'horizons':hh}
 rep['interpretation']='If NEW stream is heterogeneous between newly-opened Maker responsibility and Taker/exogenous events, they should be represented separately rather than forced into one stream-occurrence head.'
 (P/'r4_management_v62_new_stream_composition_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
