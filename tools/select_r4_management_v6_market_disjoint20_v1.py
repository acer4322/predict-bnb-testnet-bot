from pathlib import Path
import json,re,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
from tools import test_r4_preposition_responsibility_prune_v2 as v2
consumed=set(); pat=re.compile(r'(?<!\d)(\d{6,8})(?!\d)')
for p in P.glob('*'):
    if not p.is_file() or p.name.startswith('r4_management_v6_market_disjoint20'): continue
    try:s=p.read_text(encoding='utf-8',errors='ignore')
    except Exception:continue
    for m in pat.findall(s):
        x=int(m)
        if 1000000<=x<=9999999: consumed.add(x)
elig=[]
for d in v2.choose_files(500):
    mid=int(d['marketId'])
    if mid in consumed: continue
    elig.append(mid)
    if len(elig)>=20: break
out={'version':'R4_MANAGEMENT_V6_MARKET_DISJOINT20_PREREGISTERED_V1','researchOnly':True,'createdDate':'2026-08-29','selection':'First 20 newest choose_files realistic-HFT markets whose marketId does not appear in any pre-existing P0 provenance artifact text at freeze time; no outcome/trigger screening.','cohort':elig,'consumedIdCount':len(consumed),'guards':['strict-past runtime features only','realistic HFT/no dream fill','2026-08-16 SEALED excluded','no live 8781/R3/R3.1 changes','V6 factorized weak/dominant portfolio event representation frozen before scoring','one-shot validation; no post-label threshold tuning']}
(P/'r4_management_v6_market_disjoint20_preregistered_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))