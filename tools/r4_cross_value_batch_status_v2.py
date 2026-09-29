import json
from pathlib import Path
R=Path('data/research/r4_v0/hourly')
for fn,key in [('r4_baseline_cross_candidates_checkpoint_v2.json','markets'),('r4_cross_value_paired_forks_checkpoint_v2.json','rows')]:
 p=R/fn
 try:
  d=json.loads(p.read_text()); xs=d.get(key,[])
  print(fn,len(xs))
  if key=='markets': print('candidates',sum(x.get('candidateCount',0) for x in xs),'seedEq',sum(bool(x.get('seedEquivalent')) for x in xs))
  else:
   from collections import Counter
   print(Counter(x.get('class','ERR') for x in xs)); print('errors',sum('error' in x for x in xs))
 except Exception as e: print(fn,'ERR',e)
