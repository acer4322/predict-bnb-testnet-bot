from __future__ import annotations
import argparse,json
from pathlib import Path
EPS=1e-9
FIXED=[1916847,1916869]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    src=json.load(open(a.input,encoding='utf-8'))
    rows=[]
    raw=src.get('rows') or src.get('markets') or []
    # Expected anatomy result schema: one row per market with anatomy list.
    if not raw and 'anatomy' in src:
        raw=[src]
    for mr in raw:
        mid=int(mr.get('marketId'))
        if mid not in FIXED: continue
        events=mr.get('anatomy') or mr.get('events') or []
        for ev in events:
            rec=ev.get('recoverability') or ev
            q=rec.get('futureRequiredQty'); room=rec.get('repairRoomAfterOwned'); p=rec.get('admissibleFutureRepairPrice'); projected=rec.get('projectedFloorAfterOwnedRepair')
            if None in (q,room,p,projected): continue
            q=float(q);room=max(0.0,float(room));p=float(p);projected=float(projected)
            repair=min(q,room);overflow=max(0.0,q-room)
            floor_after=projected + repair*(1.0-p) - overflow*p
            if floor_after>=-EPS:
                cls='DIRECT_RECOVERABLE_AFTER_COMPOSITE'
            elif overflow>EPS:
                cls='RECURSIVE_OVERFLOW_REQUIRED'
            else:
                cls='UNRECOVERABLE'
            rows.append({'marketId':mid,'pExpand':ev.get('pExpand'),'legacyReason':rec.get('reason'),'carrierQty':q,'repairRoom':room,'repairPaidQty':repair,'overflowQty':overflow,'admissibleRepairPrice':p,'projectedFloorBeforeFutureCarrier':projected,'floorAfterPhysicalCarrier':floor_after,'classification':cls})
    if sorted(set(r['marketId'] for r in rows))!=FIXED: raise RuntimeError(f'fixed market coverage mismatch {rows}')
    direct=sum(r['classification']=='DIRECT_RECOVERABLE_AFTER_COMPOSITE' for r in rows)
    recursive=sum(r['classification']=='RECURSIVE_OVERFLOW_REQUIRED' for r in rows)
    out={'version':'ETH_V83_ALLOCATION_AWARE_RECOVERABILITY_SHADOW_FRESH2','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'fixedMarkets':FIXED,'rows':rows,'summary':{'directRecoverable':direct,'recursiveOverflowRequired':recursive,'legacyRejectedReclassified':sum(r['legacyReason']=='FUTURE_REPAIR_QTY_EXCEEDS_ROOM' and r['classification']!='UNRECOVERABLE' for r in rows)},'decision':'PREREGISTER_ONE_MARKET_DIRECT_BEHAVIOR_SMOKE' if direct>=1 else 'KEEP_SHADOW_DIAGNOSE_RECURSION','boundary':['artifact-only deterministic counterfactual','no runtime mutation','AllocationLedger V2 semantics','no threshold/qty/price tuning','no 8781','no dream fill']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary'],'rows':rows},ensure_ascii=False))
if __name__=='__main__':main()
