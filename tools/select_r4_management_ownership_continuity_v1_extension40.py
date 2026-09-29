from __future__ import annotations
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'r4_management_ownership_continuity_v1_extension40_cohort_preregistered.json';PRE=P/'r4_management_ownership_continuity_v1_support_extension40_preregistered.json'
pre=json.loads(PRE.read_text(encoding='utf-8'));assert pre['status']=='FROZEN_BEFORE_EXTENSION_COHORT_SELECTION'
cands=[int(d['marketId']) for d in v2.choose_files(1200)];candset=set(cands);used=set();scanned=0;pat=re.compile(rb'(?<!\d)(\d{7})(?!\d)')
for p in P.iterdir():
 if not p.is_file() or p==OUT or p.suffix.lower() not in {'.json','.md','.txt','.csv'}:continue
 try:data=p.read_bytes()
 except Exception:continue
 scanned+=len(data)
 for m in pat.finditer(data):
  x=int(m.group(1))
  if x in candset:used.add(x)
sel=[x for x in cands if x not in used][:40]
if len(sel)<40:raise RuntimeError(f'only {len(sel)} unused candidates from {len(cands)}')
out={'version':'R4_MANAGEMENT_OWNERSHIP_CONTINUITY_V1_EXTENSION40_COHORT_PREREGISTERED','researchOnly':True,'selectionAfterExtensionFreeze':True,'selection':'Next 40 newest choose_files realistic-HFT market IDs absent as exact 7-digit market IDs from all pre-existing P0 text artifacts; no trigger/outcome screening.','cohort':sel,'candidateCount':len(cands),'usedCandidateCount':len(used),'bytesScanned':scanned,'extensionContract':PRE.name,'guards':pre['guards']};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
