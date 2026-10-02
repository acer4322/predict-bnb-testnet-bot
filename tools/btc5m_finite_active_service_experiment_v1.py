"""V38 bounded service experiment: fast algebra screen, then one worker replay."""
import argparse
import collections
from copy import deepcopy
import inspect
import itertools
import json
import math
import shutil
import sys
import time
from types import SimpleNamespace

import prepare_btc5m_transfer_structural_v1 as parent
from prepare_btc5m_transfer_structural_v1 import ROOT, R, read, sha, load, once
from verify_btc5m_transfer_components_v1 import same
from btc5m_partial_reexposure_experiment_v1 import keyed_legs, START
from hft244_pair_route_legality_v1 import crossing_owners

STEM = 'BTC5M_FINITE_ACTIVE_SERVICE_V1_20260913'
BASE = ROOT/'.lan_worker_v1/partial_reexposure_2028352_20260913_v1'
PACKAGE = ROOT/'.lan_worker_v1/finite_active_service_2028352_20260913_v1'
BASE_JOB = 'fixed15-core-loop-2028352-partial-reexposure-20260913-v1'
JOB = 'fixed15-core-loop-2028352-finite-active-service-20260913-v1'


def dump(tag, x):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(x, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def job():
    return dict(job_id=JOB, market=2028352, arm='finite_service', mode='ORACLE_UP', cwd='.', max_threads=4,
        argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
            '--market-id', '2028352', '--mode', 'ORACLE_UP', '--money-mode', 'PARALLEL_QUANTITY',
            '--demand-mode', 'AUTO_REPAIR', '--retention', '0', '--opportunity-mode', 'ONE_ACTIVE'])


def source():
    s=(BASE/'coordination.py').read_text(encoding='utf-8')
    s=once(s, 'step=0.01, tick=0.01):', 'step=0.01, tick=0.01, continuation=False):')
    s=once(s, 'elif floor_price < ask - EPS:', 'elif floor_price < ask - EPS and not continuation:')
    s=once(s, 'class CoordinationProbe:',
        "def continuation_ready(owner):\n    return owner is not None and owner.state == 'TERMINAL'\n\nclass CoordinationProbe:")
    s=once(s, "if self.submissions or not frame['start'] <= frame['t'] < frame['end']:",
        "if len(self.submissions) >= 2 or not frame['start'] <= frame['t'] < frame['end']:")
    s=once(s, "        ledger = frame['ledger']\n",
        "        ledger = frame['ledger']\n        if self.submissions and not continuation_ready(ledger.carriers.get(self.submissions[0]['key'])):\n            return operations\n")
    s=once(s, "frame['world_profile']['quantity_step'], frame['world_profile']['tick'])",
        "frame['world_profile']['quantity_step'], frame['world_profile']['tick'], continuation=bool(self.submissions))")
    s=once(s, "row.update(t=int(frame['t']), state=state,",
        "row.update(t=int(frame['t']), continuation=bool(self.submissions), state=state,")
    s=once(s, "len(producer.coordination.submissions)<=2 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=1",
        "len(producer.coordination.submissions)<=3 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=2")
    return s


