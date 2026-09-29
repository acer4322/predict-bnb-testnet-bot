from __future__ import annotations
import json, math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_repair_exact_seam_teacher_ledger_v1.json'

def finite(x):
    try:return math.isfinite(float(x))
    except:return False

def quality(r):
    # Prefer exact branch rows with full candidate + baseline/cf + deltas.
    s=0
    if r.get('exactBranchApplied') is True:s+=100
    for k in ('candidate','baseline','counterfactual','delta'):
        if isinstance(r.get(k),dict):s+=10+len(r[k])
    return s

sources=[]; best={}
for f in sorted(P.glob('*.json')):
    try:d=json.loads(f.read_text(encoding='utf-8'))
    except Exception:continue
    rows=[]
    if isinstance(d,dict) and isinstance(d.get('rows'),list): rows=d['rows']
    elif isinstance(d,dict) and d.get('marketId') is not None and ('branchClass' in d or 'exactBranchApplied' in d): rows=[d]
    for r in rows:
        if not isinstance(r,dict):continue
        if r.get('exactBranchApplied') is not True:continue
        mid=r.get('marketId')
        if mid is None:continue
        mid=int(mid)
        q=quality(r)
        prev=best.get(mid)
        if prev is None or q>prev[0]:best[mid]=(q,f.name,r)

ledger=[]
for mid,(q,src,r) in sorted(best.items()):
    b=r.get('baseline') or {}; c=r.get('counterfactual') or {}; d=r.get('delta') or {}; cand=r.get('candidate') or {}
    bm=float(b.get('makerFilledShares') or 0); cm=float(c.get('makerFilledShares') or 0)
    bt=float(b.get('takerFilledShares') or 0); ct=float(c.get('takerFilledShares') or 0)
    maker_ret=(cm/bm) if bm>1e-9 else (1.0 if cm<=1e-9 else None)
    total_b=bm+bt; total_c=cm+ct
    total_ret=(total_c/total_b) if total_b>1e-9 else (1.0 if total_c<=1e-9 else None)
    floor_delta=d.get('finalFloor')
    if floor_delta is None and finite(c.get('finalFloor')) and finite(b.get('finalFloor')): floor_delta=float(c['finalFloor'])-float(b['finalFloor'])
    row={
      'marketId':mid,'sourceArtifact':src,'branchClass':r.get('branchClass'),'exactSeam':True,
      'secondsLeft':((cand.get('public') or {}).get('secondsLeft')),'phase':cand.get('phase'),'activeMakerOrders':cand.get('activeMakerOrders'),
      'preFloor':((cand.get('portfolio') or {}).get('worst_case_floor')),
      'floorDelta':floor_delta,'absNetDelta':d.get('finalAbsNet'),'coverageDelta':d.get('finalCoverage'),
      'makerFilledSharesBaseline':bm,'makerFilledSharesCF':cm,'makerParticipationRetention':maker_ret,
      'takerFilledSharesBaseline':bt,'takerFilledSharesCF':ct,'totalFillRetention':total_ret,
      'makerCostDelta':d.get('makerCostUsdt'),'takerCostDelta':d.get('takerCostUsdt'),'takerFeesDelta':d.get('takerFeesUsdt'),
      'participationCollapse': bool(maker_ret is not None and maker_ret < 0.50),
      'lateNoNewExposureAuthority': bool(finite(((cand.get('public') or {}).get('secondsLeft'))) and float((cand.get('public') or {}).get('secondsLeft'))<=180),
      'promotionEvidence':False
    }
    ledger.append(row)
counts=Counter(x['branchClass'] for x in ledger)
valid=[x for x in ledger if finite(x.get('floorDelta'))]
ret=[x['makerParticipationRetention'] for x in valid if finite(x.get('makerParticipationRetention'))]
rep={
 'version':'R4_REPAIR_EXACT_SEAM_TEACHER_LEDGER_V1','date':'2026-08-30','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
 'purpose':'canonical deduplicated exact-seam REPAIR_NOW vs WAIT causal teacher ledger with economic and participation-retention attribution',
 'marketCount':len(ledger),'counts':dict(counts),
 'summary':{
   'meanFloorDelta':sum(float(x['floorDelta']) for x in valid)/len(valid) if valid else None,
   'meanMakerParticipationRetention':sum(float(x) for x in ret)/len(ret) if ret else None,
   'participationCollapseCases':sum(bool(x['participationCollapse']) for x in ledger),
   'lateTeacherOnlyCases':sum(bool(x['lateNoNewExposureAuthority']) for x in ledger)
 },
 'rows':ledger,
 'guards':['strict-past candidate state only','realistic-HFT/no dream fill','exact post-HFT-fill/pre-decision seam','consumed development evidence is not fresh promotion evidence','Protection deterministic unchanged','live R3/R3.1/8781 untouched','learned modules have no action authority','<=180s rows teacher/scoring only','safety improvement cannot count as success if achieved through participation collapse']
}
OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'marketCount':rep['marketCount'],'counts':rep['counts'],'summary':rep['summary']},ensure_ascii=False))
