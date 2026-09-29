from pathlib import Path
import json,re,sys,datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_adaptive_cycle_hft_formal20_cohort_v1.json'
IDS=P/'r4_adaptive_cycle_hft_formal20_ids_v1.json'
pat=re.compile(r'(?<!\d)(\d{7})(?!\d)')
consumed=set(); scanned=0
for p in P.rglob('*'):
    if not p.is_file() or p in {OUT,IDS}:continue
    if p.suffix.lower() not in {'.json','.md','.txt','.csv'}:continue
    try:s=p.read_text(encoding='utf-8',errors='ignore')
    except Exception:continue
    scanned+=1
    for m in pat.findall(s):consumed.add(int(m))

def sealed(d):
    ts=[]
    for r in (d.get('decisionRows') or []):
        x=int(r.get('decisionMs') or 0)
        if x:ts.append(x)
    if not ts:
        for o in (d.get('orderMeta') or {}).values():
            x=int(o.get('placedAtMs') or 0)
            if x:ts.append(x)
    if not ts:return False
    dt=datetime.datetime.fromtimestamp(min(ts)/1000,datetime.timezone.utc).astimezone(ZoneInfo('Asia/Taipei'))
    return dt.date()==datetime.date(2026,8,16)

sel=[];candidates=0
for d in v2.choose_files(1200):
    mid=int(d['marketId'])
    if mid in consumed or sealed(d):continue
    candidates+=1
    sel.append(mid)
    if len(sel)>=20:break
rep={'version':'R4_ADAPTIVE_CYCLE_HFT_FORMAL20_COHORT_V1','status':'FROZEN_BEFORE_ANY_FORMAL_SCORING','contract':'r4_adaptive_cycle_hft_formal_v1_contract.json','selection':'First 20 newest eligible realistic-HFT R2_RESIDUAL markets absent as exact 7-digit IDs from all pre-existing P0 provenance text artifacts at selection time; Execution Tape required by choose_files; 2026-08-16 Asia/Taipei sealed; no winner/settlement/PNL/trigger/model-score/action-label screening.','cohort':sel,'count':len(sel),'consumedIdCount':len(consumed),'filesScanned':scanned,'eligibleVisited':candidates,'guards':['outcome blind selection','realistic HFT source only','no live R3/8781','formal contract frozen before selection']}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');IDS.write_text(json.dumps(sel,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if len(sel)!=20:raise SystemExit(2)
