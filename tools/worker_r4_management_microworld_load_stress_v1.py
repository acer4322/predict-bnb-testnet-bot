from __future__ import annotations
import json,glob,random,time,hashlib,math
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent
files=sorted(HERE.glob('r4_p0b_objective_grouping_context_replication_branches_*_v1.json'))
seeds=[]
for p in files:
 d=json.loads(p.read_text(encoding='utf-8'));seeds.extend(d.get('rows') or [])
if not seeds: raise RuntimeError('no staged grouping seeds')
def ctx(seed): return dict(seed.get('context') or {})
def run_episode(seed,i):
 r=random.Random(2026082901+i);c=ctx(seed)
 active=max(0,int(round(float(c.get('same_side_active_objectives') or 0)+r.choice([-1,0,0,1]))))
 residual=max(0.,float(c.get('same_side_total_residual') or 0)+r.choice([-18.,0.,0.,18.]))
 age=max(0.,float(c.get('same_side_oldest_objective_age_s') or 0)+r.uniform(-5,5))
 requested=r.choice([6.,9.,12.,18.,24.,36.]);gap=max(residual,18.);load=requested/gap
 # factorized event world: weak and dominant streams generated independently conditional on responsibility state;
 # coefficients are fixed before run and only encode plausible monotonic stress relationships, not Target labels.
 z_w=-0.3 + 0.35*min(active,3) - 0.55*load - 0.004*age
 z_d=-2.1 + 0.22*min(active,3) + 0.18*load
 pw=1/(1+math.exp(-z_w));pd=1/(1+math.exp(-z_d))
 weak=0;dom=0;filled=0.;steps=r.randint(6,18)
 for _ in range(steps):
  if r.random()<pw/steps*3.2:
   q=min(requested-filled,r.choice([3.,6.,9.,18.]));q=max(0.,q);filled+=q;weak=1
  if r.random()<pd/steps*3.2: dom=1
 completion=int(filled+1e-9>=requested)
 return (load,active,age,weak,dom,completion,filled/requested if requested else 0.)
N=int(__import__('sys').argv[1]) if len(__import__('sys').argv)>1 else 100000
t=time.perf_counter();rows=[run_episode(seeds[i%len(seeds)],i) for i in range(N)];a=np.array(rows,float)
bins=[0,.25,.5,.75,1,1.5,2,10];stats=[]
for lo,hi in zip(bins[:-1],bins[1:]):
 q=a[(a[:,0]>=lo)&(a[:,0]<hi)]
 if len(q):stats.append({'loadBin':[lo,hi],'n':len(q),'weakEventRate':float(q[:,3].mean()),'domEventRate':float(q[:,4].mean()),'completionRate':float(q[:,5].mean()),'meanFillFraction':float(q[:,6].mean()),'meanOwnerCount':float(q[:,1].mean())})
owner=[]
for k in range(4):
 q=a[a[:,1]==k]
 if len(q):owner.append({'ownerCount':k,'n':len(q),'weakEventRate':float(q[:,3].mean()),'completionRate':float(q[:,5].mean()),'meanFillFraction':float(q[:,6].mean())})
out={'version':'R4_MANAGEMENT_MICROWORLD_LOAD_STRESS_V1','researchOnly':True,'promotionEvidence':False,'episodes':N,'seedRows':len(seeds),'seedFiles':len(files),'elapsedSec':time.perf_counter()-t,'loadBins':stats,'ownerBins':owner,'overall':{'weakEventRate':float(a[:,3].mean()),'domEventRate':float(a[:,4].mean()),'completionRate':float(a[:,5].mean()),'meanFillFraction':float(a[:,6].mean())},'guard':'Synthetic micro-world discovery only. Fixed monotonic stress coefficients are not learned from Target labels and cannot validate direction; use this only to test interaction behavior and simulator throughput. Any candidate must return to realistic-HFT whole-market validation.'}
Path('r4_management_microworld_load_stress_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))