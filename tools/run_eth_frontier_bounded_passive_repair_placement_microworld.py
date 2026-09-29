from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from tools.eth_repair_modular.frontier_bounded_passive_repair_placement import (
    FrontierBoundedPassiveRepairContext,
    FrontierBoundedPassiveRepairPlacementPolicyV1,
)
P=FrontierBoundedPassiveRepairPlacementPolicyV1()

def ctx(**kw):
    b=dict(objective_role='REPAIR',parent_id=1,side='DOWN',proposed_price=.47,proposed_qty=2.5157,
           live_best_bid=.63,live_best_ask=.65,inherited_economic_ceiling=.6124560577773237,
           floor_before=-1.3333333333,projected_floor_at_target=-.95,tick_size=.01)
    b.update(kw);return FrontierBoundedPassiveRepairContext(**b)

def t(name,c,change,target=None,reason=None):
    d=P.evaluate(c);ok=(d.change_price==change and (target is None or abs(d.target_price-target)<=1e-9) and (reason is None or d.reason==reason))
    return {'name':name,'pass':ok,'decision':asdict(d)}

def main():
    rows=[]
    rows.append(t('1945869_geometry_047_to_061',ctx(),True,.61,'RAISE_PASSIVE_REPAIR_TO_ECONOMIC_FRONTIER'))
    rows.append(t('best_bid_inside_ceiling_use_best_bid',ctx(live_best_bid=.58,live_best_ask=.60),True,.58,'RAISE_PASSIVE_REPAIR_TO_ECONOMIC_FRONTIER'))
    rows.append(t('proposed_already_at_target_no_change',ctx(proposed_price=.61),False,.61,'PROPOSED_PRICE_ALREADY_AT_OR_ABOVE_BOUNDED_FRONTIER'))
    rows.append(t('proposed_above_target_no_lowering',ctx(proposed_price=.62),False,.62,'PROPOSED_PRICE_ALREADY_AT_OR_ABOVE_BOUNDED_FRONTIER'))
    rows.append(t('maker_cross_guard',ctx(live_best_bid=.61,live_best_ask=.61),False,.47,'TARGET_NOT_MAKER_SAFE'))
    rows.append(t('floor_damage_blocks',ctx(projected_floor_at_target=-1.34),False,.47,'BOUNDED_FRONTIER_PRICE_DAMAGES_FLOOR'))
    rows.append(t('missing_floor_projection_blocks',ctx(projected_floor_at_target=None),False,.47,'NO_EXACT_FLOOR_PROJECTION'))
    rows.append(t('non_repair_blocks',ctx(objective_role='EXPAND'),False,.47,'NOT_PARENT_REPAIR'))
    rows.append(t('no_parent_blocks',ctx(parent_id=None),False,.47,'NOT_PARENT_REPAIR'))
    rows.append(t('no_frontier_blocks',ctx(live_best_bid=None),False,.47,'NO_LIVE_FRONTIER_OR_CEILING'))
    rows.append(t('no_ceiling_blocks',ctx(inherited_economic_ceiling=None),False,.47,'NO_LIVE_FRONTIER_OR_CEILING'))
    rows.append(t('tick_floor_never_exceeds_ceiling',ctx(live_best_bid=.619,inherited_economic_ceiling=.6124560577773237),True,.61,'RAISE_PASSIVE_REPAIR_TO_ECONOMIC_FRONTIER'))
    out={'version':'ETH_FRONTIER_BOUNDED_PASSIVE_REPAIR_PLACEMENT_MICROWORLD_V1','date':'2026-09-05','researchOnly':True,'policy':P.name,'tests':rows,'passed':sum(r['pass'] for r in rows),'total':len(rows)};out['allPass']=out['passed']==out['total'];out['boundary']=['new passive Repair child price only','no qty/debt/role/ownership change','target = highest maker-safe price at or below min(current best bid, inherited economic ceiling)','no copied behind-tick threshold','exact projected floor must not worsen','no Active authority change']
    p=Path('data/research/r4_v0/p0_provenance_v1/ETH_FRONTIER_BOUNDED_PASSIVE_REPAIR_PLACEMENT_MICROWORLD_V1_RESULT_20260905.json');p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
