"""Observed response after exposure growth; no fitting, HFT or policy changes.

The full-episode peak is a descriptive witness only. Separate prefix-selected
anchors avoid selecting observations on their future recovery. Route deltas
are accounting attributions, never hidden order intent or causal ablations.
"""
import bisect
import collections
import json
import math
import time

from audit_btc5m_target_core_loop_topology_v1 import ROOT, R, MIDS, read, sha
from aggregate_btc5m_exposure_intent_ablation_v1 import get, RET
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary

STEM='BTC5M_POST_EXPOSURE_RESPONSE_V1_20260913'
SIDES=('UP','DOWN')
ROUTES=('MAKER','TAKER')
EPS=1e-8


def close(a,b):
    assert abs(a-b)<1e-7,(a,b)


def opposite(side):return 'DOWN' if side=='UP' else 'UP'


def strong(inv):
    return 'UP' if inv['UP']-inv['DOWN']>EPS else 'DOWN' if inv['DOWN']-inv['UP']>EPS else None


def empty_flow():
    return {s:{r:dict(qty=0.,cash=0.,legs=0) for r in ROUTES} for s in SIDES}


def sum_flow(rows):
    out=empty_flow()
    for s in SIDES:
        for route in ROUTES:
            for key in ('qty','cash','legs'):
                out[s][route][key]=math.fsum(r['flow'][s][route][key] for r in rows)
    return out


def reconstruct(legs):
    by=collections.defaultdict(list)
    for a in legs:
        assert a['side'] in SIDES and a['route'] in ROUTES and a['qty']>=0 and a['cash']>=0
        by[a['t']].append(a)
    inv=dict(UP=0.,DOWN=0.);cost=0.;rows=[]
    for t,batch in sorted(by.items()):
        before=dict(inv=inv.copy(),cost=cost,geometry=geometry(inv,cost))
        flow=empty_flow()
        for s in SIDES:
            for route in ROUTES:
                chosen=[a for a in batch if a['side']==s and a['route']==route]
                flow[s][route]=dict(qty=math.fsum(a['qty'] for a in chosen),cash=math.fsum(a['cash'] for a in chosen),legs=len(chosen))
            inv[s]+=math.fsum(flow[s][r]['qty'] for r in ROUTES)
            cost+=math.fsum(flow[s][r]['cash'] for r in ROUTES)
        rows.append(dict(t=t,inv=inv.copy(),cost=cost,geometry=geometry(inv,cost),before=before,flow=flow))
    return rows


def prefix_anchors(rows):
    selected=[];last=-math.inf
    for row in rows:
        side=strong(row['before']['inv'])
        if (side is not None and strong(row['inv'])==side and row['geometry']['loss']>EPS
            and row['geometry']['loss']>row['before']['geometry']['loss']+EPS
            and math.fsum(row['flow'][side][r]['qty'] for r in ROUTES)>EPS
            and row['t']-last>=30000):
            selected.append(row);last=row['t']
    return selected


