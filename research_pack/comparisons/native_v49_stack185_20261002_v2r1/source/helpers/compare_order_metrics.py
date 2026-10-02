"""Compare actual B orders with read-only local A observations, on META clock."""
import json,statistics as S
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parents[2]
RET=ROOT/'data/research/lan_worker_returns/native-engine-platform-order-audit185-20261002-v1'
local=json.loads((RET/'local.json').read_bytes())
cloud=json.loads((ROOT/'docs/research_specs/results/CLOUD_LAB_RESULTS_20261002.json').read_bytes())
original=json.loads((ROOT/'data/research/lan_worker_returns/native-engine-platform-185-20261002-v1/local.json').read_bytes())
assert local==original, 'The observational replay must not change any A result'
for strategy,rs in local.items():
    ci={r['market']:r for r in cloud[strategy]}
    assert len(rs)==len(ci)==185
    assert all(all(ci[r['market']][k]==v for k,v in r.items()) for r in rs), 'Cloud adds cohort metadata only; all original result fields must match'
raw=json.loads((RET/'A_ORDER_SUMMARIES.json').read_bytes())['rows']
assert len(raw)==370
order_index={}
for r in raw:
    key=(r['strategy'],r['market_id']);assert key not in order_index
    assert r['order_count']==len(r['orders'])
    assert all(o['qty']==15 for o in r['orders'])
    assert len({o['order_sequence'] for o in r['orders']})==len(r['orders'])
    assert r['sum_order_seconds']==sum(o['official_window_seconds'] for o in r['orders'])
    if r['orders']:assert r['average_official_window_seconds']==r['sum_order_seconds']/r['order_count']
    order_index[key]=r
comparison=json.loads((P/'B_STRICT_CLOUD_COMPARISON.json').read_bytes())
strategies={}
for strategy,summary in comparison['strategies'].items():
    pairs=[]
    for pair in summary['pairs']:
        eff=pair['effective_strategy'];mid=pair['market_id']
        a=order_index[(eff,mid)] if eff!='NO_TRADE' else {'order_count':0,'average_official_window_seconds':None,'sum_order_seconds':0}
        bv=pair['B_average_order_seconds'];av=a['average_official_window_seconds']
        pairs.append({'market_id':mid,'effective_strategy':eff,'A_local_order_count':a['order_count'],'B_order_count':pair['B_submits'],'order_count_difference':pair['B_submits']-a['order_count'],'A_local_mean_order_seconds':av,'B_mean_order_seconds':bv,'mean_order_seconds_difference':bv-av if av is not None and bv is not None else None,'A_local_sum_order_seconds':a['sum_order_seconds']})
    timings=[r['mean_order_seconds_difference'] for r in pairs if r['mean_order_seconds_difference'] is not None]
    an=sum(r['A_local_order_count'] for r in pairs);bn=sum(r['B_order_count'] for r in pairs)
    strategies[strategy]={'markets':185,'A_local_total_orders':an,'B_total_orders':bn,'total_order_count_difference':bn-an,'mean_order_count_difference':S.fmean(r['order_count_difference'] for r in pairs),'mean_per_market_order_seconds_difference_on_both_ordering_paths':S.fmean(timings) if timings else None,'paired_ordering_markets':len(timings),'A_local_order_weighted_mean_seconds':sum(r['A_local_sum_order_seconds'] for r in pairs)/an if an else None,'B_order_weighted_mean_seconds':summary['B_order_weighted_mean_seconds'],'order_weighted_mean_seconds_difference':summary['B_order_weighted_mean_seconds']-sum(r['A_local_sum_order_seconds'] for r in pairs)/an if an and bn else None,'pairs':pairs}
out={'status':'COMPLETE_LOCAL_A_OBSERVED_ORDER_COMPARISON','A_observed_job':'native-engine-platform-order-audit185-20261002-v1','A_observer_changes_results':'NO_EXACT_CLOUD_AND_ORIGINAL_LOCAL_ROW_MATCH','time_basis':'seconds relative to each official META window_start_ms, for A and B','reference_scope':'Counts/times are observed LOCAL_A. Cloud JSON does not provide these fields; do not claim cloud sequence readback. v1/v2 references composed using frozen RV selection.','B_scope':'Corrected strict IOC run; all185 descriptive, including four invalid FAV-family context paths. No substitute markets.','strategies':strategies}
(P/'B_ORDER_COMPARISON.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({s:{k:v for k,v in x.items() if k!='pairs'} for s,x in strategies.items()}))
