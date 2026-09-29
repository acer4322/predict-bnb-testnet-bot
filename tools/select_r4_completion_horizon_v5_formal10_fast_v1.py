from __future__ import annotations
import json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_completion_horizon_v5_formal10_preregistered.json'
contract=json.loads((P/'r4_completion_horizon_admission_v5_formal_contract.json').read_text())
assert contract['status']=='FROZEN_BEFORE_COHORT_SELECTION'
# Recent live-R3 markets are excluded using only recent rowid chronology, never outcomes.
live=set()
try:
 c=sqlite3.connect(ROOT/'data/strategy_r3s_r31_echtgeld_v1.db'); mx=c.execute('select max(rowid) from our_decisions').fetchone()[0] or 0
 live={int(r[0]) for r in c.execute('select distinct market_id from our_decisions where rowid>?',(max(0,mx-12000),))}; c.close()
except Exception:pass
cands=[int(d['marketId']) for d in v2.choose_files(400) if int(d['marketId']) not in live]
need=set(cands[:180]); pats={str(x).encode():x for x in need}; used=set(); scanned=0
for p in P.iterdir():
 if not p.is_file() or p==OUT or p.suffix.lower() not in {'.json','.md','.txt','.csv'}:continue
 try:data=p.read_bytes()
 except Exception:continue
 scanned+=len(data)
 for b,x in pats.items():
  if x in used:continue
  start=0
  while True:
   j=data.find(b,start)
   if j<0:break
   left=data[j-1:j] if j>0 else b''; right=data[j+len(b):j+len(b)+1]
   if (not left or not 48<=left[0]<=57) and (not right or not 48<=right[0]<=57):used.add(x);break
   start=j+1
sel=[x for x in cands if x not in used][:10]
if len(sel)<10:raise RuntimeError(f'only {len(sel)} unused candidates')
out={'version':'R4_COMPLETION_HORIZON_V5_FORMAL10_PREREGISTERED','researchOnly':True,'selectionAfterFrozenContract':True,'selection':'First 10 newest choose_files realistic-HFT markets absent as digit-bounded market IDs from all pre-existing P0 text artifacts and absent from recent live-R3 runtime chronology; no outcome, settlement winner, PNL, trigger, or strategy-result screening.','cohort':sel,'recentLiveExcludedCount':len(live),'candidateScanCount':len(need),'usedCandidateCount':len(used),'bytesScanned':scanned,'contract':'r4_completion_horizon_admission_v5_formal_contract.json'}
OUT.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
