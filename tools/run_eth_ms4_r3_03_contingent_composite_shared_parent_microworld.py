from __future__ import annotations
import json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
EPS=1e-9

def run_case(name,debt,sibling_qty,price,sequence):
    venue=1.0/float(price)
    unreserved=max(0.0,float(debt)-float(sibling_qty))
    contingent_qty=unreserved+venue
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();pid=1
    L.register_carrier('SIBLING',pid,float(debt));L.register_carrier('CONTINGENT',pid,float(debt))
    cum={'SIBLING':0.0,'CONTINGENT':0.0};alloc=[]
    for carrier,inc in sequence:
        cum[carrier]+=float(inc)
        a=L.allocate_cumulative(carrier,pid,cum[carrier],float(debt))
        if a is not None:alloc.append(a.__dict__.copy())
    st=L.describe_parent(pid)
    cont_over=sum(float(x['overflow_increment']) for x in alloc if x['carrier_key']=='CONTINGENT')
    total_over=float(st['transitionOverflow'])
    risk=cont_over*float(price)
    return {'name':name,'debt':debt,'siblingQty':sibling_qty,'price':price,'venueMin':venue,'unreservedDebt':unreserved,
            'contingentQty':contingent_qty,'sequence':sequence,'allocations':alloc,'parent':st,
            'contingentOverflowQty':cont_over,'contingentOverflowRisk':risk,'totalOverflowQty':total_over,
            'overflowCapPass':cont_over<=venue+EPS,'riskCapPass':risk<=1.0+EPS,
            'conservationPass':abs(float(st['repairPaid'])+float(st['remainingDebt'])-float(st['initialDebt']))<=1e-9}

def market_cases(mid,debt,sib,p):
    venue=1.0/p;un=max(0.0,debt-sib);cq=un+venue
    return [
      run_case(f'{mid}_CONTINGENT_FULL_FIRST',debt,sib,p,[('CONTINGENT',cq),('SIBLING',sib)]),
      run_case(f'{mid}_SIBLING_FULL_FIRST',debt,sib,p,[('SIBLING',sib),('CONTINGENT',cq)]),
      run_case(f'{mid}_INTERLEAVED_HALF',debt,sib,p,[('SIBLING',sib/2),('CONTINGENT',cq/2),('SIBLING',sib/2),('CONTINGENT',cq/2)]),
      run_case(f'{mid}_CONTINGENT_PARTIAL_THEN_SIBLING',debt,sib,p,[('CONTINGENT',cq/3),('SIBLING',sib),('CONTINGENT',cq*2/3)]),
      run_case(f'{mid}_SAME_CLOCK_SIBLING_THEN_CONTINGENT',debt,sib,p,[('SIBLING',sib),('CONTINGENT',cq)]),
      run_case(f'{mid}_SAME_CLOCK_CONTINGENT_THEN_SIBLING',debt,sib,p,[('CONTINGENT',cq),('SIBLING',sib)]),
    ]

def main():
    cases=[]
    cases += market_cases(1946317,2.7664517696201454,2.127659574468085,0.46)
    cases += market_cases(1946640,3.397306397306397,2.1739130434782608,0.43)
    gates={'allOverflowCapPass':all(x['overflowCapPass'] for x in cases),
           'allRiskCapPass':all(x['riskCapPass'] for x in cases),
           'allConservationPass':all(x['conservationPass'] for x in cases),
           'cases':len(cases)}
    out={'version':'MS4_R3_03_CONTINGENT_COMPOSITE_SHARED_PARENT_MICROWORLD_V1','researchOnly':True,
         'formula':'contingentQty=max(0,parentDebt-liveSiblingPhysicalRemaining)+oneVenueMinAtContingentPrice',
         'cases':cases,'gates':gates,
         'boundary':['same parent debt shared by sibling and contingent carriers','pending carrier gives zero payment/protection','confirmed fill repair-first/overflow-second','one venue-min overflow is the original R2.63 risk authority cap','no HFT behavior/no Target runtime input']}
    print(json.dumps({'ok':all(v for k,v in gates.items() if k!='cases'),'gates':gates,
                      'maxContingentOverflowRisk':max(x['contingentOverflowRisk'] for x in cases),
                      'maxContingentOverflowQtyOverVenue':max(x['contingentOverflowQty']-x['venueMin'] for x in cases)},ensure_ascii=False))
    from pathlib import Path
    p=Path('data/research/r4_v0/p0_provenance_v1/MS4_R303_CONTINGENT_COMPOSITE_SHARED_PARENT_MICROWORLD_RESULT_20260907.json')
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8')
if __name__=='__main__':main()
