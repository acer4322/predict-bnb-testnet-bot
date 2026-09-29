from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np
import replay_r3_formation_dream_sync_v0 as core
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r3_v0/r3_formation_dream_sync_recent20_v0.json'
def main():
    c=sqlite3.connect(core.DB)
    mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][-300:]
    rows=[]
    for mid in mids:
        z=core.build_market(c,mid)
        if z: rows.append(z)
    c.close()
    rows=rows[-20:]
    if not rows: raise SystemExit('no clear formation markets in recent cohort')
    states=['ALLOW_ASYMMETRY','BUILD_WEAK_SIDE','CROSSING_PROTECTION']
    summary={'version':'R3_FORMATION_DREAM_SYNC_RECENT20_V0','mode':'dream-state replay; recent target-selected clear formation markets; descriptive only; no pass threshold','config':core.CFG,'markets':len(rows),'marketIds':[x['marketId'] for x in rows],'meanCheckpointSync':float(np.mean([x['checkpointSync'] for x in rows])),'medianCheckpointSync':float(np.median([x['checkpointSync'] for x in rows])),'meanTransitionSync2cp':float(np.mean([x['transitionSync2cp'] for x in rows])),'medianTeacherSwitches':float(np.median([x['teacherSwitches'] for x in rows])),'medianR3Switches':float(np.median([x['r3Switches'] for x in rows])),'meanTeacherOcc':{},'meanR3Occ':{}}
    for s in states:
        summary['meanTeacherOcc'][s]=float(np.mean([x['teacherOcc'][s] for x in rows]))
        summary['meanR3Occ'][s]=float(np.mean([x['r3Occ'][s] for x in rows]))
    OUT.write_text(json.dumps({'summary':summary,'markets':rows},indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
