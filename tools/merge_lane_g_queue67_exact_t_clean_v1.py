from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
NEW=ROOT/'data/research/lan_worker_returns/lane-g-queue67-exact-t-clean-20260907-v1'
OLD=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/LANE_G_QUEUE67_EXACT_T_CLEAN_MERGED_V1_20260907.json'

def loadp(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

rows=[]; gates=[]
for p in sorted(NEW.glob('shard*.json')):
    d=loadp(p); rows.extend(d['rows']); gates.append(d.get('gates',{}))
# unique canonical key is market,key,target decision t
seen=set(); dup=[]
for r in rows:
    k=(int(r['marketId']),str(r['targetKey']),int(r.get('targetT') or r.get('baselineCancelT')))
    if k in seen: dup.append(k)
    seen.add(k)
rows=sorted(rows,key=lambda r:(int(r.get('targetT') or r.get('baselineCancelT')),int(r['marketId']),str(r['targetKey'])))

oldrows=[]
for p in sorted(OLD.glob('lane-g-qov-decision-forks-shard*-20260907-v1/result.json')):
    oldrows.extend(loadp(p).get('rows',[]))
old_by={(int(r['marketId']),str(r['targetKey']),int(r['baselineCancelT'])):r for r in oldrows}
changes=[]
for r in rows:
    k=(int(r['marketId']),str(r['targetKey']),int(r.get('targetT') or r.get('baselineCancelT')))
    o=old_by.get(k)
    if not o: continue
    old_trig=int((o.get('forkValue') or {}).get('triggerT') or 0)
    new_trig=int((r.get('forkValue') or {}).get('triggerT') or 0)
    old_p=float((o.get('terminalDelta') or {}).get('pnl') or 0.0)
    new_p=float((r.get('terminalDelta') or {}).get('pnl') or 0.0)
    changes.append({'marketId':k[0],'key':k[1],'targetT':k[2],'oldTriggerT':old_trig,'newTriggerT':new_trig,'oldTriggerDeltaMs':old_trig-k[2], 'newTriggerDeltaMs':new_trig-k[2], 'oldPnlDelta':old_p,'newPnlDelta':new_p,'pnlDeltaChange':new_p-old_p})
mis=[x for x in changes if x['oldTriggerDeltaMs']!=0]
changed=[x for x in mis if abs(x['pnlDeltaChange'])>1e-12]
vals=[float((r.get('terminalDelta') or {}).get('pnl') or 0.0) for r in rows]
out={
 'version':'LANE_G_QUEUE67_EXACT_T_CLEAN_MERGED_V1_20260907',
 'researchOnly':True,'runtimeAuthority':False,
 'rows':rows,
 'summary':{
   'rows':len(rows),'markets':len({int(r['marketId']) for r in rows}),
   'uniqueKeys':len(seen),'duplicateKeys':len(dup),
   'allExactTriggerTimes':all(bool(r.get('exactTriggerTime') if 'exactTriggerTime' in r else r.get('triggerTimeExact')) for r in rows),
   'allTargetsExercised':all(bool(r.get('exercised')) for r in rows),
   'allTriggerStatesEqual':all(bool((r.get('forkValue') or {}).get('triggerEqual')) for r in rows),
   'allCorrect':all(bool(r.get('correct')) for r in rows),
   'terminalPnlDeltaSum':sum(vals),'terminalPnlPositive':sum(v>1e-12 for v in vals),'terminalPnlNegative':sum(v<-1e-12 for v in vals),'terminalPnlZero':sum(abs(v)<=1e-12 for v in vals),
   'oldMisalignedCount':len(mis),'oldMisalignedMarkets':len({x['marketId'] for x in mis}),
   'misalignedWithChangedTerminalPnl':len(changed)
 },
 'oldVsNewMisaligned':mis,
 'boundary':['canonical queue fork corpus uses (marketId,targetKey,targetT) exact receipt binding','old key-only 67-fork corpus retained only as prior art','consumed 24 cohort only','no fresh/no 8781']
}
OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'output':str(OUT.relative_to(ROOT)),'summary':out['summary'],'changedMisaligned':changed},ensure_ascii=False,indent=2))
