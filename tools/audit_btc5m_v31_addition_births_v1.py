"""V31 follow-up: attribute later UP fills to preexisting versus new owners."""
import collections
import json
import math
from prepare_btc5m_held_amplitude_v1 import ROOT,R,RET,JOB,BASE,STEM,START,read,sha,get,dump_for
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs,close

OUT='BTC5M_V31_ADDITION_BIRTHS_V1_20260913'


def attribute(result,trace):
    births={o['key']:dict(t=p['t'],**o) for p in trace['plans'] for o in p['operations'] if o['kind']=='NEW'}
    queues=collections.defaultdict(collections.deque)
    for r in trace['demand_final']['full_raw_receipts']:
        assert r['fee']==0.
        if r['qty']>0:queues[r['key']].append([r['qty'],r['contractPrice']])
    legs=[]
    for event in result['atomic_responsibility_events']:
        for fill in event['fill_rows']:
            remaining=fill['fill_increment'];op=births[fill['key']]
            while remaining>1e-10:
                raw=queues[fill['key']][0];q=min(remaining,raw[0])
                assert op['t']<=event['t'] and op['side']==fill['side']
                legs.append(dict(t=event['t'],born_t=op['t'],key=fill['key'],side=fill['side'],
                    route=op['route'],qty=q,cash=q*raw[1],role=op['role']))
                remaining-=q;raw[0]-=q
                if raw[0]<=1e-10:queues[fill['key']].popleft()
    assert sum(x[0] for queue in queues.values() for x in queue)<1e-7
    for owner in trace['demand_final']['all_final_carriers']:
        matched=[x for x in legs if x['key']==owner['key']]
        close(math.fsum(x['qty'] for x in matched),owner['filled'])
        close(math.fsum(x['cash'] for x in matched),owner['payment'])
    independent=canonical_legs(result,trace)
    assert len(legs)==len(independent)
    for a,b in zip(legs,independent):
        assert a['t']==b['t'] and a['side']==b['side']
        close(a['qty'],b['qty']);close(a['cash'],b['cash'])
    close(math.fsum(x['cash'] for x in legs),result['final_cost'])
    return births,legs


def partition(births,legs,cut):
    later=[x for x in legs if x['side']=='UP' and x['t']>cut]
    buckets={}
    for name,preexisting in [('preexisting_at_cut',True),('new_after_cut',False)]:
        selected=[x for x in later if (x['born_t']<=cut)==preexisting]
        buckets[name]=dict(qty=math.fsum(x['qty'] for x in selected),cash=math.fsum(x['cash'] for x in selected),
            positive_fill_owners=len({x['key'] for x in selected}),owner_keys=sorted({x['key'] for x in selected}))
    close(sum(v['cash'] for v in buckets.values()),math.fsum(x['cash'] for x in later))
    close(sum(v['qty'] for v in buckets.values()),math.fsum(x['qty'] for x in later))
    return dict(t=cut,seconds=(cut-START)/1000,partitions=buckets,
        up_new_after_cut=sum(o['side']=='UP' and o['t']>cut for o in births.values()))


def main():
    prior=read(R/(STEM+'_RESULT.json'));assert prior['verification']=='PASS'
    cut=prior['candidate']['post_exposure']['peak']['t'];out={}
    for tag,folder in [('baseline',BASE),('held_amplitude',RET/JOB)]:
        result,trace=get(folder);births,legs=attribute(result,trace)
        windows={str(s):partition(births,legs,START+int(s*1000)) for s in (50,150,240)}
        peak=partition(births,legs,cut)
        if tag=='held_amplitude':
            close(sum(v['cash'] for v in peak['partitions'].values()),prior['candidate']['post_exposure']['peak']['until_end']['strong_acquisition_drag'])
        start,end=trace['observations'][0],trace['observations'][-1]
        out[tag]=dict(result_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'),
            up_new_total=sum(o['side']=='UP' for o in births.values()),windows=windows,
            after_previously_reported_v31_peak=peak,gross_first=start['gross'],gross_last=end['gross'],
            original_desired_last=end['desired'],up_role_counts=dict(collections.Counter(o['role'] for o in births.values() if o['side']=='UP')),
            canonical_legs=legs)
    assert out['baseline']['gross_first']==out['held_amplitude']['gross_first']
    assert out['baseline']['gross_last']==out['held_amplitude']['gross_last']
    result=dict(status='PASS',new_native_jobs=0,model_fits=0,actor_changes=0,figures=0,
        prior_result_sha256=sha(R/(STEM+'_RESULT.json')),arms=out,
        dedup='V31 counted phase flows and peak response. This follow-up newly attributes every later canonical fill to the original owner birth time; no additional replay or parameter search.',
        selection='Fixed 50/150/240-second cuts and the already reported V31 global peak; peak is descriptive, never an actor input.',
        interpretation='All 35 filled UP owners after the V31 peak were created after that peak. Additional UP admission is an actionable mechanism hypothesis; deleting orders would change subsequent state, so these totals are not counterfactual savings.',
        reporting_preference='Generate a figure only when a special path or event is clearer with one. This follow-up has none.')
    dump_for(OUT,'RESULT',result)
    print(json.dumps(dict(status='PASS',new_native_jobs=0,figures=0,
        after_peak=out['held_amplitude']['after_previously_reported_v31_peak'],
        after240=out['held_amplitude']['windows']['240']),indent=2))


if __name__=='__main__':main()
