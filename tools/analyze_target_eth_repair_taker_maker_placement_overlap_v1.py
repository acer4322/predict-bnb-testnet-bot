from __future__ import annotations
import argparse,bisect,json,math,sqlite3,statistics,importlib.util
from pathlib import Path
from collections import Counter,defaultdict

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('bookbase',HERE/'train_target_eth_first_leg_package_path_stability_teacher_v1.py');bookbase=importlib.util.module_from_spec(spec);spec.loader.exec_module(bookbase)
EPS=1e-9

def q(xs,p):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    z=(len(ys)-1)*p;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
    return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':q(ys,.25),'p75':q(ys,.75),'p90':q(ys,.9)}

def side_best(bids,asks,side):
    z=bookbase.side_book(bids,asks,side)
    return None if z is None else float(z['bid'])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--lifecycle',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_FRESH150_ACTIVE_INTERVENTION_LIFECYCLE_V1.json');ap.add_argument('--placements',default='data/research/r4_v0/p0_provenance_v1/target_eth_fresh150_no18_placement_v2_result.json');ap.add_argument('--book-db',default='data/research/r4_v0/p0_provenance_v1/eth_fresh150_inference_compact_v1.db');ap.add_argument('--output',required=True);a=ap.parse_args()
    life=json.load(open(a.lifecycle,encoding='utf-8'));pl=json.load(open(a.placements,encoding='utf-8'))
    ps=defaultdict(list)
    for r in pl.get('rows',[]):
        if not r.get('highConfidencePlacement'):continue
        if r.get('placementCarrierReadyMs') is None or r.get('firstFillMs') is None:continue
        ps[int(r['marketId'])].append(r)
    repair=[r for r in life.get('rows',[]) if r.get('effect')=='REPAIR_EFFECT' and r.get('latestMakerExpansionAt') is not None]
    bymid=defaultdict(list)
    for r in repair:bymid[int(r['marketId'])].append(r)
    c=sqlite3.connect(f'file:{Path(a.book_db).resolve().as_posix()}?mode=ro',uri=True)
    rows=[]
    try:
        for mid,rr in sorted(bymid.items()):
            lo=min(int(r['firstEventMs']) for r in rr)-1500;hi=max(int(r['firstEventMs']) for r in rr)+10
            states=bookbase.load_states(c,mid,lo,hi,'source_timestamp_ms');ts=[s[0] for s in states]
            for r in rr:
                t=int(r['firstEventMs']);side=str(r['side']);ex=int(r['latestMakerExpansionAt'])
                cand=[p for p in ps.get(mid,[]) if str(p['side'])==side and int(p['placementCarrierReadyMs'])>ex and int(p['placementCarrierReadyMs'])<t]
                filled=[p for p in cand if int(p['firstFillMs'])<t]
                pending=[p for p in cand if int(p['firstFillMs'])>t]
                if filled and pending:state='BOTH_FILLED_AND_PENDING'
                elif pending:state='REPAIR_MAKER_PENDING_AT_TAKER'
                elif filled:state='REPAIR_MAKER_FILLED_BEFORE_TAKER'
                else:state='NO_REPAIR_MAKER_PLACEMENT'
                latest=max(cand,key=lambda p:int(p['placementCarrierReadyMs'])) if cand else None
                latest_f=max(filled,key=lambda p:int(p['firstFillMs'])) if filled else None
                latest_p=max(pending,key=lambda p:int(p['placementCarrierReadyMs'])) if pending else None
                idx=bisect.bisect_right(ts,t-1)-1;bb=None;behind=None
                if idx>=0 and latest_p is not None:
                    _,bids,asks=states[idx];bb=side_best(bids,asks,side)
                    if bb is not None:behind=max(0.0,(bb-float(latest_p['targetPrice']))/.01)
                rows.append({'marketId':mid,'takerIndex':int(r['takerIndex']),'takerAt':t,'takerSide':side,'takerShares':float(r['shares']),'takerAvgPrice':float(r['avgPrice']),'preAbsNet':float(r['preAbsNet']),'msSinceLatestMakerExpansion':r.get('msSinceLatestMakerExpansion'),'state':state,'repairMakerCandidates':len(cand),'filledBeforeTaker':len(filled),'pendingPlacementOverlap':len(pending),'latestRepairMakerPlacementAgeMs':None if latest is None else t-int(latest['placementCarrierReadyMs']),'latestRepairMakerFillAgeMs':None if latest_f is None else t-int(latest_f['firstFillMs']),'pendingPlacementAgeMs':None if latest_p is None else t-int(latest_p['placementCarrierReadyMs']),'pendingFirstFillLeadAfterTakerMs':None if latest_p is None else int(latest_p['firstFillMs'])-t,'pendingTargetPrice':None if latest_p is None else float(latest_p['targetPrice']),'repairBestBidAtTaker':bb,'pendingBehindBestTicks':behind})
    finally:c.close()
    cnt=Counter(r['state'] for r in rows);n=len(rows);pend=[r for r in rows if r['pendingPlacementOverlap']>0];filled=[r for r in rows if r['filledBeforeTaker']>0]
    out={'version':'TARGET_ETH_REPAIR_TAKER_MAKER_PLACEMENT_OVERLAP_V1','researchOnly':True,'coverage':{'repairTakerParentsWithLatestExpansion':n,'markets':len({r['marketId'] for r in rows}),'highConfidencePlacementParents':sum(len(v) for v in ps.values())},'stateCounts':dict(cnt),'stateRates':{k:v/n for k,v in cnt.items()} if n else {},'summary':{'anyRepairMakerPlacementRate':sum(r['repairMakerCandidates']>0 for r in rows)/n if n else None,'filledRepairMakerBeforeTakerRate':len(filled)/n if n else None,'pendingPlacementOverlapRate':len(pend)/n if n else None,'latestPlacementAgeMs':stats([r['latestRepairMakerPlacementAgeMs'] for r in rows]),'latestFilledRepairAgeMs':stats([r['latestRepairMakerFillAgeMs'] for r in filled]),'pendingPlacementAgeMs':stats([r['pendingPlacementAgeMs'] for r in pend]),'pendingFirstFillLeadAfterTakerMs':stats([r['pendingFirstFillLeadAfterTakerMs'] for r in pend]),'pendingBehindBestTicks':stats([r['pendingBehindBestTicks'] for r in pend]),'pendingAtOrNear1TickRate':sum((r['pendingBehindBestTicks'] is not None and r['pendingBehindBestTicks']<=1+EPS) for r in pend)/sum(r['pendingBehindBestTicks'] is not None for r in pend) if any(r['pendingBehindBestTicks'] is not None for r in pend) else None},'rows':rows,'boundary':['Fresh ETH REPAIR_EFFECT Taker parents only.','High-confidence no18 Maker placement reconstruction only.','Placement-before/Taker-before-first-fill is overlap evidence, not proof the private order was still live because cancels are unobserved.','No winner/PnL.','No runtime threshold/authority.']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:out[k] for k in ('coverage','stateCounts','stateRates','summary')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