def fast_screen(module):
    """Exact state sensitivity, not generated market fills or fitted probability."""
    import roles_runtime
    started=time.perf_counter(); counts={}; cases=0
    anchor=-359.2864807870742
    for strong, weak in [('UP','DOWN'),('DOWN','UP')]:
        roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION', strong); c=collections.Counter()
        for cent,gap,depth,lifecycle,opposite_cancel in itertools.product(
                range(1,100), (0.,.002,.5,7.5,27.902,79.202), (0.,1.,15.,54.1),
                ('SUBMITTED','CANCEL_PENDING','UNKNOWN','TERMINAL_ZERO','TERMINAL_PARTIAL','TERMINAL_FULL'), (False,True)):
            ask=cent/100; inv={strong:1336.9285714285716,weak:731.2316182013029}
            cost=inv[weak]-anchor+gap; owners=[]; ops=[]
            if lifecycle.startswith('TERMINAL'):
                filled={'TERMINAL_ZERO':0.,'TERMINAL_PARTIAL':7.5,'TERMINAL_FULL':15.}[lifecycle]
                inv[weak]+=filled; cost+=filled*.1
            else:
                owners.append(dict(key='weak_old', side=weak, state=lifecycle, qty=15., limit=.1))
            if opposite_cancel:
                owners.append(dict(key='opposite_old',side=strong,state='CANCEL_PENDING',qty=15.,limit=.85))
                ops=[dict(kind='CANCEL',key='opposite_old')]
            state=dict(inv=inv,cost=cost,payoff={s:inv[s]-cost for s in inv}, owners=owners,
                pending_qty={s:math.fsum(o['qty'] for o in owners if o['side']==s) for s in inv},
                pending_cash={s:math.fsum(o['qty']*o['limit'] for o in owners if o['side']==s) for s in inv})
            before=deepcopy((state,ops)); args=(state,ops,ask,depth,dict(anchor_floor=anchor),True,crossing_owners)
            base=module.decide(*args); alt=module.decide(*args,continuation=True)
            assert before==(state,ops), 'Decision must not mutate reservations'
            cases+=1; c['cases']+=1; c['baseline_eligible']+=base['eligible']; c['service_eligible']+=alt['eligible']
            if alt['eligible']:
                q=alt['quantity']; c['service_above_007']+=ask>.07000001
                assert q*ask>=1-1e-8 and q<=depth+1e-8 and q<=alt['filled_net_quantity_capacity']+1e-8
                assert q*(1-ask)<=max(0.,anchor-alt['projected_down_from_pending_down'])+1e-8
                assert abs(q/.01-round(q/.01))<1e-6 and not alt['conflicts']
                assert not crossing_owners(weak,ask,[dict(key=o['key'],side=o['side'],price=o['limit']) for o in owners])
            if base['eligible']: same(base,alt)
        counts[strong]=dict(c)
    assert counts['UP']==counts['DOWN']
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION','UP')
    lifecycle={}
    for status in ('SUBMITTED','CANCEL_PENDING','UNKNOWN','TERMINAL'):
        for filled in (0.,7.5,15.):
            ready=module.continuation_ready(SimpleNamespace(state=status,filled=filled))
            assert ready==(status=='TERMINAL'); lifecycle[f'{status}:{filled}']=ready
    assert not module.continuation_ready(None)
    return dict(status='PASS',state_cases=cases,decision_evaluations=2*cases,by_strong_side=counts,
        lifecycle_cases=lifecycle,mirrored_exact=True,finite_goal_depth_notional_step_cross_and_pending_pass=True,
        elapsed_seconds=time.perf_counter()-started,model_fit=0,simulated_market_rollouts=0,
        interpretation='Synthetic local state/lifecycle stress cases, not independent market trials, fill forecasts or learned Target rules. Real source state supplies the numerical anchor; perturbations are not actual receipts.')


