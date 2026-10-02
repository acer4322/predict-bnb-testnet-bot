from pathlib import Path
import json,re,sys,datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_github_repair_regime_v3_fresh_hft_cohort_v1.json'
IDS=P/'r4_github_repair_regime_v3_fresh_hft_ids_v1.json'
# Build only the recent HFT candidates we may need, then scan provenance for those exact IDs.
cands=v2.choose_files(220)
ordered=[]; by_id={}
for d in cands:
    mid=int(d.get('marketId') or 0)
    if mid and mid not in by_id:
        ordered.append(mid); by_id[mid]=d
ordered=ordered[:160]
pat=re.compile(rb'(?<!\d)(' + b'|'.join(str(x).encode() for x in ordered) + rb')(?!\d)')
consumed=set(); scanned=0; scanned_bytes=0
for p in P.rglob('*'):
    if not p.is_file() or p in {OUT,IDS}: continue
    low=[x.lower() for x in p.parts]
    # Worker bundles are copied subsets of source provenance; source artifacts remain in P0 and are scanned.
    if any('worker_bundle' in x for x in low): continue
    if p.suffix.lower() not in {'.json','.md','.txt','.csv'}: continue
    try:
        data=p.read_bytes()
    except Exception:
        continue
    scanned+=1; scanned_bytes+=len(data)
    for m in pat.findall(data): consumed.add(int(m))

def sealed(d):
    ts=[]
    for r in (d.get('decisionRows') or []):
        x=int(r.get('decisionMs') or 0)
        if x: ts.append(x)
    if not ts:
        for o in (d.get('orderMeta') or {}).values():
            x=int(o.get('placedAtMs') or 0)
            if x: ts.append(x)
    if not ts:return False
    dt=datetime.datetime.fromtimestamp(min(ts)/1000,datetime.timezone.utc).astimezone(ZoneInfo('Asia/Taipei'))
    return dt.date()==datetime.date(2026,8,16)
sel=[]; visited=[]
for mid in ordered:
    d=by_id[mid]
    reason='ELIGIBLE'
    if mid in consumed: reason='CONSUMED'
    elif sealed(d): reason='SEALED_2026_08_16'
    visited.append({'marketId':mid,'reason':reason})
    if reason=='ELIGIBLE':
        sel.append(mid)
        if len(sel)>=20: break
rep={
 'version':'R4_GITHUB_REPAIR_REGIME_V3_FRESH_HFT_COHORT_V1',
 'status':'FROZEN_BEFORE_ANY_FRESH_SCORING',
 'contract':'r4_github_repair_regime_v3_fresh_hft_contract.json',
 'selection':'First 20 newest eligible realistic-HFT R2_RESIDUAL markets absent as exact IDs from pre-existing source P0 provenance; worker-bundle copies ignored; no outcome/model/action-label screening.',
 'cohort':sel,'count':len(sel),'candidateIdsConsidered':len(ordered),'candidateConsumedMatches':len(consumed),'sourceFilesScanned':scanned,'sourceBytesScanned':scanned_bytes,'visitedPrefix':visited,
 'guards':['outcome blind selection','realistic HFT source only','no live R3/8781','contract frozen before selection']
}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); IDS.write_text(json.dumps(sel,indent=2),encoding='utf-8')
print(json.dumps(rep,indent=2))
if len(sel)!=20: raise SystemExit(2)
