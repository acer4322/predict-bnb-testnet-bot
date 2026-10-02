"""Offline V39 Target/model path report; no native calls, fits or charts."""
import bisect
import collections
import json
import math

from btc5m_success_case_benchmark_v1 import ROOT, R, STEM, BASE, PACKAGE, SOURCE, TARGET, SELECTED, read, sha, dump, jobs, worker
from audit_btc5m_price_retention_casepanel_v1 import load_paths, measure
from audit_btc5m_post_exposure_response_v1 import reconstruct
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs


def summary(rows, final_side):
    """Integrate observed step states on [0,300]; never interpolate fills."""
    start = dict(t=0, inv=dict(UP=0., DOWN=0.), cost=0.)
    states = [start, *rows]
    terminal = states[-1]; cost = terminal['cost']
    area = 0.; positive_seconds = 0.; direction_seconds = collections.Counter()
    signs = []; flips = []; prev = None
    for i, row in enumerate(states):
        left = max(0., row['t']/1000)
        right = min(300., states[i+1]['t']/1000 if i+1 < len(states) else 300.)
        dt = max(0., right-left)
        u, d = row['inv']['UP']-row['cost'], row['inv']['DOWN']-row['cost']
        area += max(0., -min(u, d))*dt
        positive_seconds += dt if min(u, d) > 1e-8 else 0.
        sign = 'UP' if u>d+1e-8 else 'DOWN' if d>u+1e-8 else 'BALANCED'
        direction_seconds[sign] += dt
        if sign != 'BALANCED':
            if prev is not None and sign != prev: flips.append(dict(seconds=left, side=sign))
            signs.append(sign); prev = sign
    for s in ('UP','DOWN'):
        assert abs(sum(row['flow'][s][r]['qty'] for row in rows for r in ('MAKER','TAKER'))-terminal['inv'][s]) < 1e-7
    assert abs(sum(row['flow'][s][r]['cash'] for row in rows for s in ('UP','DOWN') for r in ('MAKER','TAKER'))-cost) < 1e-7
    pu = terminal['inv']['UP']-cost; pd = terminal['inv']['DOWN']-cost
    gain = max(0.,pu,pd); loss = max(0.,-min(pu,pd))
    checkpoints = {}
    for sec in (0,50,100,150,200,250,300):
        row = next(r for r in reversed(states) if r['t'] <= sec*1000)
        checkpoints[str(sec)] = dict(inv=row['inv'], cost=row['cost'],
            payoff={s:row['inv'][s]-row['cost'] for s in ('UP','DOWN')})
    worst = min(states, key=lambda r:min(r['inv'].values())-r['cost'])
    weak = 'DOWN' if final_side=='UP' else 'UP'
    after = [r for r in rows if r['t'] > worst['t']]
    repair = math.fsum(r['flow'][weak][rt]['qty']-r['flow'][weak][rt]['cash'] for r in after for rt in ('MAKER','TAKER'))
    drag = math.fsum(r['flow'][final_side][rt]['cash'] for r in after for rt in ('MAKER','TAKER'))
    positive = [r for r in rows if min(r['inv'].values())-r['cost'] > 1e-8]
    last_positive = positive[-1] if positive else None
    protection = None
    if last_positive:
        tail = [r for r in rows if r['t'] > last_positive['t']]
        flow = {s:dict(qty=math.fsum(r['flow'][s][rt]['qty'] for r in tail for rt in ('MAKER','TAKER')),
            cash=math.fsum(r['flow'][s][rt]['cash'] for r in tail for rt in ('MAKER','TAKER'))) for s in ('UP','DOWN')}
        changes = {s:flow[s]['qty']-sum(x['cash'] for x in flow.values()) for s in flow}
        before = {s:last_positive['inv'][s]-last_positive['cost'] for s in flow}
        for s in flow: assert abs(before[s]+changes[s]-(terminal['inv'][s]-cost))<1e-7
        protection = dict(last_positive_fill_seconds=last_positive['t']/1000,before=before,
            subsequent_flow=flow,delta_payoff=changes, interpretation='Post-hoc diagnostic, not a policy trigger')
    return dict(terminal=dict(up=pu,down=pd,cost=cost,inventory=terminal['inv'],
            loss_to_gain=loss/gain if gain else None, signed_lower_over_higher=min(pu,pd)/max(pu,pd) if max(pu,pd)>0 else None,
            up_per_cost=pu/cost if cost else None, down_per_cost=pd/cost if cost else None),
        negative_floor_area=area, negative_floor_area_per_final_cost=area/cost if cost else None,
        both_positive_seconds=positive_seconds, direction_seconds=dict(direction_seconds), net_direction_flips=flips,
        checkpoints=checkpoints, first_fill_seconds=rows[0]['t']/1000, last_fill_seconds=rows[-1]['t']/1000,
        after_last_both_positive=protection,
        first_both_positive_seconds=next((r['t']/1000 for r in rows if min(r['inv'].values())-r['cost']>1e-8),None),
        post_worst=dict(seconds=worst['t']/1000, floor=min(worst['inv'].values())-worst['cost'],
            fixed_terminal_weak_side=weak, weak_acquisition_lift=repair, other_side_cost=drag,
            accounting_interpretation='Side labels fixed by final direction, even if dynamic inventory direction changes'),
        flow={s:{rt:dict(qty=math.fsum(r['flow'][s][rt]['qty'] for r in rows),
            cash=math.fsum(r['flow'][s][rt]['cash'] for r in rows),
            last_seconds=max((r['t']/1000 for r in rows if r['flow'][s][rt]['qty']>0),default=None))
            for rt in ('MAKER','TAKER')} for s in ('UP','DOWN')})