def response(rows,anchor,end,seconds):
    assert end>anchor['t']
    finish=min(end,anchor['t']+int(seconds*1000))
    after=[r for r in rows if anchor['t']<r['t']<=finish]
    at_end=after[-1] if after else anchor
    side=strong(anchor['inv']);weak=opposite(side)
    flow=sum_flow(after)
    cash={s:math.fsum(flow[s][r]['cash'] for r in ROUTES) for s in SIDES}
    qty={s:math.fsum(flow[s][r]['qty'] for r in ROUTES) for s in SIDES}
    lift_by_route={r:flow[weak][r]['qty']-flow[weak][r]['cash'] for r in ROUTES}
    lift=math.fsum(lift_by_route.values());drag=cash[side]
    before_weak=anchor['inv'][weak]-anchor['cost'];after_weak=at_end['inv'][weak]-at_end['cost']
    after_strong=at_end['inv'][side]-at_end['cost']
    close(after_weak-before_weak,lift-drag)
    close(after_strong-(anchor['inv'][side]-anchor['cost']),qty[side]-cash[side]-cash[weak])
    for s in SIDES:close(at_end['inv'][s]-anchor['inv'][s],qty[s])
    close(at_end['cost']-anchor['cost'],math.fsum(cash.values()))
    deficit=max(0.,-before_weak)+drag
    service=lift/deficit if deficit>EPS else None
    if before_weak<0 and service is not None:close(service,1+after_weak/deficit)
    first={}
    for s in SIDES:
        first[s]={r:next(((a['t']-anchor['t'])/1000. for a in after if a['flow'][s][r]['qty']>EPS),None) for r in ROUTES}
    flips=[a['t'] for a in after if strong(a['inv'])!=side]
    recover={}
    for fraction in (.25,.5,1.):
        recover[str(fraction)]=next(((a['t']-anchor['t'])/1000. for a in after
            if a['inv'][weak]-a['cost']>=before_weak+(-before_weak)*fraction-EPS),None) if before_weak<-EPS else None
    net=at_end['inv'][side]-at_end['inv'][weak]
    path=path_summary(anchor,rows,anchor['t'],finish)
    next_weak=next((a for a in after if sum(a['flow'][weak][r]['qty'] for r in ROUTES)>EPS),None)
    return dict(requested_seconds=seconds,observed_seconds=(finish-anchor['t'])/1000.,complete_window=finish==anchor['t']+int(seconds*1000),
        weak_side=weak,strong_side=side,after_t=at_end['t'],endpoint=at_end['geometry'],
        weak_payoff_before=before_weak,weak_payoff_after=after_weak,strong_payoff_after=after_strong,
        weak_payoff_change=after_weak-before_weak,weak_payoff_recovered_fraction=(after_weak-before_weak)/(-before_weak) if before_weak<-EPS else None,
        weak_economic_lift=lift,weak_lift_by_route=lift_by_route,strong_acquisition_drag=drag,
        economic_service_fraction=service,net_retained_fraction=net/abs(anchor['geometry']['up_net']),
        ongoing_strong_acquisition=qty[side]>EPS,weak_acquisition=qty[weak]>EPS,
        both_routes_on_weak=all(flow[weak][r]['qty']>EPS for r in ROUTES),
        first_fill_seconds=first,first_weak_routes=[r for r in ROUTES if next_weak['flow'][weak][r]['qty']>EPS] if next_weak else [],
        first_recovery_seconds=recover,first_direction_loss_or_flip_seconds=(flips[0]-anchor['t'])/1000. if flips else None,
        weak_payoff_still_negative=after_weak<-EPS,direction_retained_entire_window=not flips,
        minimum_floor=path['minimum_floor'],negative_floor_area=path['negative_floor_area_currency_seconds'],
        flow=flow,subsequent_buckets=len(after),two_sided_buckets=sum(all(sum(a['flow'][s][r]['qty'] for r in ROUTES)>EPS for s in SIDES) for a in after))


def anchor_report(rows,a,start,end):
    return dict(t=a['t'],seconds=(a['t']-start)/1000.,state=a,
        windows={str(s):response(rows,a,end,s) for s in (5,15,30)},
        until_end=response(rows,a,end,(end-a['t'])/1000.))


def analyze(rows,start,end):
    assert all(start<=a['t']<end for a in rows)
    anchors=prefix_anchors(rows)
    # Every truncated prefix must give the same already-selected anchors.
    for i in range(1,len(rows)+1):
        assert [a['t'] for a in prefix_anchors(rows[:i])]==[a['t'] for a in anchors if a['t']<=rows[i-1]['t']]
    peak=max(rows,key=lambda r:r['geometry']['loss'])
    assert peak['geometry']['loss']>EPS
    return dict(terminal=rows[-1]['geometry'],peak=anchor_report(rows,peak,start,end),
        prefix_anchors=[anchor_report(rows,a,start,end) for a in anchors],curve=rows,
        prefix_selection_future_invariant=True)


