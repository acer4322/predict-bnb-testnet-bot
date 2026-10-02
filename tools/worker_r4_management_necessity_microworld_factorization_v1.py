from __future__ import annotations
import json, os, math, random
from pathlib import Path
import numpy as np
OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
rng=np.random.default_rng(9127)
N=400000
# Synthetic discovery world: staleness alone should not imply unnecessary if a live pipeline still owns meaningful unresolved work.
age=rng.exponential(55,N)
event_rate=rng.gamma(1.8,1.4,N)
live_owner=rng.binomial(1,0.55,N)
unresolved=rng.gamma(2.0,8.0,N)
progress=rng.beta(1.2,4.0,N)
pending_cancel=rng.binomial(1,0.08,N)
recent_fill=rng.binomial(1,np.clip(0.35*np.exp(-age/140)+0.12*live_owner,0,0.8))
# assumed mechanism for discovery only
logit=(-1.0 + 0.012*age - 0.28*event_rate - 1.35*live_owner - 0.035*unresolved*live_owner + 0.9*progress + 0.8*pending_cancel - 0.7*recent_fill)
p=1/(1+np.exp(-np.clip(logit,-20,20)))
y=rng.binomial(1,p)
# candidate scores
s_stale=0.012*age-0.28*event_rate
s_occ=s_stale-1.35*live_owner-0.035*unresolved*live_owner+0.9*progress+0.8*pending_cancel-0.7*recent_fill
from sklearn.metrics import roc_auc_score
rep={'version':'R4_MANAGEMENT_NECESSITY_MICROWORLD_FACTORIZATION_V1','researchOnly':True,'promotionEvidence':False,'episodes':N,'auc':{'stalenessOnly':float(roc_auc_score(y,s_stale)),'stalenessPlusOccupancy':float(roc_auc_score(y,s_occ))},'rates':{},'guard':'Synthetic mechanistic world with assumed hazards; discovery only. Any candidate must return to strict-past realistic-HFT whole-market integrated shadow.'}
for lo in [0,1]:
 for old in [0,1]:
  mask=(live_owner==lo)&((age>=60)==old)
  rep['rates'][f'live{lo}_old{old}']={'n':int(mask.sum()),'unnecessaryRate':float(y[mask].mean()),'meanAge':float(age[mask].mean()),'meanUnresolved':float(unresolved[mask].mean())}
OUT.mkdir(parents=True,exist_ok=True);(OUT/'result.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
