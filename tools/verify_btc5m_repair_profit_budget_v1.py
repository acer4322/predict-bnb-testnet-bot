"""Read-only budget, receipt and response validation of two frozen native arms."""
import argparse
import bisect
import collections
import json
import math
from aggregate_btc5m_exposure_intent_ablation_v1 import get,ROOT,R,RET,STREAMS
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS
from audit_btc5m_target_core_loop_topology_v1 import read,sha
from verify_btc5m_whole_oracle_repair_v1 import audit_receipts,close,stop_reason
from audit_btc5m_post_exposure_response_v1 import reconstruct,analyze
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary

STEM='BTC5M_REPAIR_PROFIT_BUDGET_V1_20260913'
PACKAGE=ROOT/'.lan_worker_v1/repair_profit_budget_2026085_20260913_v1'


def audit(tag,result,tr):
    retention=0. if tag=='control' else .5
    assert result['clock_smoke']['profit_retention']==retention
    assert result['clock_smoke']['manifest_sha256']==sha(PACKAGE/'manifest.json')
    assert result['clock_smoke']['actual_replay_frames']==1462 and result['worker'].upper()=='DESKTOP-JIERAGF'
    assert result['execution_accounting_valid'] and result['unresolved_owners']==0 and result['active_native_submits']==0
    assert result['oracle_direction']=='ORACLE_UP' and result['runtime_eligible'] is False
    receipts=audit_receipts(result,tr)
    carriers={r['key']:r for r in tr['demand_final']['all_final_carriers']}
    legs=[]
    for e in result['atomic_responsibility_events']:
        for f in e['fill_rows']:
            if f['fill_increment']>1e-8:legs.append(dict(t=e['t'],side=f['side'],route='MAKER',qty=f['fill_increment'],cash=f['fill_increment']*carriers[f['key']]['limit']))
    rows=reconstruct(legs);states={s['t']:s for s in tr['states']}
    paid=dict(UP=0.,DOWN=0.);paid_rows=[(0,dict(paid))]
    for row in rows:
        close(row['cost'],states[row['t']]['cost'])
        for s in paid:
            close(row['inv'][s],states[row['t']]['inv'][s])
            paid[s]+=sum(row['flow'][s][r]['cash'] for r in ('MAKER','TAKER'))
        if retention:assert paid['DOWN']<=.5*(row['inv']['UP']-paid['UP'])+1e-7
        paid_rows.append((row['t'],dict(paid)))
    reduced=[];old_down=[r for r in tr['money_rows'] if r['side']=='DOWN']
    assert len(old_down)==len(tr['profit_budget_rows'])
    paid_times=[a[0] for a in paid_rows]
    for row,old in zip(tr['profit_budget_rows'],old_down):
        assert row['t']==old['t'];close(row['old_quantity_cap'],old['capped'])
        actual_paid=paid_rows[bisect.bisect_right(paid_times,row['t'])-1][1]
        for s in actual_paid:close(row['paid'][s],actual_paid[s])
        close(row['confirmed_up_gross'],row['inv']['UP']-row['paid']['UP'])
        room=max(0.,(1-retention)*row['confirmed_up_gross']-row['paid']['DOWN']-row['pending_down_cash'])
        close(row['available_cash'],room)
        expected=min(row['old_quantity_cap'],max(0.,round(math.floor((room/row['price']+1e-10)/.01)*.01,8))) if retention else row['old_quantity_cap']
        close(expected,row['admitted'])
        if retention:assert row['admitted']*row['price']<=room+1e-7
        if row['admitted']<row['old_quantity_cap']-1e-8:reduced.append(row)
    first_by_time={}
    for row in tr['profit_budget_rows']:first_by_time.setdefault(row['t'],row)
    for plan in tr['plans']:
        down=[o for o in plan['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
        if down and retention:assert sum(o['price']*o['qty'] for o in down)<=first_by_time[plan['t']]['available_cash']+1e-7
    # Candidate-frame draft reservations, including pending cancellations, are
    # checked above; independently verify actual snapshots throughout the episode.
    observed_checks=0
    for row in tr['demand_rows']:
        s=row['state'];paid_at=paid_rows[bisect.bisect_right(paid_times,row['t'])-1][1]
        if retention:assert paid_at['DOWN']+s['pending_cash']['DOWN']<=.5*(s['inv']['UP']-paid_at['UP'])+1e-7
        observed_checks+=1
    terminal=geometry(result['final_inventory'],result['final_cost']);gross=result['final_inventory']['UP']-paid['UP']
    close(terminal['up'],gross-paid['DOWN'])
    path=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),tr['states'],1788758100000,1788758400000)
    response=analyze(rows,1788758100000,1788758400000)
    works=tr['demand_final']['works']
    for work in works:
        initial=work['initial_state']
        close(work['target'],initial['inv']['DOWN']+initial['pending_qty']['DOWN']+30.)
        if work['ended_t'] is not None:
            ended=next(s for s in tr['states'] if s['t']>=work['born_t'] and stop_reason(s,work['target']))
            assert ended['t']==work['ended_t'] and stop_reason(ended,work['target'])==work['status']
    last_budget=tr['profit_budget_rows'][-1]
    last_minimum=max(18.,1./last_budget['price'])
    return dict(terminal=terminal,paid_by_side=paid,up_acquisition_gross=gross,up_profit_retention=terminal['up']/gross,
        trajectory=path,post_exposure=response,receipts=receipts,
        budget=dict(retention=retention,rows=len(old_down),reduced=len(reduced),first_reduction=reduced[0] if reduced else None,
                    canonical_reservation_checks=observed_checks,last_candidate=last_budget,
                    terminal_remaining_cash_if_budget_enabled=max(0.,(1-retention)*gross-paid['DOWN']),
                    last_candidate_minimum_shares=last_minimum,
                    last_candidate_original_ticket_below_minimum=last_budget['requested']<last_minimum-1e-8,
                    last_candidate_budget_cannot_fund_minimum=last_budget['available_cash']<last_minimum*last_budget['price']-1e-8),
        works=works,work_status_counts=dict(collections.Counter(w['status'] for w in works)),
        unfinished_work_ids=[w['id'] for w in works if w['ended_t'] is None],
        events=len(result['atomic_responsibility_events']),submits=result['submits'],core_similarity=result['core_similarity'],
        final_pending_cash=result['clock_smoke']['final_pending_cash_direct'],native_safety=result['safety_gate'])


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--control-only',action='store_true');args=ap.parse_args()
    m=read(PACKAGE/'manifest.json');assert m==read(R/(STEM+'_PREREGISTERED.json'))
    assert all(sha(PACKAGE/n)==h for n,h in m['files'].items())
    old,ot=get(RET/'whole-oracle-repair-2026085-repeat-20260913-v1')
    control,ct=get(RET/'repair-profit-budget-2026085-control-20260913-v1')
    parity={k:old[k]==control[k] for k in PARITY_FIELDS}
    parity.update({k:ot[k]==ct[k] for k in (*STREAMS,'intent','money_rows','demand_events','demand_rows','demand_final')})
    assert all(parity.values()),parity
    out=dict(status='CONTROL_PASS',parity=parity,native_jobs=1)
    (R/(STEM+'_CONTROL.json')).write_text(json.dumps(out,indent=2)+'\n')
    if args.control_only:print(json.dumps(out));return
    half,ht=get(RET/'repair-profit-budget-2026085-half-20260913-v1')
    assert half['theta']==control['theta']
    first=next(a['t'] for a,b in zip(ct['plans'],ht['plans']) if a!=b)
    prefix={k:[a for a in ct[k] if a['t']<first]==[a for a in ht[k] if a['t']<first] for k in (*STREAMS,'intent','money_rows')}
    assert all(prefix.values()),prefix
    arms={tag:audit(tag,r,t) for tag,r,t in (('control',control,ct),('half',half,ht))}
    out.update(status='COMPLETE',verification='PASS',native_jobs=2,local_native_jobs=0,model_fits=0,
        first_plan_difference_t=first,prefix=prefix,arms=arms,scope=m['selection_scope'])
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(json.dumps({tag:dict(terminal=a['terminal'],paid=a['paid_by_side'],retention=a['up_profit_retention'],
        area=a['trajectory']['negative_floor_area_currency_seconds'],worst=a['trajectory']['minimum_floor'],
        work_status=a['work_status_counts'],unfinished=a['unfinished_work_ids'],submits=a['submits']) for tag,a in arms.items()},indent=2))


if __name__=='__main__':main()