def prepare():
    assert not PACKAGE.exists(), 'Prepared package must not be overwritten'
    bm=read(BASE/'manifest.json');assert all(sha(BASE/n)==h for n,h in bm['files'].items())
    dump('PROTOCOL',dict(status='PREREGISTERED_BEFORE_SCREEN_AND_NATIVE',market=2028352,baseline_job=BASE_JOB,
        hypothesis='The same finite OWN payoff anchor can receive one additional Active service after the first coordinator carrier is canonically terminal, even when Passive15 remains legal.',
        dedup='Historical V5/V8 persistence/rearm/routes and V34 reexposure are consumed. V37 found cap-only no action and identified first two-gate decision. New intervention fixes the V34 economic anchor, preserves its first two Active decisions and all quantities, admits at most one receipt-gated continuation; no renewed target, rolling target, zero-payoff target, generic unlimited rearm or low-price classifier.',
        fast_scope='Reuse current pure economic/legality decision and lifecycle representation for bounded synthetic state sensitivity. Old Passive-only execution surrogate is not a calibrated simulator for Passive15 plus Active, cancel-pending and current source clock.',
        active_limits=dict(original_first=1,coordinator_original=1,coordinator_continuation=1,total=3),
        receipt_gate='Prior coordinator owner must be TERMINAL; partial and zero outcomes remain eligible for reevaluation, UNKNOWN/cancel-pending never releases ownership.',
        economic_goal='Existing frozen anchor remains unchanged; confirmed and reserved quantity update residual. No forced exact zero, no cash budget.',
        compatibility='Maintenance readiness must continue referring to first coordinator receipt when a second submission is appended; len==1 becomes len>=1, no pricing-rule change.',
        max_native_jobs=1,max_threads=4,local_native_jobs=0,model_fits=0,parameter_search=0,
        evaluation=['Exact plan/state prefix before first changed NEW','Source/receipt/owner/reservation accounting','Finite-anchor gap and actual fill','Both terminal conditional payoffs','Full-path loss area and recovery','Renewed strong acquisition cost and passive replacement'],
        failure='Preserve failed native and UNKNOWN evidence. No duplicate submit or automatic rerun.'))
    PACKAGE.mkdir()
    for name in bm['files']:
        dst=PACKAGE/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/name,dst)
    (PACKAGE/'coordination.py').write_text(source(),encoding='utf-8')
    ms=(BASE/'maintenance_scope.py').read_text(encoding='utf-8')
    ms=once(ms, "if len(submits) == 1 else None", "if len(submits) >= 1 else None")
    (PACKAGE/'maintenance_scope.py').write_text(ms,encoding='utf-8')
    m=deepcopy(bm);m.update(version=STEM,parent_manifest_sha256=sha(BASE/'manifest.json'),maximum_native_jobs=1,
        max_active_submits=3,paired_markets=[],market=2028352)
    m['files']={n:sha(PACKAGE/n) for n in bm['files']}
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    changed=[n for n,h in bm['files'].items() if h!=m['files'][n]]
    assert sorted(changed)==['coordination.py','maintenance_scope.py']
    sys.path.insert(0,str(PACKAGE)); import roles_runtime
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION','UP')
    module=load('finite_service_component',PACKAGE/'coordination.py');module.self_test(crossing_owners)
    screen=fast_screen(module);dump('FAST_SCREEN',screen)
    # Recompute recorded unchanged decisions; the new keyword defaults to the old route.
    tr=read(R/'lan_worker_returns'/BASE_JOB/'clock_trace.json.gz');ep=tr['coordination_episode']
    for row in tr['coordination_rows']:
        z=module.decide(row['state'],row['original_operations'],row['active_ask'],row['visible_depth'],
            ep if row['anchor_floor'] is not None else None,row['active_confirmed'],crossing_owners)
        for k,v in z.items():same(v,row[k])
    witness=read(R/'BTC5M_ACTIVE_CONTINUATION_V1_20260913_RESULT.json')['our']['first_two_masks_eligible']
    candidate=module.decide(witness['state'],witness['original_operations'],witness['diagnostic']['active_ask'],
        witness['diagnostic']['visible_depth'],ep,True,crossing_owners,continuation=True)
    same(candidate,witness['diagnostic'])
    dump('WITNESS',dict(t=witness['t'],seconds=witness['seconds'],quantity=candidate['quantity'],price=candidate['active_ask'],
        baseline_operations=witness['original_operations'],first_two_actives_unchanged=True,
        pending_cancel_not_released=True,meaning='Pure component first difference; native plan/receipt/continuation not yet observed'))
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):compile(parent.compiled(PACKAGE,mode),'finite_service_compile_only','exec')
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION','UP')
    # Explicitly prove scope does not switch off with a second coordinator carrier.
    base=load('finite_service_commitment',PACKAGE/'commitment_repair.py')
    scope=load('finite_service_scope',PACKAGE/'maintenance_scope.py');scope.self_test(base)
    obj=scope.make_probe(base)(None);obj.coordinator=SimpleNamespace(submissions=[dict(key='first'),dict(key='second')])
    owner=SimpleNamespace(key='old',route='PASSIVE',state='CANCEL_PENDING',limit=.08)
    frame=dict(t=1,ledger=SimpleNamespace(carriers={'first':SimpleNamespace(state='TERMINAL',filled=74.2)}),
        quotes={'DOWN':dict(ask=.08)},world_profile=dict(tick=.01))
    assert obj.maintenance(frame,'old',owner,.06,True,.01474)==(.07,False) and len(obj.scope_rows)==1
    dump('COMPONENT',dict(status='PASS',files=len(m['files']),changed=changed,fast_state_cases=screen['state_cases'],
        original_decision_rows=len(tr['coordination_rows']),three_mode_compile=True,maintenance_keeps_first_receipt=True,
        first_difference_seconds=witness['seconds'],manifest_sha256=sha(PACKAGE/'manifest.json')))
    dump('WAVE',dict(jobs=[job()],sequential=True,manifest_sha256=sha(PACKAGE/'manifest.json')))
    dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',fast_screen='PASS',native_submissions=0))
    print(json.dumps(dict(status='FROZEN',job=JOB,fast=screen,changed=changed,manifest_sha256=sha(PACKAGE/'manifest.json'))))


def worker():
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM;w.PACKAGE=PACKAGE;w.dump=dump
    return w


