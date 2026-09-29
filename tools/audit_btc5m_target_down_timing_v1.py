"""Recheck consumed 2028352 Target/OUR acquisition paths; never run a simulator."""
import bisect
import collections
import math
from pathlib import Path

from btc5m_partial_reexposure_experiment_v1 import (
    ROOT, R, START, BASE, BASE_JOB, JOB, read, sha, same, keyed_legs,
)
from audit_btc5m_post_exposure_response_v1 import reconstruct

STEM='BTC5M_TARGET_DOWN_TIMING_V1_20260913'
SOURCE=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2028352.json.gz'
SOURCE_SHA='26bc5fc72df030ea90a6ebaff2afa6772e6f158fc0bb5b635adcedc3e227c7ee'
WINDOWS=[(0,100),(100,150),(150,180),(180,190),(190,300),(0,188.843),(188.843,300),(0,300)]


def dump(tag,value):
    import json
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def flow(legs):
    q=math.fsum(x['qty'] for x in legs);c=math.fsum(x['cash'] for x in legs)
    return dict(qty=q,cash=c,vwap=c/q if q else None,branch_lift=q-c,
        first_t=min((x['t'] for x in legs),default=None),last_t=max((x['t'] for x in legs),default=None))


def state(legs,t):
    used=[x for x in legs if x['t']<=t]
    inv={s:math.fsum(x['qty'] for x in used if x['side']==s) for s in ('UP','DOWN')}
    c=math.fsum(x['cash'] for x in used)
    return dict(t=t,inv=inv,cost=c,payoff={s:inv[s]-c for s in inv},down_shares_per_total_cost=inv['DOWN']/c if c else None,
        down_shares_per_up_share=inv['DOWN']/inv['UP'] if inv['UP'] else None)


def window(legs,lo,hi):
    left=START+lo*1000;right=START+hi*1000
    rows=[x for x in legs if left<x['t']<=right]
    f={s:flow([x for x in rows if x['side']==s]) for s in ('UP','DOWN')}
    a=state(legs,left);b=state(legs,right)
    for s in f:same(b['inv'][s]-a['inv'][s],f[s]['qty'])
    same(b['cost']-a['cost'],sum(x['cash'] for x in f.values()))
    same(b['payoff']['DOWN']-a['payoff']['DOWN'],f['DOWN']['branch_lift']-f['UP']['cash'])
    return dict(lo=lo,hi=hi,flow=f,before=a,after=b,down_payoff_change=b['payoff']['DOWN']-a['payoff']['DOWN'],
        weak_lift_per_strong_cash=f['DOWN']['branch_lift']/f['UP']['cash'] if f['UP']['cash'] else None,
        by_route={s:{route:flow([x for x in rows if x['side']==s and x['route']==route]) for route in sorted({x['route'] for x in rows if x['side']==s})} for s in f})


def verify_target(source):
    actions=source['targetActions'];parents=source['targetParents']
    assert source['market']['market_id']==2028352 and source['market']['quality_status']=='COMPLETE_FORWARD_V1'
    assert len(actions)==810 and len(parents)==499
    assert len({a['source_leg_id'] for a in actions})==len(actions)
    assert all(a['market_id']==2028352 and a['quote_type']=='BID' and a['side'] in ('UP','DOWN') and a['role'] in ('MAKER','TAKER') and
        0<a['price']<1 and a['shares']>0 and START<a['event_ms']<START+300000 and a['event_ms']%1000==0 and a['observed_at_ms']>=a['event_ms'] for a in actions)
    key=lambda a:(a['role'],a['side'],a['quote_type'],a['order_hash'])
    grouped=collections.defaultdict(list)
    for a in actions:grouped[key(a)].append(a)
    assert len(grouped)==len(parents)==len({key(a) for a in parents})
    for p in parents:
        rows=grouped[key(p)]
        same(math.fsum(a['shares'] for a in rows),p['shares'])
        same(math.fsum(a['shares']*a['price'] for a in rows),p['shares']*p['average_price'])
        assert p['fill_legs']==len(rows) and p['first_event_ms']==min(x['event_ms'] for x in rows) and p['last_event_ms']==max(x['event_ms'] for x in rows)
    legs=[dict(t=a['event_ms'],side=a['side'],route=a['role'],qty=a['shares'],cash=a['shares']*a['price']) for a in actions]
    curve=reconstruct(legs);same(curve,reconstruct(list(reversed(legs))))
    label=read(R/'BTC5M_CORE_LOOP_TRANSFER_AB_V1_20260913_OFFLINE_LABELS.json')['labels']['2028352']
    terminal=state(legs,START+300000);same(terminal['inv'],label['final_inventory']);same(terminal['cost'],label['cost'])
    return legs,curve,dict(status='PASS',legs=810,parents=499,second_buckets=len(curve),parent_cost_qty_time_reconciled=True,
        source_leg_ids_unique=True,same_second_order_invariant=True,prior_offline_terminal_equal=True)


