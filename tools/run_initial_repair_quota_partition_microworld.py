from __future__ import annotations
import json
from tools.eth_repair_modular.initial_repair_quota_partition import InitialRepairQuotaPartitionContext,InitialRepairQuotaPartitionPolicyV1

p=InitialRepairQuotaPartitionPolicyV1()
cases=[]
def check(name,ctx,pred):
    d=p.evaluate(ctx); ok=bool(pred(d)); cases.append({'name':name,'pass':ok,'decision':{'partition':d.partition,'plans':[x.__dict__ for x in d.plans],'totalReservedQty':d.total_reserved_qty,'residualDebt':d.residual_unreserved_debt,'reason':d.reason}})

check('large_parent_two_local_prices',InitialRepairQuotaPartitionContext(1,5.0,.40,5.0,[.39,.41,.38],.45),lambda d:d.partition and len(d.plans)==2 and d.total_reserved_qty<=5.0+1e-9)
check('does_not_copy_fixed_spacing',InitialRepairQuotaPartitionContext(1,6.0,.40,6.0,[.397,.405,.421],.45),lambda d:d.partition and len({round(x.price,3) for x in d.plans})==2)
check('small_debt_cannot_force_two',InitialRepairQuotaPartitionContext(1,3.0,.40,3.0,[.39,.41],.45),lambda d:not d.partition and d.reason=='ONLY_ONE_CHILD_FITS_PARENT_DEBT')
check('already_partitioned_original_unchanged',InitialRepairQuotaPartitionContext(1,5.0,.40,2.5,[.39,.41],.45),lambda d:not d.partition and d.reason=='ORIGINAL_CARRIER_DOES_NOT_MONOPOLIZE_PARENT')
check('economic_ceiling_filters_price',InitialRepairQuotaPartitionContext(1,6.0,.40,6.0,[.43,.39],.405),lambda d:d.partition and all(x.price<=.405+1e-9 for x in d.plans))
check('duplicate_prices_deduped',InitialRepairQuotaPartitionContext(1,6.0,.40,6.0,[.40,.40,.39],.45),lambda d:d.partition and len(d.plans)==2 and len({x.price for x in d.plans})==2)
check('venue_qty_cap_blocks_tiny_price',InitialRepairQuotaPartitionContext(1,20.0,.05,20.0,[.10,.11],.20,max_venue_qty=12.0),lambda d:not any(x.price==.05 for x in d.plans))
check('total_never_exceeds_debt',InitialRepairQuotaPartitionContext(1,4.8,.42,4.8,[.41,.40,.39],.50),lambda d:d.total_reserved_qty<=4.8+1e-9)
check('invalid_debt_blocks',InitialRepairQuotaPartitionContext(1,0.0,.40,0.0,[.39],.45),lambda d:not d.partition)
check('invalid_original_price_blocks',InitialRepairQuotaPartitionContext(1,5.0,0.0,5.0,[.39],.45),lambda d:not d.partition)

out={'version':'INITIAL_REPAIR_QUOTA_PARTITION_MICROWORLD_V1','date':'2026-09-05','policy':p.name,'passed':sum(x['pass'] for x in cases),'total':len(cases),'allPass':all(x['pass'] for x in cases),'cases':cases,'boundary':['planning only','first passive Repair carrier only','same parent/objective/role','visible price inputs only','venue-min children','aggregate reservation <= authoritative debt','no fixed Target tick spacing','no debt creation','no 8781']}
print(json.dumps(out,ensure_ascii=False))
