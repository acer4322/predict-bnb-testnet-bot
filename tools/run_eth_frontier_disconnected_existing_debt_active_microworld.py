from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from tools.eth_repair_modular.frontier_disconnected_existing_debt_active import (
    FrontierDisconnectedExistingDebtActiveContext,
    FrontierDisconnectedExistingDebtActivePolicyV1,
)

P=FrontierDisconnectedExistingDebtActivePolicyV1()

def ctx(**kw):
    base=dict(parent_id=1,parent_side='DOWN',seconds_left=250.0,same_parent_debt=2.75,
              live_bid=.63,live_ask=.65,inherited_economic_ceiling=.516,
              floor_before=-1.33,floor_after_venue_min_repair=-.79,
              passive_reserved_qty=0.0,other_same_parent_reserved_qty=0.0,
              payment_progress_since_epoch=False,active_already_owned=False,hard_confirmed=False)
    base.update(kw);return FrontierDisconnectedExistingDebtActiveContext(**base)

def test(name,c,allow,reason=None):
    d=P.evaluate(c); ok=(d.allow_active_existing_debt==allow and (reason is None or d.reason==reason))
    return {'name':name,'pass':ok,'decision':asdict(d)}

def main():
    rows=[]
    rows.append(test('allow_disconnected_floor_improving_existing_debt',ctx(),True))
    rows.append(test('competitive_passive_within_ceiling_blocks',ctx(live_bid=.50),False,'PASSIVE_COMPETITIVE_PRICE_STILL_WITHIN_CEILING'))
    rows.append(test('passive_reservation_blocks',ctx(passive_reserved_qty=1.0),False,'SIBLING_RESERVATION_STILL_OWNS_DEBT'))
    rows.append(test('other_sibling_reservation_blocks',ctx(other_same_parent_reserved_qty=.2),False,'SIBLING_RESERVATION_STILL_OWNS_DEBT'))
    rows.append(test('active_owner_blocks',ctx(active_already_owned=True),False,'ACTIVE_ALREADY_OWNED'))
    rows.append(test('hard_confirmed_blocks',ctx(hard_confirmed=True),False,'ACTIVE_ALREADY_OWNED'))
    rows.append(test('payment_progress_blocks_reassessment',ctx(payment_progress_since_epoch=True),False,'PAYMENT_PROGRESS_REASSESS'))
    rows.append(test('late_fence_preserved',ctx(seconds_left=179.9),False,'LATE_ACTIVE_FENCE_PRESERVED'))
    rows.append(test('no_debt_blocks',ctx(same_parent_debt=0),False,'NO_PARENT_REPAIR_DEBT'))
    # ask=.30 -> venue minimum 3.333 > 2.75, no overflow in V1
    rows.append(test('venue_min_overflow_not_allowed_in_v1',ctx(live_ask=.30,live_bid=.60),False,'VENUE_MIN_EXCEEDS_UNRESERVED_DEBT'))
    rows.append(test('non_improving_floor_blocks',ctx(floor_after_venue_min_repair=-1.34),False,'ACTIVE_REPAIR_DOES_NOT_STRICTLY_IMPROVE_FLOOR'))
    rows.append(test('missing_projection_blocks',ctx(floor_after_venue_min_repair=None),False,'NO_EXACT_FLOOR_PROJECTION'))
    out={'version':'ETH_FRONTIER_DISCONNECTED_EXISTING_DEBT_ACTIVE_MICROWORLD_V1','researchOnly':True,'policy':P.name,'tests':rows,'passed':sum(r['pass'] for r in rows),'total':len(rows)}
    out['allPass']=out['passed']==out['total']
    out['boundary']=['existing Repair debt only; no new speculative responsibility','passive frontier disconnect defined structurally as current best bid above inherited ceiling','no arbitrary behind-tick threshold','sibling reservation must be zero','venue-min overflow intentionally disabled in V1','exact projected worst-case floor must strictly improve','legacy <=180s Active fence preserved']
    p=Path('data/research/r4_v0/p0_provenance_v1/ETH_FRONTIER_DISCONNECTED_EXISTING_DEBT_ACTIVE_MICROWORLD_V1_RESULT_20260905.json');p.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
