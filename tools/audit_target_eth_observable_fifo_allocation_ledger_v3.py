from __future__ import annotations
import argparse,json,sqlite3,statistics
from collections import defaultdict,deque,Counter
from pathlib import Path
EPS=1e-9

def role(side,up,dn):
    if up+dn<=EPS or abs(up-dn)<=EPS:return 0
    weak='UP' if up<dn else 'DOWN'
    return 1 if side==weak else -1

def quantiles(xs):
    if not xs:return {'n':0,'mean':None,'median':None,'p10':None,'p90':None}
    ys=sorted(float(x) for x in xs);n=len(ys)
    return {'n':n,'mean':sum(ys)/n,'median':statistics.median(ys),'p10':ys[max(0,int(.1*n)-1)],'p90':ys[min(n-1,int(.9*n))]}

def order_hash_from_parent_id(pid):
    s=str(pid);parts=s.split(':')
    return parts[2] if len(parts)>=5 else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--snapshot-db',required=True);ap.add_argument('--raw-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    # Keep the exact V2 snapshot cohort/parent aggregates, but recover true within-ms ordering from raw event ids.
    con=sqlite3.connect(a.snapshot_db);con.row_factory=sqlite3.Row
    raw=list(con.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH'"));con.close()
    rc=sqlite3.connect(a.raw_db);rc.row_factory=sqlite3.Row
    event_groups=list(rc.execute("select order_hash,min(id) first_raw_id,min(event_ms) first_raw_ms,count(*) legs from wallet_shadow_target_events where asset='ETH' group by order_hash"));rc.close()
    first_raw={str(r['order_hash']):(int(r['first_raw_id']),int(r['first_raw_ms']),int(r['legs'])) for r in event_groups}
    by=defaultdict(list);unmapped=[];raw_ms_mismatch=0
    for r in raw:
        d=dict(r);oh=order_hash_from_parent_id(d['parent_id']);meta=first_raw.get(str(oh))
        if meta is None:
            unmapped.append(str(d['parent_id']));continue
        d['raw_first_id']=meta[0];d['raw_first_ms']=meta[1];d['raw_legs']=meta[2];raw_ms_mismatch+=int(int(d['first_event_ms'])!=meta[1]);by[int(d['market_id'])].append(d)
    for mid in by:by[mid].sort(key=lambda r:(int(r['first_event_ms']),int(r['raw_first_id'])))
    c=Counter();viol=Counter();pair_sums=[];examples=[];market_rows=[];same_clock_groups=0;same_clock_extra=0;same_clock_resolved=0
    for mid,evs in by.items():
        up=dn=0.;lots={'UP':deque(),'DOWN':deque()};mid_pair_qty=mid_expand_qty=mid_repair_qty=mid_unmatched_expand=0.;mid_comp=0
        clocks=Counter(int(r['first_event_ms']) for r in evs);same_clock_groups+=sum(1 for n in clocks.values() if n>1);same_clock_extra+=sum(n-1 for n in clocks.values() if n>1);same_clock_resolved+=sum(n for n in clocks.values() if n>1)
        for r in evs:
            pid=str(r['parent_id']);side=str(r['side']).upper();t=int(r['first_event_ms']);px=float(r['average_price']);q=float(r['shares']);rel=role(side,up,dn);gap=abs(up-dn);repair_q=expand_q=seed_q=0.
            if rel==1:
                repair_q=min(q,gap);expand_q=max(0.,q-repair_q)
            elif rel==-1:expand_q=q
            else:seed_q=q
            if abs(q-repair_q-expand_q-seed_q)>1e-7:viol['physicalConservation']+=1
            c['parents']+=1;c['repairComponents']+=int(repair_q>EPS);c['expandComponents']+=int(expand_q>EPS);c['seedComponents']+=int(seed_q>EPS)
            if repair_q>EPS and expand_q>EPS:c['compositeParents']+=1;c['compositeOverflowQty']+=expand_q
            if repair_q>EPS:
                lot_id=f'{mid}:{pid}:R';lots[side].append({'lotId':lot_id,'marketId':mid,'parentId':pid,'t':t,'rawFirstId':int(r['raw_first_id']),'side':side,'price':px,'initialQty':repair_q,'remainingQty':repair_q});mid_repair_qty+=repair_q;c['repairQty']+=repair_q;mid_comp+=1
            if expand_q>EPS:
                c['expandQty']+=expand_q;mid_expand_qty+=expand_q;mid_comp+=1;opp='DOWN' if side=='UP' else 'UP';rem=expand_q
                while rem>EPS and lots[opp]:
                    lot=lots[opp][0];take=min(rem,float(lot['remainingQty']))
                    if take<=EPS:lots[opp].popleft();continue
                    if lot['side']==side:viol['sameSideMatch']+=1
                    ps=float(lot['price'])+px;pair_sums.append(ps);mid_pair_qty+=take;c['matchedQty']+=take;c['matchRows']+=1;c['nonDamagingMatchedQty']+=take if ps<=1.+EPS else 0.
                    if len(examples)<40:examples.append({'marketId':mid,'repairLotId':lot['lotId'],'repairParentId':lot['parentId'],'repairRawFirstId':lot['rawFirstId'],'repairT':lot['t'],'repairSide':lot['side'],'repairPrice':lot['price'],'expandParentId':pid,'expandRawFirstId':int(r['raw_first_id']),'expandT':t,'expandSide':side,'expandPrice':px,'matchedQty':take,'pairSum':ps,'nonDamaging':ps<=1.+EPS,'expandWasCompositeOverflow':repair_q>EPS})
                    lot['remainingQty']-=take;rem-=take
                    if lot['remainingQty']<-EPS:viol['negativeLotRemainder']+=1
                    if lot['remainingQty']<=EPS:lots[opp].popleft()
                if rem>EPS:mid_unmatched_expand+=rem;c['unmatchedExpandQty']+=rem
            if side=='UP':up+=q
            else:dn+=q
        ur=sum(float(x['remainingQty']) for s in ('UP','DOWN') for x in lots[s]);c['unmatchedRepairQty']+=ur
        if mid_pair_qty-mid_expand_qty>1e-7:viol['marketMatchedExceedsExpand']+=1
        if mid_pair_qty-mid_repair_qty>1e-7:viol['marketMatchedExceedsRepair']+=1
        market_rows.append({'marketId':mid,'parents':len(evs),'components':mid_comp,'repairQty':mid_repair_qty,'expandQty':mid_expand_qty,'matchedQty':mid_pair_qty,'unmatchedExpandQty':mid_unmatched_expand,'unmatchedRepairQty':ur})
    if unmapped:viol['unmappedParentsToRawOrder']+=len(unmapped)
    if raw_ms_mismatch:viol['snapshotVsRawFirstMsMismatch']+=raw_ms_mismatch
    weighted_non=float(c['nonDamagingMatchedQty'])/float(c['matchedQty']) if c['matchedQty'] else None
    out={'version':'TARGET_ETH_OBSERVABLE_FIFO_ALLOCATION_LEDGER_V3','date':'2026-09-04','researchOnly':True,'actionAuthority':False,'snapshotDb':str(a.snapshot_db),'rawOrderingDb':str(a.raw_db),'markets':len(by),'rawOrderingLinkage':{'snapshotParents':len(raw),'mappedParents':len(raw)-len(unmapped),'unmappedParents':len(unmapped),'snapshotVsRawFirstMsMismatch':raw_ms_mismatch},'counts':dict(c),'sameClock':{'groups':same_clock_groups,'extraParents':same_clock_extra,'parentsInResolvedSameClockGroups':same_clock_resolved},'pairSum':quantiles(pair_sums),'matchedQtyPairSumWeightedNonDamagingShare':weighted_non,'invariantViolations':dict(viol),'allHardInvariantsPass':sum(viol.values())==0,'sampleMatches':examples,'marketSample':market_rows[:100],'interpretationBoundary':'Observable deterministic FIFO economic lot ledger reconstructed from Target parent events with raw event-id ordering; not a claim about Target private responsibility IDs.','boundary':['same V2 snapshot cohort','same-clock ordered by raw wallet_shadow_target_events first id','Repair-first physical decomposition','composite overflow retained as Expand','FIFO opposite-side lot matching','no model fitting','no winner/settlement use','no runtime mutation','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'markets':out['markets'],'linkage':out['rawOrderingLinkage'],'counts':out['counts'],'sameClock':out['sameClock'],'pairSum':out['pairSum'],'nonDamagingMatchedShare':weighted_non,'violations':out['invariantViolations'],'allHardInvariantsPass':out['allHardInvariantsPass']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
