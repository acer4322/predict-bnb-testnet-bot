from __future__ import annotations
import json,statistics,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=json.loads((P/'r4_management_simulator_execution_primitives_dev7_v1.json').read_text())
UN=json.loads((P/'r4_management_simulator_execution_primitives_val8_v1.json').read_text())
first8={int(x['marketId']) for x in UN['markets'][:8]};next8={int(x['marketId']) for x in UN['markets'][8:16]}
dev=DEV['rows']+[r for r in UN['rows'] if int(r['marketId']) in first8];val=[r for r in UN['rows'] if int(r['marketId']) in next8]
def feat(r):return (round(float(r['price'])*5),int(float(r['secondsLeft'])//30),min(2,int(r['ownerCount'])),min(2,int(float(r['weakResponsibilityCount']))),min(3,int(float(r['events15s'])//2)),min(3,int(float(r['oldestOwnerAge'])//10)))
def dist(a,b):
 w=(1,1,1,1,.5,.5);return sum(wi*(0 if x==y else 1) for wi,x,y in zip(w,a,b))
partials=[r for r in dev if int(r['anyFill5s']) and not int(r['completed5s']) and float(r['requestedQty'])>0]
positive=[r for r in val if int(r['anyFill5s']) and float(r['requestedQty'])>0]
if len(val)<20:raise SystemExit(f'INSUFFICIENT_VALIDATION_ROOTS {len(val)}')
if len(positive)<5:raise SystemExit(f'INSUFFICIENT_POSITIVE_FILL_ROOTS {len(positive)}')
def pred_frac(r):
 if not int(r['anyFill5s']):return 0.0
 if int(r['completed5s']):return 1.0
 if not partials:return .5
 z=sorted(partials,key=lambda x:dist(feat(r),feat(x)))[:min(7,len(partials))]
 return max(0.,min(1.,statistics.median([float(x['fillQty5s'])/max(1e-9,float(x['requestedQty'])) for x in z])))
rows=[]
for r in val:
 af=min(1.,float(r['fillQty5s'])/max(1e-9,float(r['requestedQty']))) if float(r['requestedQty'])>0 else 0.;pf=pred_frac(r);pq=pf*float(r['requestedQty']);rows.append({'marketId':r['marketId'],'actualFraction':af,'predFraction':pf,'actualQty':float(r['fillQty5s']),'predQty':pq,'positive':int(r['anyFill5s']),'completed':int(r['completed5s'])})
pos=[x for x in rows if x['positive']];frac_mae=sum(abs(x['predFraction']-x['actualFraction']) for x in pos)/len(pos);scale=max(1.,sum(abs(x['actualQty']) for x in rows)/len(rows));qty_nmae=sum(abs(x['predQty']-x['actualQty']) for x in rows)/len(rows)/scale
rep={'version':'R4_MANAGEMENT_SIMULATOR_EXECUTION_QUANTITY_V1','researchOnly':True,'developmentRoots':len(dev),'developmentPartialFillRoots':len(partials),'validationRoots':len(val),'positiveFillRoots':len(pos),'completedPositiveRoots':sum(x['completed'] for x in pos),'conditionalFillFractionMAE':frac_mae,'reconstructedFillQtyNormalizedMAE':qty_nmae,'gatePass':frac_mae<=.25 and qty_nmae<=.35,'isolation':'Oracle occurrence/completion supplied by execution occurrence layer; this gate evaluates positive-fill quantity representation only.','rows':rows};(P/'r4_management_simulator_execution_quantity_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in rep.items() if k!='rows'},indent=2))