def book_summary(books,lo,hi,clock):
    parts=[]
    assert all(a[clock]<=b[clock] for a,b in zip(books,books[1:])),clock
    for i,b in enumerate(books):
        left=max(START+lo*1000,b[clock]);right=min(START+hi*1000,books[i+1][clock] if i+1<len(books) else START+300000)
        if right>left:
            parts.append(dict(seconds=(right-left)/1000,ask=1-b['best_bid'] if b['best_bid'] is not None else None,
                lag=(b['received_ms']-b['source_ms'])/1000))
    duration=math.fsum(x['seconds'] for x in parts)
    valid=[x for x in parts if x['ask'] is not None and 0<x['ask']<1]
    priced=math.fsum(x['seconds'] for x in valid)
    return dict(coverage_seconds=duration,missing_seconds=hi-lo-duration,valid_price_seconds=priced,missing_price_seconds=hi-lo-priced,
        mean_down_ask=math.fsum(x['seconds']*x['ask'] for x in valid)/priced if priced else None,
        time_weighted_update_transport_lag=math.fsum(x['seconds']*x['lag'] for x in parts)/duration if duration else None,
        ask_at_most_010_seconds=math.fsum(x['seconds'] for x in valid if x['ask']<=.100000001),
        ask_above_080_seconds=math.fsum(x['seconds'] for x in valid if x['ask']>.800000001))


