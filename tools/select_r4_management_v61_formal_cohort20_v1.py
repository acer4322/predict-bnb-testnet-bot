from __future__ import annotations
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_management_v61_formal_cohort20_preregistered_v1.json'
# Contract must exist before selection.
contract=json.loads((P/'r4_management_simulator_v61_formal_validation_contract.json').read_text(encoding='utf-8'))
assert contract.get('status')=='FROZEN_BEFORE_NEXT_VALIDATION_COHORT_SELECTION'
# Conservative exclusion: every integer-looking market id already referenced by any existing P0 artifact before this selector writes output.
used=set()
pat=re.compile(r'(?<!\d)(1\d{6})(?!\d)')
for p in P.iterdir():
    if not p.is_file() or p==OUT: continue
    try: txt=p.read_text(encoding='utf-8',errors='ignore')
    except Exception: continue
    for m in pat.finditer(txt): used.add(int(m.group(1)))
sel=[]
for d in v2.choose_files(2000):
    mid=int(d['marketId'])
    if mid in used: continue
    sel.append(mid)
    if len(sel)>=20: break
if len(sel)<20: raise RuntimeError(f'only {len(sel)} unreferenced markets found')
out={'version':'R4_MANAGEMENT_V6_1_FORMAL_COHORT20_PREREGISTERED_V1','researchOnly':True,'selectionAfterFrozenContract':True,'selection':'First 20 newest choose_files realistic-HFT markets whose marketId is absent from all pre-existing P0 provenance artifact text; market-id chronology only; no outcome/trigger screening.','cohort':sel,'consumedIdCountAtFreeze':len(used),'contract':'r4_management_simulator_v61_formal_validation_contract.json','guards':contract['guards']}
OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out,indent=2))
