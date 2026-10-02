from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'r4_management_ownership_continuity_v1_untouched20_preregistered.json';CON=P/'r4_management_ownership_continuity_v1_validation_contract.json'
contract=json.loads(CON.read_text(encoding='utf-8'));assert contract['status']=='FROZEN_BEFORE_UNTOUCHED_COHORT_SELECTION'
cands=[int(d['marketId']) for d in v2.choose_files(400)];need=set(cands[:250]);pats={str(x).encode():x for x in need};used=set();scanned=0
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
   left=data[j-1:j] if j>0 else b'';right=data[j+len(b):j+len(b)+1]
   if (not left or not (48<=left[0]<=57)) and (not right or not (48<=right[0]<=57)):
    used.add(x);break
   start=j+1
sel=[x for x in cands if x not in used][:20]
if len(sel)<20:raise RuntimeError(f'only {len(sel)} unused candidates among scanned range')
out={'version':'R4_MANAGEMENT_OWNERSHIP_CONTINUITY_V1_UNTOUCHED20_PREREGISTERED','researchOnly':True,'selectionAfterFrozenContract':True,'selection':'First 20 newest choose_files realistic-HFT market IDs absent as digit-bounded IDs from all pre-existing P0 text artifacts at selection time; no outcome/trigger screening.','cohort':sel,'candidateScanCount':len(need),'usedCandidateCount':len(used),'bytesScanned':scanned,'contract':CON.name,'guards':contract['guards']};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
