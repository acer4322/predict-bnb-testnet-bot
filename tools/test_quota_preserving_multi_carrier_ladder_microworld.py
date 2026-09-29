from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.quota_preserving_carrier_ladder import CarrierLadderContext,QuotaPreservingCarrierLadderPolicyV1

def main():
    p=QuotaPreservingCarrierLadderPolicyV1()
    cases={
      'largeDebt3':CarrierLadderContext(1,8.0,0.0,[0.50,0.48,0.46],3,12.0),
      'mediumDebt1':CarrierLadderContext(2,3.0,0.0,[0.50,0.48,0.46],3,12.0),
      'existingReservation':CarrierLadderContext(3,6.0,2.0,[0.50,0.48,0.46],3,12.0),
      'tinyDebt0':CarrierLadderContext(4,1.0,0.0,[0.50,0.48],3,12.0),
      'duplicatePrices':CarrierLadderContext(5,6.0,0.0,[0.50,0.50,0.48],3,12.0),
      'venueQtyBound':CarrierLadderContext(6,20.0,0.0,[0.05,0.50,0.48],3,12.0)
    }
    out={}
    for k,c in cases.items():
        d=p.evaluate(c)
        out[k]={'reason':d.reason,'plans':[x.__dict__ for x in d.plans],'totalNewReservedQty':d.total_new_reserved_qty,'totalReservedAfter':d.total_reserved_after,'availableBefore':d.available_before}
    checks={
      'largeDebtGetsThree':len(out['largeDebt3']['plans'])==3,
      'mediumDebtGetsOne':len(out['mediumDebt1']['plans'])==1,
      'existingReservationBounded':out['existingReservation']['totalReservedAfter']<=6.0+1e-9,
      'tinyDebtGetsZero':len(out['tinyDebt0']['plans'])==0,
      'duplicatePriceDeduped':len({x['price'] for x in out['duplicatePrices']['plans']})==len(out['duplicatePrices']['plans']),
      'venueQtyBoundRespected':all(x['qty']<=12.0+1e-9 for x in out['venueQtyBound']['plans']),
      'allQuotaBounded':all(out[k]['totalReservedAfter']<=cases[k].authoritative_debt+1e-9 for k in out)
    }
    result={'version':'QUOTA_PRESERVING_MULTI_CARRIER_LADDER_MICROWORLD_RESULT_V1','date':'2026-09-05','checks':checks,'cases':out,'allPass':all(checks.values()),'decision':'PASS_TO_REALISTIC_HFT_FEASIBILITY_SHADOW' if all(checks.values()) else 'REJECT','boundary':['planning only','no order submit','no debt creation','no 8781']}
    op=ROOT/'data/research/r4_v0/p0_provenance_v1/QUOTA_PRESERVING_MULTI_CARRIER_LADDER_MICROWORLD_RESULT_20260905.json';op.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