def relative_path_error(target, ours):
    """Descriptive shape error normalized by each path's own final cost, scoring only."""
    grid = sorted({0,300000,*[r['t'] for r in target if 0<=r['t']<=300000],*[r['t'] for r in ours if 0<=r['t']<=300000]})
    paths = []
    for rows in (target,ours):
        paths.append(([r['t'] for r in rows], rows, rows[-1]['cost']))
    area = dict(UP=0.,DOWN=0.)
    for t,nxt in zip(grid,grid[1:]):
        vectors=[]
        for times,rows,cost in paths:
            i=bisect.bisect_right(times,t)-1
            state=rows[i] if i>=0 else dict(inv=dict(UP=0.,DOWN=0.),cost=0.)
            vectors.append({s:(state['inv'][s]-state['cost'])/cost if cost else 0. for s in area})
        for s in area: area[s]+=abs(vectors[0][s]-vectors[1][s])*(nxt-t)/1000
    return dict(normalized_payoff_absolute_error_area=area,
        mean_absolute_error={s:v/300 for s,v in area.items()},
        interpretation='Post-hoc own-final-cost normalization for path shape only; raw costs/payoffs retained; no learned success cutoff')


def main():
    paths,metadata=load_paths();old={x['market_id']:x for x in read(TARGET)['paths']}
    actions=[json.loads(l) for l in (SOURCE/'target_actions.jsonl').read_text(encoding='utf-8').splitlines()]
    parents=[json.loads(l) for l in (SOURCE/'target_parents.jsonl').read_text(encoding='utf-8').splitlines()]
    targets={}
    for mid in SELECTED:
        rows=paths[mid];side=old[mid]['terminal']['normalSide'];z=measure(rows)
        aa=[x for x in actions if x['market_id']==mid];pp=[x for x in parents if x['market_id']==mid and x['role']=='MAKER']
        low=[x for x in aa if x['role']=='MAKER' and x['price']<.07-1e-8]
        targets[str(mid)]=dict(summary=summary(rows,side), final_observed_net_side=side,
            recorded_fill_legs=old[mid]['recorded_fill_legs'], event_seconds=old[mid]['event_second_buckets'],
            prior_findings_reused=dict(counts=z['counts'],last_bucket_mechanism=old[mid]['last_bucket_mechanism'],
                final_one_sided_run=old[mid]['final_one_sided_run']),
            sizing=dict(exported_raw_available=bool(aa),maker_filled_parent_size_modes=collections.Counter(round(p['shares'],8) for p in pp).most_common(5),
                maker_legs_below_fixed15_new_price_floor=len(low) if aa else None,
                maker_qty_below_floor=math.fsum(x['shares'] for x in low) if aa else None,
                maker_cash_below_floor=math.fsum(x['shares']*x['price'] for x in low) if aa else None,
                low_price_legs=[{k:x[k] for k in ('event_ms','side','price','shares','order_hash')} for x in low],
                warning='Observed fills/filled-parent totals are not original NEW sizes. No fixed15 change; low-price passive opportunities cannot be copied.'),
            source_rows_sha256=old[mid]['source_rows_sha256'])
    dump('TARGET_REFERENCE',dict(status='PASS',targets=targets,source_sha256=sha(TARGET),
        reuses='V22/V30 verified observations; not new discovery counts', target_runtime_input=False))
    w=worker();completed={};pending=[]
    for j in jobs():
        ap=w.artifact(j,'AUDIT');cp=w.artifact(j,'CONTINUATION_AUDIT')
        if not ap.exists() or not cp.exists(): pending.append(j['job_id']);continue
        au=read(ap);assert au['execution_status']=='PASS' and read(cp)['status']=='PASS'
        folder=R/'lan_worker_returns'/j['job_id'];n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz')
        inp=read(PACKAGE/f'inputs/public_{j["market"]}.json.gz');start=inp['market']['window_start_ms']
        legs=canonical_legs(n,tr)
        for x in legs:x['t']-=start
        rows=reconstruct(legs)
        side=old[j['market']]['terminal']['normalSide']
        completed[j['job_id']]=dict(market=j['market'],arm=j['arm'], selected_direction=au['selected_direction'],
            execution='PASS', summary=summary(rows,side), comparison=relative_path_error(paths[j['market']],rows),
            activity=au['structural_activity'], active_submits=au['active_submits'],
            native_seconds=au['native_elapsed_seconds'], new_orders=au['submits'], source_frames=au['frames'],
            missing_terminal_clocks=au['terminal_timing']['missing_owner_observation_clocks'],
            result_sha256=sha(folder/'result.json'), trace_sha256=sha(folder/'clock_trace.json.gz'),
            active_events=tr['opportunity_submissions']+tr['coordination_submissions'],
            coordination_episode=tr['coordination_episode'],
            route_reason_counts={key:dict(collections.Counter(r['reason'] for r in tr[key])) for key in ('opportunity_rows','coordination_rows')},
            growth_anchor=tr['addition_growth_rows'][-1]['anchor'])
    pairs={}
    for mid in (1977248,1942969):
        pair=[j for j in jobs() if j['market']==mid]
        if all(j['job_id'] in completed for j in pair):
            tt=[read(R/'lan_worker_returns'/j['job_id']/'clock_trace.json.gz') for j in pair]
            pairs[str(mid)]={key:tt[0][key]==tt[1][key] for key in ('plans','states','native_actions')}
    result=dict(status='COMPLETE' if not pending else 'PARTIAL',verification='PASS' if not pending else 'PENDING',
        targets=targets,completed=completed,pending_jobs=pending,paired_path_equality=pairs,
        native_jobs_completed=len(completed),native_total_seconds=sum(x['native_seconds'] for x in completed.values()),
        model_fits=0,parameter_search=0,policy_changes=0,local_native_jobs=0,figures=0,
        candidate_manifest_sha256=sha(PACKAGE/'manifest.json'),parent_manifest_sha256=sha(BASE/'manifest.json'),
        failure_control=dict(market=2028352,rerun=False,source='BTC5M_V38_VS_TARGET_2028352_PATHS_20260913.json',
            source_sha256=sha(R/'BTC5M_V38_VS_TARGET_2028352_PATHS_20260913.json')),
        boundary='Successful outcome-selected development cases; matched known-final-net-bit control versus inherited OWN direction latch. No unseen graduation or complete learning claim.')
    if not pending:
        result.update(learning_status='TARGET_CORE_LOOP_NOT_REPRODUCED_ON_SELECTED_SUCCESS_CASES',
            findings=[
                '1977248 known-DOWN reaches both-positive temporarily, then later DOWN acquisition cost exceeds realized UP repair lift; finite quantity work completion does not establish protected economic completion.',
                '1977248 Target weak-side UP repair is entirely Maker; its Active fills add the strong DOWN side. OUR uses 3 weak-side Active and no strong Active. Routes are not yet Target-like.',
                '1977248 Target has 415 passive shares for 18.2 cash below .07. Fixed15 NEW cannot rest there; this is a joint sizing/route-domain limitation, not a reason to copy old fills or relax min-notional.',
                '1942969 Target has zero Maker fills below .07, 196 seconds both-positive and 4 observed net sign changes. OUR remains UP, achieves no both-positive time, and first Active repair fills near198 seconds.',
                '1942969 known/no-direction plans, states and native actions match; both latched UP. This is no evidence of learned direction alpha.',
                '1977248 no-direction latches UP rather than Target final DOWN. A favorable gain/loss ratio can still describe the wrong side and large absolute loss; orientation and raw branch risk must accompany the ratio.'
            ],
            benchmark_roles=dict(primary='1977248 known final DOWN: Target direction stable; isolate repair and readdition first',
                extension='1942969: both-positive protection spending and dynamic net reversal; final net side is not a constant private intention',
                offline_reference='2084104: successful Target path; incomplete public tape excludes native',
                failure_control='2028352: existing V38 result only; no rerun'),
            next_scope='On 1977248 known-DOWN, diagnose confirmed repair followed by renewed addition, continuing repair demand and low-price execution capacity. Use actual pending/terminal receipts for bounded local sequence checks before a single new policy intervention. Do not only increase Active count or force all weak payoffs to zero.',
            next_candidate_frozen=False,next_job_dispatched=False)
    dump('RESULT',result)
    dump('PROGRESS',dict(status=result['status'],native_submissions=sum(w.artifact(j,'SUBMIT').exists() for j in jobs()),
        native_jobs_completed=len(completed),pending_jobs=pending,model_fits=0,policy_changes=0))
    print(json.dumps(dict(status=result['status'],native_seconds=result['native_total_seconds'],
        completed={k:dict(market=v['market'],arm=v['arm'],selected=v['selected_direction'],terminal=v['summary']['terminal'],
            both_positive_seconds=v['summary']['both_positive_seconds'],active=v['active_submits']) for k,v in completed.items()},
        pairs=pairs,pending=pending),allow_nan=False))


if __name__=='__main__':main()
