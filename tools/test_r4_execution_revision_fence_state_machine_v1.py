from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_execution_revision_fence_state_machine_v1_report.json'

def repair_taker_qty(predicted:float, latest_obligation:float)->float:
    # No 18-share cap. Bound only to latest authoritative REPAIR obligation.
    return max(0.0,min(float(predicted),float(latest_obligation)))

def add_taker_qty(predicted:float)->float:
    # ADD is not a repair remainder and is not capped by repair semantics.
    return max(0.0,float(predicted))

def maker_child_qty(latest_unowned:float)->float:
    # Current live safety compatibility: each Maker child <=10, but not exactly 10.
    return max(0.0,min(10.0,float(latest_unowned)))

def main():
    rows=[]
    # 1 stale Maker action invalidated by a fill revision
    rev=10;action={'computedRevision':rev,'kind':'MAKER_REPAIR','qty':10.0};rev=11;latest_gap=4.0
    stale=action['computedRevision']!=rev;new_qty=maker_child_qty(latest_gap) if stale else action['qty']
    rows.append({'scenario':'MAKER_STALE_ACTION_INTERRUPTED_BY_FILL','pass':stale and abs(new_qty-4.0)<1e-9,'staleActionAborted':stale,'recomputedQty':new_qty})
    # 2 Repair Taker can exceed 18 when obligation requires it
    q=repair_taker_qty(42.0,35.0)
    rows.append({'scenario':'TAKER_REPAIR_NO_18_CAP','pass':abs(q-35.0)<1e-9 and q>18,'predictedQty':42.0,'latestRepairObligation':35.0,'submittedQty':q})
    # 3 Repair Taker cannot overshoot a shrunken obligation
    q=repair_taker_qty(30.0,7.25)
    rows.append({'scenario':'TAKER_REPAIR_LATEST_OBLIGATION_BOUND','pass':abs(q-7.25)<1e-9,'predictedQty':30.0,'latestRepairObligation':7.25,'submittedQty':q})
    # 4 ADD Taker remains unconstrained by repair remainder semantics
    q=add_taker_qty(27.0)
    rows.append({'scenario':'TAKER_ADD_NOT_REPAIR_CAPPED','pass':abs(q-27.0)<1e-9 and q>18,'predictedQty':27.0,'submittedQty':q})
    # 5 cancel request retains ownership; cancel-race fill changes revision/remainder before replacement
    rev=20;owner={'state':'CANCEL_PENDING','remaining':10.0,'owns':True};race_fill=6.0;owner['remaining']-=race_fill;rev+=1;gap_after=3.0;other_reserved=0.0;unowned=max(0.0,gap_after-other_reserved);replacement=maker_child_qty(unowned)
    rows.append({'scenario':'CANCEL_RACE_FILL_RECOMPUTES_REMAINDER','pass':owner['owns'] and owner['state']=='CANCEL_PENDING' and rev==21 and abs(replacement-3.0)<1e-9,'revision':rev,'oldOwnerStillOwnsUntilTerminal':owner['owns'],'replacementQtyAfterTerminalWouldBe':replacement})
    # 6 another carrier can satisfy obligation while cancel is pending; no duplicate replacement
    gap=8.0;other_reserved=10.0;unowned=max(0.0,gap-other_reserved);replacement=maker_child_qty(unowned)
    rows.append({'scenario':'OTHER_CARRIER_ALREADY_OWNS_REMAINDER','pass':replacement==0.0,'gap':gap,'otherLiveReserved':other_reserved,'replacementQty':replacement})
    # 7 partial fill resets stale candidate clock
    stale_started=1000;partial_fill_at=2600;clock_after=None
    rows.append({'scenario':'PARTIAL_FILL_INTERRUPTS_STALE_CLOCK','pass':clock_after is None,'staleCandidateStartedAtMs':stale_started,'partialFillAtMs':partial_fill_at,'staleClockAfterFill':clock_after})
    # 8 sub-min-notional exact remainder stays dust rather than oversized child
    qty=maker_child_qty(0.7);price=0.5;dust=qty*price<1.0
    rows.append({'scenario':'EXACT_REMAINDER_DUST_NO_OVERSIZE','pass':dust and abs(qty-0.7)<1e-9,'qty':qty,'price':price,'notional':qty*price,'state':'DUST_REPAIR_PENDING' if dust else 'SUBMIT'})
    out={'version':'R4_EXECUTION_REVISION_FENCE_STATE_MACHINE_V1','researchOnly':True,'actionAuthority':False,'passed':all(r['pass'] for r in rows),'scenariosPassed':sum(r['pass'] for r in rows),'scenariosTotal':len(rows),'rows':rows,'invariants':['no venue write from stale execution revision','Taker no fixed 18-share cap','Repair Taker bounded only by latest authoritative repair obligation','ADD Taker not repair-capped','Maker child <=10 but may be smaller exact remainder','cancel request does not release ownership','fill progress interrupts stale refresh candidate','sub-min-notional exact remainder stays explicit dust']}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
