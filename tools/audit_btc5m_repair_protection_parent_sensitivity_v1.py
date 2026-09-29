"""Posthoc known-parent partial-fill sensitivity, without inferring submission times."""
import collections,json,math
from audit_btc5m_repair_protection_reuse_v1 import R,ROOT,STEM,read,sha,stats


def summarize(rows):
    return dict(n=len(rows),before_break_even=sum(r['end_weak_payoff']<-1e-9 for r in rows),
        subsequent_spend_exceeds_gain=sum(r['strong_spend']>r['repair_gain']+1e-9 for r in rows),
        returned_to_weak=sum(r['returned_to_weak'] for r in rows),
        spend_over_repair_gain=stats(r['spend_over_preceding_repair_gain'] for r in rows))


def main():
    base=read(R/(STEM+'_RESULT.json'));prereg=read(R/(STEM+'_PREREGISTERED.json'));records=[];by_market={}
    for mid,data in base['markets'].items():
        source=read(ROOT/prereg['sources'][mid]['path']);actions=source['targetActions']
        first={};parent_meta={}
        def key(a):
            assert a['order_hash']
            return a['role'],a['side'],a['order_hash']
        for a in actions:first[key(a)]=min(a['event_ms'],first.get(key(a),a['event_ms']))
        for p in source['targetParents']:parent_meta[key(p)]=p
        assert set(first)==set(parent_meta)
        assert all(t==parent_meta[k]['first_event_ms'] for k,t in first.items())
        these=[]
        for pair in data['weak_strong_pairs']:
            legs=[a for a in actions if pair['strong_start']<=a['event_ms']<=pair['strong_end'] and a['side']==pair['strong']]
            assert legs
            old=[a for a in legs if first[key(a)]<pair['start']]
            new=[a for a in legs if first[key(a)]>=pair['start']]
            all_spend=math.fsum(a['shares']*a['price'] for a in legs)
            assert abs(all_spend-pair['strong_spend'])<1e-7
            new_spend=math.fsum(a['shares']*a['price'] for a in new)
            record=dict(pair,known_parent_spend=all_spend-new_spend,first_observed_parent_spend=new_spend,
                all_strong_legs_from_already_seen_parents=not new,all_strong_legs_first_observed_after_weak_start=not old,
                first_observed_only_spend_exceeds_preceding_gain=new_spend>pair['repair_gain']+1e-9)
            records.append(record);these.append(record)
        by_market[mid]=dict(all=summarize(these),
            all_strong_first_observed=summarize([r for r in these if r['all_strong_legs_first_observed_after_weak_start']]),
            first_observed_only_spend_exceeds_gain=sum(r['first_observed_only_spend_exceeds_preceding_gain'] for r in these))
    out=dict(status='COMPLETE',analysis='Posthoc partial-fill sensitivity; not a new preregistered hypothesis or source expansion.',
        source_result_sha256=sha(R/(STEM+'_RESULT.json')),all=summarize(records),
        known_parent_only=summarize([r for r in records if r['all_strong_legs_from_already_seen_parents']]),
        all_strong_first_observed=summarize([r for r in records if r['all_strong_legs_first_observed_after_weak_start']]),
        first_observed_only_spend_exceeds_gain=sum(r['first_observed_only_spend_exceeds_preceding_gain'] for r in records),
        by_market=by_market,rows=records,
        limits=['First observed fill is not order creation, submission or private intent. Previously unfilled pending parents remain unknown.',
            'Removing previously observed parent fills reduces one mechanical confound only; not proof of causal cash recycling.',
            'All original pairs retained; filtered summaries are sensitivity results, not population estimates.'])
    (R/(STEM+'_PARENT_SENSITIVITY.json')).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in out.items() if k not in ('rows','limits')}))


if __name__=='__main__':main()
