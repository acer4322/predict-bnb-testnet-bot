import json
from pathlib import Path
from collections import Counter,defaultdict
p=Path('data/research/r4_v0/hourly/r4_cross_value_paired_forks_checkpoint_v2.json'); d=json.loads(p.read_text()); rows=[x for x in d.get('rows',[]) if 'error' not in x]
print('rows',len(rows),'classes',Counter(x.get('class') for x in rows))
by=defaultdict(Counter)
for x in rows: by[x['marketId']][x.get('class')]+=1
print('markets',len(by))
for m,c in sorted(by.items()): print(m,dict(c))
vals=[float(x.get('deltaFloor',0)) for x in rows if x.get('deltaFloor') is not None]
if vals: print('sumDelta',sum(vals),'meanDelta',sum(vals)/len(vals),'nonzero',sum(abs(v)>1e-9 for v in vals))
