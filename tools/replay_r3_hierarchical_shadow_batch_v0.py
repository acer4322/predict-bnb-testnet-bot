from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path
from collections import Counter
import numpy as np
import replay_r3_hierarchical_shadow_v0 as base

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'data'/'research'/'r3_v0'/'shadow_batches_v0'
OUTDIR.mkdir(parents=True,exist_ok=True)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start',type=int,required=True); ap.add_argument('--end',type=int,required=True); args=ap.parse_args()
    c=sqlite3.connect(base.DB)
    mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
    test=mids[int(.8*len(mids)):]
    chunk=test[args.start:args.end]
    out=[]
    for m in chunk:
        z=base.replay_market(c,m)
        if z: out.append(z)
    c.close()
    acts=[a for m in out for a in m['actions']]
    cnt=Counter(a['action'] for a in acts); actual=Counter(a['actualNext'] for a in acts)
    def prec(name,label):
        xs=[a for a in acts if a['action']==name]
        return sum(a['actualNext']==label for a in xs)/len(xs) if xs else None
    expand=[a for a in acts if a['action']=='EXPAND']
    rep={'version':'R3_HIERARCHICAL_SHADOW_BATCH_V0','start':args.start,'end':args.end,'requestedMarkets':len(chunk),'markets':len(out),'checkpoints':len(acts),'actionCounts':dict(cnt),'actualNextCounts':dict(actual),'metrics':{'expandPrecisionVsNextSide':prec('EXPAND','EXPAND'),'holdPermissionPrecisionVsNextSide':prec('HOLD','EXPAND'),'protectPrecisionVsNextSideRepair':prec('PROTECT','REPAIR'),'repairPrecisionVsNextSideRepair':prec('REPAIR','REPAIR'),'expandFloorGe0Rate':sum(a['floor']>=0 for a in expand)/len(expand) if expand else None,'expandMedianFloor':float(np.median([a['floor'] for a in expand])) if expand else None,'expandMedianUpside':float(np.median([a['upside'] for a in expand])) if expand else None}}
    outp=OUTDIR/f'batch_{args.start:04d}_{args.end:04d}.json'; outp.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'out':str(outp),'summary':rep},indent=2))
if __name__=='__main__': main()