def main():
    assert sha(SOURCE)==SOURCE_SHA
    dump('PROTOCOL',dict(status='RETROSPECTIVE_READ_ONLY',market=2028352,
        question='Was DOWN acquired too late relative to repair need, rather than merely a post-repair UP growth defect?',
        dedup='V29 was 2026085. V33 had only post-worst Target totals and V34 contrasted OUR managers. New: all 810 Target legs/499 parents, whole-path windows, normalized protection, clock sensitivity and rising-price Active evidence against both completed traces.',
        native_jobs=0,model_fits=0,parameter_search=0,actor_changes=0,figures=0,windows=WINDOWS,
        boundaries='Windows selected retrospectively, not a strategy clock. Target event seconds differ from OUR exchange/receipt/canonical clocks. Quantities and prices are actual observed fills, never hypothetical available liquidity or private NEW authority.'))
    source=read(SOURCE);target,curve,checks=verify_target(source)
    same(source['books'],read(BASE/'inputs/public_2028352.json.gz')['books'])
    paths={'TARGET':target};sources={};traces={};execution_clocks={}
    for name,j in [('V33',BASE_JOB),('V34',JOB)]:
        folder=R/'lan_worker_returns'/j;n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz')
        assert n['status']=='COMPLETE' and n['execution_accounting_valid'] and n['unresolved_owners']==0
        news={o['key']:dict(t=p['t'],**o) for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW'}
        legs=[dict(x,route={'PASSIVE':'MAKER','ACTIVE':'TAKER'}[x['route']]) for x in keyed_legs(n,tr,news)]
        paths[name]=legs;traces[name]=tr
        states={r['t']:r for r in tr['states']}
        for row in reconstruct(legs):same(row['inv'],states[row['t']]['inv']);same(row['cost'],states[row['t']]['cost'])
        sources[name]=dict(job=j,result_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'))
        exchange=[];receive=[]
        for r in tr['demand_final']['full_raw_receipts']:
            if r['qty']>0:
                side=news[r['key']]['side'];base=dict(side=side,qty=r['qty'],cash=r['qty']*r['contractPrice'],route='MAKER' if r['maker'] else 'TAKER')
                exchange.append(dict(base,t=r['exchange_ts']/1e6));receive.append(dict(base,t=r['receive_ts']/1e6))
        execution_clocks[name]={clock:{f'{lo}_{hi}':window(rows,lo,hi) for lo,hi in WINDOWS[:5]} for clock,rows in [('exchange',exchange),('receive',receive)]}
        same(state(exchange,START+300000)['inv'],state(legs,START+300000)['inv'])
        same(state(exchange,START+300000)['cost'],state(legs,START+300000)['cost'])
    summary={name:dict(windows={f'{lo}_{hi}':window(legs,lo,hi) for lo,hi in WINDOWS},
        checkpoints={str(s):state(legs,START+s*1000) for s in (100,150,172,180,188.843,190,240,300)}) for name,legs in paths.items()}
    # Uniform whole-series shifts check sensitivity without treating the shift as a latency estimate.
    sensitivity={name:{str(shift):{f'{lo}_{hi}':window([dict(r,t=r['t']+shift*1000) for r in legs],lo,hi)
        for lo,hi in [(100,150),(180,190)]} for shift in (-3,0,3)} for name,legs in paths.items()}
    target_events=[]
    for sec in range(150,191):
        actions=[a for a in source['targetActions'] if a['event_ms']==START+sec*1000]
        if not actions:continue
        groups=collections.defaultdict(list)
        for a in actions:groups[(a['side'],a['role'])].append(a)
        target_events.append(dict(seconds=sec,groups=[dict(side=k[0],route=k[1],legs=len(v),parents=len({x['order_hash'] for x in v}),
            qty=math.fsum(x['shares'] for x in v),cash=math.fsum(x['shares']*x['price'] for x in v),
            minimum_price=min(x['price'] for x in v),maximum_price=max(x['price'] for x in v)) for k,v in groups.items()]))
    owner_diagnostic={}
    for name,tr in traces.items():
        ops=[dict(t=p['t'],**o) for p in tr['plans'] if START+180000<p['t']<=START+188000 for o in p['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
        final={r['key']:r for r in tr['demand_final']['all_final_carriers']}
        for o in ops:o['final_owner']=final[o['key']]
        checkpoint=next(r for r in tr['commitment_repair_rows'] if r['t']>=START+181000)
        owner_diagnostic[name]=dict(rising_window_down_new=ops,checkpoint=checkpoint,
            active_submissions=tr['opportunity_submissions']+tr['coordination_submissions'],
            last_down_canonical_before_rise=max(x['t'] for x in paths[name] if x['side']=='DOWN' and x['t']<START+180000),
            first_down_canonical_after_180=min(x['t'] for x in paths[name] if x['side']=='DOWN' and x['t']>START+180000))
        assert all(o['final_owner']['filled']==0 for o in ops),name
    report=dict(status='COMPLETE',verification='PASS',source_sha256=SOURCE_SHA,target_checks=checks,our_sources=sources,
        summary=summary,target_full_curve=curve,target_rising_events=target_events,our_order_diagnostic=owner_diagnostic,
        execution_clock_sensitivity=execution_clocks,uniform_shift_sensitivity=sensitivity,
        books={clock:{f'{lo}_{hi}':book_summary(source['books'],lo,hi,clock) for lo,hi in WINDOWS[:5]} for clock in ('source_ms','received_ms')},
        limitations=['Observed Target buys from zero; unobserved fees/rebates and private initial balances excluded.',
            'Target event times are second buckets; fills are not placement times. Private queue/pending/cancel state is unknown.',
            'A shared wall clock is not a shared inventory, capital scale, queue position or available top-level depth.',
            'Target also buys expensive DOWN later and ends both branches negative in this case.',
            'No chosen Target quantity, timing, price or final outcome is added to an actor. No new native run.'],
        next='Prioritize realized DOWN protection versus contemporaneous UP spending and the maker/active transition during rising prices. Retain the V34 UP cost finding, but do not isolate it as the only cause.',
        native_jobs=0,worker_calls=0,model_fits=0,parameter_search=0,actor_changes=0,figures=0)
    dump('RESULT',report)
    dump('PROGRESS',dict(status='COMPLETE',verification='PASS',target_legs=810,target_parents=499,
        reused_jobs=[BASE_JOB,JOB],native_jobs=0,actor_changes=0,next_candidate_frozen=False,next_native_submitted=False))
    import json
    print(json.dumps(dict(status='PASS',checks=checks,comparison={name:{k:summary[name]['windows'][k] for k in ('100_150','180_190')} for name in paths})))


if __name__=='__main__':main()
