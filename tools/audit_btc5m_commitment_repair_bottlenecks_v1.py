"""Post-run read-only replacement and isolated-gate attribution; no replay."""
import collections
import json
from prepare_btc5m_commitment_repair_probe_v1 import ROOT,R,BASE,JOB,STEM,START,get,RET,read,sha,dump
from btc5m_commitment_repair_probe_v1 import decide
from hft244_pair_route_legality_v1 import crossing_owners


def main():
    b,bt=get(BASE);n,nt=get(RET/JOB)
    result=read(R/(STEM+'_RESULT.json'));assert result['verification']=='PASS'
    counts=collections.Counter();owner_rows=[];price_rows=[]
    for row in nt['commitment_repair_rows']:
        scenario=None
        if row['reason']=='WAIT_FOR_EXTRA_OWNER_TERMINAL':
            scenario=decide(row['state'],row['original_operations'],row['price'],row['ask'],row['active_confirmed'],[],crossing_owners)
            label='REMOVE_ONE_EXTRA_OWNER_LIMIT';bucket=owner_rows
        elif row['reason']=='FIXED15_NEW_PRICE_INVALID':
            scenario=decide(row['state'],row['original_operations'],.07,row['ask'],row['active_confirmed'],row['outstanding'],crossing_owners)
            label='MINIMUM_LEGAL_FIXED15_PRICE';bucket=price_rows
        if scenario is not None:
            counts[label+':'+scenario['reason']]+=1
            if scenario['eligible']:
                bucket.append(dict(t=row['t'],seconds=(row['t']-START)/1000,original_reason=row['reason'],
                    old_price=row['price'],ask=row['ask'],outstanding=row['outstanding'],recheck=scenario))
    assert len(owner_rows)==89 and len(price_rows)==47
    owner='DOWN_605'
    maintenance=[x for x in nt['demand_maintenance_rows'] if x['key']==owner]
    canceled=next(x for x in maintenance if x['cancellable'] and x['stale'])
    assert canceled['t']==START+261171 and not canceled['surplus'] and canceled['current_passive_price']==.05
    plan=next(p for p in nt['plans'] if p['t']==canceled['t'])
    assert any(o['kind']=='CANCEL' and o['key']==owner for o in plan['operations'])
    row=next(x for x in nt['commitment_repair_rows'] if x['t']==canceled['t'])
    legal_recheck=decide(row['state'],row['original_operations'],.07,row['ask'],True,[],crossing_owners)
    assert legal_recheck['eligible']
    replacement=[]
    for t in (START+224412,START+224778):
        old_plan=next(p for p in bt['plans'] if p['t']==t)
        new_plan=next(p for p in nt['plans'] if p['t']==t)
        old_down=[o for o in old_plan['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
        new_down=[o for o in new_plan['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
        old_state=next(d['state'] for d in bt['demand_rows'] if d['t']==t)
        new_state=next(d['state'] for d in nt['demand_rows'] if d['t']==t)
        old_gap=max(0.,old_state['inv']['UP']-old_state['inv']['DOWN']-old_state['pending_qty']['DOWN'])
        new_gap=max(0.,new_state['inv']['UP']-new_state['inv']['DOWN']-new_state['pending_qty']['DOWN'])
        replacement.append(dict(t=t,seconds=(t-START)/1000,baseline_down_new=old_down,candidate_down_new=new_down,
            baseline_gap=old_gap,candidate_gap=new_gap,
            baseline_owners=[c for c in bt['demand_final']['all_final_carriers'] if c['key'] in {o['key'] for o in old_down}]))
    assert len(replacement[0]['baseline_down_new'])==len(replacement[0]['candidate_down_new'])==1
    assert len(replacement[1]['baseline_down_new'])==1 and not replacement[1]['candidate_down_new']
    assert replacement[1]['baseline_gap']>=15>replacement[1]['candidate_gap']
    flow=read(R/(STEM+'_FLOW_DIAGNOSTIC.json'))['flow_changes']
    assert len(flow)==3 and abs(sum(x['difference']['qty'] for x in flow))<1e-8
    assert abs(sum(x['difference']['cash'] for x in flow))<1e-8
    assert result['terminal_delta']==dict(up=0.,down=0.,cost=0.,up_net=0.)
    metrics={tag:{k:result[tag]['trajectory'][k] for k in ('both_positive_seconds','negative_floor_area_currency_seconds',
            'reversed_net_seconds','minimum_floor')} for tag in ('baseline','candidate')}
    delta={k:metrics['candidate'][k]-metrics['baseline'][k] for k in metrics['baseline']}
    out=dict(status='PASS',native_jobs_added=0,model_fits=0,source_result_sha256=sha(RET/JOB/'result.json'),
        source_trace_sha256=sha(RET/JOB/'clock_trace.json.gz'),flow_changes=flow,replacement_frontiers=replacement,
        path_metrics=metrics,path_delta=delta,
        isolated_gate_counts=dict(counts),owner_limit_rechecks=owner_rows,legal_fixed15_price_rechecks=price_rows,
        unfilled_extra_maintenance=dict(owner=owner,events=maintenance,cancel=canceled,current_price_recheck=legal_recheck),
        interpretation='15 shares were filled about 60 seconds earlier, then offset by 15 fewer at 242.759. The second extra owner is economically equivalent to a baseline 224.412 birth. Third extra was cancelled as stale without a fill. The recurrent demand is still serialized by its one-extra-owner experimental restriction.',
        proposed_next='One bounded change to extra-owner concurrency, preserving current-commitment demand and legal reservations; separately test minimum-legal quote and maintenance consistency later.',
        limitations='These are alternative decisions evaluated on already realized V24 states, not alternative fill trajectories. 89 and 47 are observation-frame counts, not independent opportunities, proposed order counts, or predicted profit. Zero-relative target is not identified. Preserving a moving floor is invariant to translating both payoff branches and does not learn an absolute terminal payoff objective.')
    dump('BOTTLENECKS',out)
    print(json.dumps(dict(status='PASS',owner_limit_eligible_frames=len(owner_rows),legal_price_eligible_frames=len(price_rows),
                         path_delta=delta,unfilled_extra_cancel_seconds=(canceled['t']-START)/1000)))


if __name__=='__main__':main()
