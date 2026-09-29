"""Explain the observed concurrency/quote interaction without new simulation."""
import collections
import json
import types
import verify_btc5m_active_repair_opportunity_v1 as previous
from prepare_btc5m_repair_constraints_v1 import R,ROOT,START,config,read,sha,get,RET,dump_for
from btc5m_exposure_suppression_metrics_v1 import geometry

STEM='BTC5M_REPAIR_CONSTRAINTS_INTERACTION_V1_20260913'


def main():
    a,at=get(RET/config('concurrent')['JOB']);n,nt=get(RET/config('both')['JOB'])
    rows={r['t']:r for r in nt['commitment_repair_rows']};matched=[];bins={}
    for arm in ('concurrent','quote','both'):
        audit=read(R/(config(arm)['STEM']+'_RESULT.json'));assert audit['verification']=='PASS'
        group={}
        for owner in audit['extra_orders']:
            cell=group.setdefault(str(owner['limit']),dict(orders=0,submitted_qty=0.,filled=0.,payment=0.))
            cell['orders']+=1;cell['submitted_qty']+=owner['qty'];cell['filled']+=owner['filled'];cell['payment']+=owner['payment']
        bins[arm]=group
    for op in at['commitment_repair_submissions']:
        if op['price']!=.08:continue
        row=rows[op['t']]
        matched.append(dict(t=op['t'],seconds=(op['t']-START)/1000,concurrent_submission=op,
            both_reason=row['reason'],both_payoff=row['state']['payoff'],
            both_net_up=row['state']['inv']['UP']-row['state']['inv']['DOWN'],
            both_pending=row['pending_qty'],both_current_candidate_price=row['price']))
    counts=dict(collections.Counter(r['both_reason'] for r in matched))
    assert len(matched)==37 and counts==dict(TICKET_ALONE_DOES_NOT_IMPROVE_CONFIRMED_FLOOR=37)
    assert all(r['both_current_candidate_price']==.08 and r['both_net_up']<0 for r in matched)
    left=geometry(a['final_inventory'],a['final_cost']);right=geometry(n['final_inventory'],n['final_cost'])
    delta={k:right[k]-left[k] for k in ('up','down','cost','up_net')}
    diagnose=types.FunctionType(previous.diagnose.__code__,dict(previous.diagnose.__globals__,STEM=STEM),'interaction_flow')
    flow=diagnose(dict(control=(a,at),active=(n,nt)),delta)
    fd=read(R/(STEM+'_FLOW_DIAGNOSTIC.json'))
    fd['interpretation']='Concurrent-only vs both fixes, same V24 demand and Active. Quote correction changes earlier fills and later state-dependent decisions. Not a cash budget or a direct limit on .08 prices.'
    dump_for(STEM,'FLOW_DIAGNOSTIC',fd)
    quote_audit=read(R/(config('quote')['STEM']+'_RESULT.json'))
    kept_keys={r['key'] for r in quote_audit['prevented_stale_cancels']}
    kept_owners=[o for o in quote_audit['extra_orders'] if o['key'] in kept_keys]
    out=dict(status='PASS',new_native_jobs=0,model_fits=0,overlay_price_bins=bins,
        concurrent_08_births_matched_to_both=matched,matched_reason_counts=counts,
        terminal_delta_both_minus_concurrent=delta,flow=flow,
        quote_only_retained_and_filled_owners=kept_owners,
        evidence_hashes={arm:dict(result=sha(RET/config(arm)['JOB']/'result.json'),trace=sha(RET/config(arm)['JOB']/'clock_trace.json.gz')) for arm in ('concurrent','quote','both')},
        finding='The combined branch did not clamp .08 to .07. Earlier .07 repairs changed filled inventory. At all 37 concurrent-only .08 birth clocks, combined current UP was the weaker payoff branch, so the retained ticket-alone floor gate blocked another DOWN. Subsequent UP fills increased downside again.',
        interpretation='Two structural corrections have non-additive path effects. The remaining issue is coordination of repair intensity, filled/pending branch protection, and later directional acquisition. Do not blindly remove the per-ticket floor rule or infer Target intent from this one path.',
        limitations='Matched-clock outcomes describe two realized paths after an earlier intervention; not isolated causal effects of each rejected order. Legal prices do not promise fills. Single consumed market only.')
    dump_for(STEM,'RESULT',out)
    print(json.dumps(dict(status='PASS',matched_08_births=len(matched),reasons=counts,terminal_delta=delta,
                         overlay_price_bins=bins,quote_maintenance_filled_owners=[o['key'] for o in kept_owners])))


if __name__=='__main__':main()