def target(mid,expected):
    bundle='open_funding_recovery_train_20260911_v3' if mid in MIDS[:3] else 'v20_consumed_btc5_transfer5_20260912_v1'
    path=ROOT/'.lan_worker_v1'/bundle/f'input_{mid}.json.gz'
    assert sha(path)==expected['source_sha256']
    source=read(path);actions=source['targetActions']
    assert len({a['source_leg_id'] for a in actions})==len(actions)
    assert all(a['quote_type']=='BID' and 0<a['price']<1 and a['shares']>0 and a['event_ms']%1000==0 for a in actions)
    legs=[dict(t=a['event_ms'],side=a['side'],route=a['role'],qty=a['shares'],cash=a['shares']*a['price']) for a in actions]
    rows=reconstruct(legs)
    reverse=reconstruct(list(reversed(legs)))
    assert rows==reverse,'same-time source ordering affects aggregate accounting'
    assert len(rows)==len(expected['curve'])
    for actual,prior in zip(rows,expected['curve']):
        assert actual['t']==prior['t']
        for k in ('up','down','cost','inventory_up','inventory_down'):close(actual['geometry'][k],prior[k])
    out=analyze(rows,source['market']['window_start_ms'],source['market']['window_end_ms'])
    return dict(**out,source=str(path.relative_to(ROOT)),source_sha256=sha(path),source_legs=len(legs),
                timing='Target event_ms 1-second buckets; private receipt and order lifecycle unknown',fees='UNOBSERVED_EXCLUDED_AS_IN_PRIOR_GEOMETRY')


def own(tag):
    folder=RET/f'whole-oracle-repair-2026085-{tag}-20260913-v1'
    result,tr=get(folder);carriers={c['key']:c for c in tr['demand_final']['all_final_carriers']}
    assert all(c['fees']==0 for c in carriers.values())
    for r in tr['demand_final']['full_raw_receipts']:
        if r['qty']>EPS:close(r['contractPrice'],carriers[r['key']]['limit'])
    legs=[]
    for event in result['atomic_responsibility_events']:
        for fr in event['fill_rows']:
            if fr['fill_increment']<=EPS:continue
            c=carriers[fr['key']]
            legs.append(dict(t=event['t'],side=fr['side'],route='MAKER' if fr['route']=='PASSIVE' else 'TAKER',
                             qty=fr['fill_increment'],cash=fr['fill_increment']*c['limit']))
    rows=reconstruct(legs)
    states={s['t']:s for s in tr['states']}
    for row in rows:
        close(row['cost'],states[row['t']]['cost'])
        for s in SIDES:close(row['inv'][s],states[row['t']]['inv'][s])
    close(rows[-1]['cost'],result['final_cost'])
    out=analyze(rows,1788758100000,1788758400000)
    return dict(**out,source_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'),
                timing='Canonical OWN receipt observations; not identical private timing to Target event buckets',fees='NATIVE_ZERO_VERIFIED')


def summarize(items):
    by_window={}
    for window in ('5','15','30'):
        all_rows=[a['windows'][window] for item in items for a in item['prefix_anchors']]
        rows=[r for r in all_rows if r['complete_window']]
        improvements=[r for r in rows if r['weak_payoff_change']>EPS]
        by_window[window]=dict(complete_windows=len(rows),censored=len(all_rows)-len(rows),
            improved_weak_payoff=len(improvements),worsened_weak_payoff=sum(r['weak_payoff_change']<-EPS for r in rows),
            unchanged_weak_payoff=sum(abs(r['weak_payoff_change'])<=EPS for r in rows),
            improved_but_still_negative=sum(r['weak_payoff_still_negative'] for r in improvements),
            improved_with_same_direction=sum(r['direction_retained_entire_window'] for r in improvements),
            ongoing_strong_acquisition=sum(r['ongoing_strong_acquisition'] for r in rows),
            weak_acquisition=sum(r['weak_acquisition'] for r in rows),both_weak_routes=sum(r['both_routes_on_weak'] for r in rows),
            direction_loss_or_flip=sum(not r['direction_retained_entire_window'] for r in rows))
    return dict(markets=len(items),selected_anchors=sum(len(x['prefix_anchors']) for x in items),windows=by_window,
                warning='Descriptive repeated observations from consumed markets; not independent trials or causal effects')