def audit():
    w=worker();folder=R/'lan_worker_returns'/JOB
    n=read(folder/'result.json')
    if n['status']!='COMPLETE':
        dump('PROGRESS',dict(status='NATIVE_FAILED_PRESERVED',native_submissions=1,error=n.get('error'),economic='UNKNOWN'))
        print(json.dumps(dict(status='FAIL',error=n.get('error'))));return
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM=STEM;v.PACKAGE=PACKAGE;v.dump=dump;v.jobs=lambda:[None,job()]
    # Explicit contract extension only: complete existing validation still runs.
    code=once(inspect.getsource(v.inspect_job),'len(tr[\'coordination_submissions\'])<=2',"len(tr['coordination_submissions'])<=3")
    ns=dict(v.__dict__);exec(compile(code,'FINITE_SERVICE_MAX_THREE_AUDIT','exec'),ns)
    execution=ns['inspect_job'](1)
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.PACKAGE=PACKAGE;cv.STEM=STEM;cv.dump=dump;cv.jobs=lambda:[None,job()]
    code=once(inspect.getsource(cv.main),"episode,confirmed,crossing_owners)",
        "episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    ns=dict(cv.__dict__);exec(compile(code,'FINITE_SERVICE_CONTINUATION_AUDIT','exec'),ns);ns['main']()
    tr=read(folder/'clock_trace.json.gz');bn=read(R/'lan_worker_returns'/BASE_JOB/'result.json');bt=read(R/'lan_worker_returns'/BASE_JOB/'clock_trace.json.gz')
    first=next(i for i,(a,b) in enumerate(zip(tr['plans'],bt['plans'])) if a!=b);t=tr['plans'][first]['t']
    witness=read(R/(STEM+'_WITNESS.json'));assert t==witness['t']
    assert tr['plans'][:first]==bt['plans'][:first]
    assert [s for s in tr['states'] if s['t']<=t]==[s for s in bt['states'] if s['t']<=t]
    assert len(tr['coordination_submissions'])==2 and tr['coordination_submissions'][0]==bt['coordination_submissions'][0]
    assert tr['opportunity_submissions']==bt['opportunity_submissions']
    same(tr['coordination_episode'],bt['coordination_episode'])
    service=tr['coordination_submissions'][1];same(service['qty'],witness['quantity']);same(service['price'],witness['price'])
    assert tr['plans'][first]['operations']==[
        *witness['baseline_operations'],{k:v for k,v in service.items() if k!='t'}]
    a=read(w.artifact(job(),'AUDIT'));oa=read(R/'BTC5M_PARTIAL_REEXPOSURE_V1_20260913_2028352_partial_AUDIT.json')
    fill=next(o for o in a['receipt_accounting']['orders'] if o['key']==service['key'])
    flows={};strong_after={};event_flows={};service_legs=[]
    for label,result,trace in [('baseline',bn,bt),('candidate',n,tr)]:
        news={o['key']:dict(t=p['t'],**o) for p in trace['plans'] for o in p['operations'] if o['kind']=='NEW'}
        legs=keyed_legs(result,trace,news)
        grouped=collections.defaultdict(lambda:[0.,0.])
        for leg in legs:
            key=(leg['t'],leg['side'],leg['route'],leg['price'])
            grouped[key][0]+=leg['qty'];grouped[key][1]+=leg['cash']
        event_flows[label]=grouped
        if label=='candidate':service_legs=[leg for leg in legs if leg['key']==service['key']]
        flows[label]={side:{route:dict(qty=math.fsum(x['qty'] for x in legs if x['side']==side and x['route']==route),
            cash=math.fsum(x['cash'] for x in legs if x['side']==side and x['route']==route)) for route in ('PASSIVE','ACTIVE')} for side in ('UP','DOWN')}
        strong_after[label]=[x for x in legs if x['side']=='UP' and x['t']>t]
    totals={label:{side:dict(qty=sum(flows[label][side][r]['qty'] for r in ('PASSIVE','ACTIVE')),
        cash=sum(flows[label][side][r]['cash'] for r in ('PASSIVE','ACTIVE'))) for side in ('UP','DOWN')} for label in flows}
    delta={s:n['final_inventory'][s]-n['final_cost']-bn['final_inventory'][s]+bn['final_cost'] for s in ('UP','DOWN')}
    quantity_delta={s:totals['candidate'][s]['qty']-totals['baseline'][s]['qty'] for s in ('UP','DOWN')}
    cash_delta={s:totals['candidate'][s]['cash']-totals['baseline'][s]['cash'] for s in ('UP','DOWN')}
    for side in delta:same(delta[side],quantity_delta[side]-sum(cash_delta.values()))
    event_deltas=[]
    for key in sorted(set(event_flows['baseline'])|set(event_flows['candidate'])):
        old=event_flows['baseline'].get(key,[0.,0.]);new=event_flows['candidate'].get(key,[0.,0.])
        if abs(new[0]-old[0])>1e-8 or abs(new[1]-old[1])>1e-8:
            event_deltas.append(dict(t=key[0],seconds=(key[0]-START)/1000,side=key[1],route=key[2],price=key[3],
                quantity_delta=new[0]-old[0],cash_delta=new[1]-old[1]))
    for side in delta:
        same(sum(x['quantity_delta'] for x in event_deltas if x['side']==side),quantity_delta[side])
        same(sum(x['cash_delta'] for x in event_deltas if x['side']==side),cash_delta[side])
    service_t=max(x['t'] for x in service_legs) if service_legs else None
    goal=None
    if service_t is not None:
        later=[s for s in tr['states'] if s['t']>=service_t];state=later[0];anchor=tr['coordination_episode']['anchor_floor']
        goal=dict(last_service_fill_t=service_t,anchor=anchor,confirmed_state=state,
            confirmed_gap_after_service=anchor-(state['inv']['DOWN']-state['cost']),
            anchor_reached_later=any(s['inv']['DOWN']-s['cost']>=anchor-1e-8 for s in later),
            pending_at_first_final=[x for x in tr['demand_final']['all_final_carriers'] if x['key']=='DOWN_327'])
    out=dict(status='COMPLETE',verification='PASS',job=JOB,baseline_job=BASE_JOB,first_plan_difference_seconds=(t-START)/1000,
        first_plan=tr['plans'][first],prefix_exact=True,unchanged_first_two_active=True,finite_anchor_unchanged=True,
        service_owner=fill,execution=execution,flows=flows,strong_fills_after_intervention=strong_after,
        quantity_delta=quantity_delta,cash_delta_by_side=cash_delta,
        economic_event_deltas=event_deltas,finite_goal_after_service=goal,
        baseline_terminal={s:bn['final_inventory'][s]-bn['final_cost'] for s in delta},
        candidate_terminal={s:n['final_inventory'][s]-n['final_cost'] for s in delta},terminal_delta=delta,
        path_comparison={label:{k:x['trajectory'][k] for k in ('negative_floor_area_currency_seconds','minimum_floor','both_positive_seconds')}
            for label,x in [('baseline',oa),('candidate',a)]},
        native_jobs=1,local_native_jobs=0,model_fits=0,parameter_search=0,
        limitation='One consumed-market bounded service contrast. Fast cases are exact algebra checks, not calibrated rollout training. No general Target policy or high-price Active trigger identified.')
    dump('RESULT',out);dump('PROGRESS',dict(status='COMPLETE',verification='PASS',native_submissions=1))
    post=read(w.artifact(job(),'POSTCHECK'))
    dump('FINAL_VALIDATION',dict(status='PASS',native_job=JOB,native_submissions=1,duplicate_submissions=0,
        fast_screen_cases=read(R/(STEM+'_FAST_SCREEN.json'))['state_cases'],
        source_frames=a['frames'],raw_receipts=a['raw_receipts'],all_owners_terminal=a['all_owners_terminal'],
        all_pending_zero=a['all_pending_zero'],first_plan_and_state_prefix_exact=True,finite_anchor_unchanged=True,
        complete_economic_event_deltas_reconciled=True,baseline_manifest_unchanged=all(sha(BASE/k)==h for k,h in read(BASE/'manifest.json')['files'].items()),
        candidate_manifest_sha256=sha(PACKAGE/'manifest.json'),native_sha256=post['native_sha256'],
        shared_files_unchanged=post['shared_files_unchanged'],missing_owner_terminal_clocks=a['terminal_timing']['missing_owner_observation_clocks'],
        tool_sha256=sha(ROOT/'tools/btc5m_finite_active_service_experiment_v1.py')))
    print(json.dumps({k:out[k] for k in ('status','candidate_terminal','terminal_delta','cash_delta_by_side','service_owner','path_comparison')}))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','preflight','submit','status','collect','audit']);a=ap.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='audit':audit()
    else:
        w=worker()
        z=w.preflight() if a.action=='preflight' else w.dispatch.cmd_status(w.HOST,JOB) if a.action=='status' else getattr(w,a.action)(0)
        print(json.dumps(z))
