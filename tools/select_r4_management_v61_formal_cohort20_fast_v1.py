from __future__ import annotations
import json,re,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; OUT=P/'r4_management_v61_formal_cohort20_preregistered_v1.json'
contract=json.loads((P/'r4_management_simulator_v61_formal_validation_contract.json').read_text(encoding='utf-8'))
assert contract['status']=='FROZEN_BEFORE_NEXT_VALIDATION_COHORT_SELECTION'
cands=[int(d['marketId']) for d in v2.choose_files(200)]
# Exclude the already-scored V6 one-shot20 explicitly.
prev=set(json.loads((P/'r4_management_v6_market_disjoint20_ids.json').read_text(encoding='utf-8')))
cands=[x for x in cands if x not in prev]
need=set(cands[:100]); pats={str(x).encode():x for x in need}; used=set(); scanned=0
# Single-pass binary scan with digit-boundary check; avoids accidental float-substring matches.
for p in P.iterdir():
    if not p.is_file() or p in {OUT,Path(__file__)}: continue
    if p.suffix.lower() not in {'.json','.md','.txt','.csv'}: continue
    try:
        data=p.read_bytes()
    except Exception: continue
    scanned += len(data)
    for b,x in list(pats.items()):
        start=0
        while True:
            j=data.find(b,start)
            if j<0: break
            left=data[j-1:j] if j>0 else b''; right=data[j+len(b):j+len(b)+1]
            if (not left or not (48<=left[0]<=57)) and (not right or not (48<=right[0]<=57)):
                used.add(x); break
            start=j+1
    if len(need-used)>=20 and len(used)>=80: pass
sel=[x for x in cands if x not in used][:20]
if len(sel)<20: raise RuntimeError(f'only {len(sel)} unused candidates')
out={'version':'R4_MANAGEMENT_V6_1_FORMAL_COHORT20_PREREGISTERED_V1','researchOnly':True,'selectionAfterFrozenContract':True,'selection':'First 20 newest choose_files realistic-HFT markets absent as digit-bounded market IDs from all pre-existing P0 text artifacts, after excluding the consumed V6 one-shot20; no outcome/trigger screening.','cohort':sel,'candidateScanCount':len(need),'usedCandidateCount':len(used),'bytesScanned':scanned,'contract':'r4_management_simulator_v61_formal_validation_contract.json','guards':contract['guards']}
OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
