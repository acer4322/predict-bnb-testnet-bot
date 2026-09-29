from __future__ import annotations
import json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; P=ROOT/'data/research/r4_v0/p0_provenance_v1'
# Availability-qualified pool from indexed DB/tape intersection only. No outcome/action screening.
pool=[1783154,1783150,1783080,1783072,1783069,1782959,1782946,1782926,1782821,1782807,1782804,1782803,1782753,1782750,1782745,1782495,1782490,1782487,1782477,1782469,1782465,1782453,1782437,1782273,1782263,1782260,1782254,1782218,1782215,1782208,1782044,1781777,1781776,1781742,1780007,1779994,1779971,1779842,1779841,1779717,1779716,1779701,1779692,1779617,1779290,1779284,1778006,1778003,1777990,1777969,1777851,1777808,1777805,1777804,1777725,1777718,1777712,1777660,1777638,1777602,1777592,1777470,1777469,1777459,1777368,1777356,1777345,1777331,1777269,1777259,1777237,1777236,1777219,1777186,1777180,1777170,1777167,1777166,1777057,1777046,1777044,1777001,1776960,1776929,1776714,1776700,1776698,1776689,1776642,1776637,1776624,1776056,1775850,1775840,1775833,1775797,1775783,1775774,1775773,1775634,1775624,1775622,1775546,1775527,1775485,1775470,1775467,1775465,1775419,1775341,1775340,1775329,1775286,1775228,1775217,1775212,1775012,1774997,1774989,1774983]
pat=re.compile(rb'(?<![0-9])(?:'+b'|'.join(str(x).encode() for x in pool)+rb')(?![0-9])')
used=set(); scanned=0; files=0
for p in P.iterdir():
    if not p.is_file() or p.suffix.lower() not in {'.json','.md','.txt','.csv'}: continue
    try:data=p.read_bytes()
    except Exception: continue
    files+=1; scanned+=len(data)
    for m in pat.finditer(data): used.add(int(m.group(0)))
sel=[x for x in pool if x not in used][:20]
if len(sel)<20: raise RuntimeError(f'only {len(sel)} unused markets found')
out={'version':'R4_COMPLETION_HORIZON_V5_EXTENSION20_COHORT_PREREGISTERED','status':'FROZEN_BEFORE_SCORING','researchOnly':True,'contract':'r4_completion_horizon_v5_extension20_contract.json','selection':'first 20 availability-qualified pool markets absent as exact digit-bounded IDs from all pre-existing P0 text artifacts; no outcome/action screening','cohort':sel,'poolSize':len(pool),'usedInP0':len(used),'filesScanned':files,'bytesScanned':scanned}
(P/'r4_completion_horizon_v5_extension20_cohort_preregistered.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