def phase_context(target_item,own_item):
    # Follow-up diagnostic, declared after the initial response results: put both
    # peaks on the same market clock without pretending the two OWN states match.
    result={}
    for name,t in (('target_peak',target_item['peak']['t']),('own_peak',own_item['peak']['t'])):
        result[name]={}
        for who,item in (('target',target_item),('own',own_item)):
            rows=item['curve'];at=rows[bisect.bisect_right([a['t'] for a in rows],t)-1]
            anchor=dict(at,t=t)
            result[name][who]=dict(requested_t=t,state_last_changed_t=at['t'],state=at['geometry'],
                next30=response(rows,anchor,1788758400000,30))
    return dict(analysis_phase='EXPLORATORY_FOLLOW_UP_AFTER_INITIAL_RESPONSE_OUTPUT',
        purpose='Market phase and current geometry differ; no identical-state or causal speed comparison',anchors=result)


def self_test():
    legs=[dict(t=1000,side='UP',route='MAKER',qty=100.,cash=70.),dict(t=2000,side='UP',route='MAKER',qty=20.,cash=16.),
          dict(t=3000,side='DOWN',route='TAKER',qty=20.,cash=2.),dict(t=3000,side='UP',route='MAKER',qty=10.,cash=5.)]
    rows=reconstruct(legs);assert rows==reconstruct(list(reversed(legs)))
    assert [r['t'] for r in prefix_anchors(rows)]==[2000]
    r=response(rows,rows[1],4000,5)
    assert not r['complete_window'] and r['observed_seconds']==2
    close(r['weak_economic_lift'],18);close(r['strong_acquisition_drag'],5);close(r['weak_payoff_change'],13)
    assert r['weak_payoff_still_negative'] and r['ongoing_strong_acquisition']


def main():
    start=time.perf_counter();self_test()
    protocol=read(R/(STEM+'_PROTOCOL.json'))
    assert protocol['windows_seconds']==[5,15,30] and protocol['context_markets']==list(MIDS)
    previous=read(R/'BTC5M_EXPOSURE_SUPPRESSION_METRIC_V1_20260913.json')
    targets={str(mid):target(mid,previous['target_markets'][str(mid)]) for mid in MIDS}
    ours={tag:own(tag) for tag in ('capacity','repeat')}
    out=dict(version=STEM,status='COMPLETE',verification='PASS',protocol_sha256=sha(R/(STEM+'_PROTOCOL.json')),
        user_refinement=protocol['user_refinement'],target=targets,own=ours,
        target_prefix_summary=summarize(list(targets.values())),
        selected_market_prefix_summary=summarize([targets['2026085']]),
        own_prefix_summary={k:summarize([v]) for k,v in ours.items()},
        common_market_clock_context=phase_context(targets['2026085'],ours['repeat']),
        limits=protocol['measurement'],native_jobs=0,model_fits=0,policy_changes=False,
        verification_details=dict(target_previous_geometry_parity_all8=True,within_second_leg_order_invariance_all8=True,
            own_native_price_fee_and_state_reconciliation=True,all_economic_response_attribution_identifies=True,
            prefix_selection_does_not_use_future=True,censoring_and_concurrent_add_repair_examples=True),
        elapsed_seconds=time.perf_counter()-start)
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(out,indent=2,allow_nan=False,ensure_ascii=False)+'\n',encoding='utf-8')
    def brief(a):
        p=a['peak']
        return dict(peak_seconds=p['seconds'],peak_loss=p['state']['geometry']['loss'],
            windows={k:{f:v[f] for f in ('weak_payoff_change','weak_payoff_after','strong_payoff_after','net_retained_fraction','weak_lift_by_route','strong_acquisition_drag','first_recovery_seconds','complete_window')} for k,v in p['windows'].items()},
            until_end={f:p['until_end'][f] for f in ('weak_payoff_after','strong_payoff_after','net_retained_fraction','first_recovery_seconds')})
    print(json.dumps(dict(status='PASS',elapsed_seconds=out['elapsed_seconds'],target=brief(targets['2026085']),
        own={k:brief(v) for k,v in ours.items()},prefix_summary=out['target_prefix_summary'],
        selected_summary=out['selected_market_prefix_summary']),indent=2))


if __name__=='__main__':main()
