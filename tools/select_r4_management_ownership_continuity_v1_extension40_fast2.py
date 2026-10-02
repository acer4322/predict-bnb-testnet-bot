from __future__ import annotations
import json,lzma,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';TAPE=ROOT/'data/execution_tape_v1/markets';OUT=P/'r4_management_ownership_continuity_v1_extension40_cohort_preregistered.json';PRE=P/'r4_management_ownership_continuity_v1_support_extension40_preregistered.json';FROZEN={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}
pre=json.loads(PRE.read_text(encoding='utf-8'));assert pre['status']=='FROZEN_BEFORE_EXTENSION_COHORT_SELECTION'
# Build exact 7-digit used-id set once from P0 text artifacts.
used=set();scanned=0;pat=re.compile(rb'(?<!\d)(\d{7})(?!\d)')
for p in P.iterdir():
 if not p.is_file() or p==OUT or p.suffix.lower() not in {'.json','.md','.txt','.csv'}:continue
 try:data=p.read_bytes()
 except Exception:continue
 scanned+=len(data);used.update(int(m.group(1)) for m in pat.finditer(data))
files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True);seen=set();sel=[];opened=0
for p in files:
 if len(sel)>=40:break
 try:
  with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
 except Exception:continue
 opened+=1;mid=int(d.get('marketId') or 0);student=str(d.get('student') or '')
 if not mid or mid in seen or mid in FROZEN or 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}):continue
 if not (TAPE/f'{mid}.json.xz').exists():continue
 seen.add(mid)
 if mid in used:continue
 sel.append(mid)
if len(sel)<40:raise RuntimeError(f'only {len(sel)} unused eligible markets after opening {opened}')
out={'version':'R4_MANAGEMENT_OWNERSHIP_CONTINUITY_V1_EXTENSION40_COHORT_PREREGISTERED','researchOnly':True,'selectionAfterExtensionFreeze':True,'selection':'First 40 newest eligible markets under the exact choose_files R2_RESIDUAL/orderMeta/tape/FROZEN rules whose exact 7-digit market ID is absent from all pre-existing P0 text artifacts; stopped only after fixed 40, no outcome/trigger screening.','cohort':sel,'sourceFilesOpened':opened,'usedIdCount':len(used),'bytesScanned':scanned,'extensionContract':PRE.name,'guards':pre['guards']};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
