"""One V34 partial-repair reexposure contrast; reuse frozen V33 and LAN tools."""
import argparse
import ast
import collections
from copy import deepcopy
import inspect
import json
from pathlib import Path
import shutil
import sys

import prepare_btc5m_transfer_structural_v1 as parent
from prepare_btc5m_transfer_structural_v1 import ROOT, R, read, sha, load, once
from hft244_pair_route_legality_v1 import crossing_owners
from verify_btc5m_transfer_components_v1 import same
from verify_btc5m_active_repair_opportunity_v1 import receipt_auditor

STEM = 'BTC5M_PARTIAL_REEXPOSURE_V1_20260913'
PACKAGE = ROOT/'.lan_worker_v1/partial_reexposure_2028352_20260913_v1'
BASE = parent.PACKAGE
JOB = 'fixed15-core-loop-2028352-partial-reexposure-20260913-v1'
BASE_JOB = 'fixed15-core-loop-2028352-transfer-known-20260913-v1'
START = 1788766200000


def dump(tag, value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def job():
    return dict(job_id=JOB, market=2028352, arm='partial', mode='ORACLE_UP', cwd='.', max_threads=4,
        argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
            '--market-id','2028352','--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY',
            '--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE'])


def coordinator_source():
    source=(BASE/'coordination.py').read_text(encoding='utf-8')
    # The only economic edit: a positive strong payoff suffices; weak need not have reached positive.
    source=once(source,"if min(previous['payoff'].values()) <= EPS or state['payoff'][roles.weak] >= -EPS:",
        "if previous['payoff'][roles.strong] <= EPS or state['payoff'][roles.weak] >= -EPS:")
    source=once(source,"reason='CONFIRMED_UP_ONLY_FILL_BREAKS_PREVIOUS_BOTH_POSITIVE_STATE'",
        "reason='CONFIRMED_STRONG_ONLY_FILL_WORSENS_PREVIOUS_REPAIRED_STATE'")
    source=once(source,"'One receipt-triggered repair after UP acquisition breaks an observed lock.\\n\\nCompare the minimum legal Active order with restoring the pre-addition floor.\\nThe reference is captured causally, not fixed at zero or read from Target.\\n'",
        "'One receipt-triggered repair after strong acquisition worsens a previously repaired state. The anchor may remain negative.'")
    return source


def shadow(module, rows):
    previous=None; seen=False; episode=None; output=[]
    for row in rows:
        state=row['state']; confirmed=row['active_confirmed']
        if episode is None: episode=module.detect(previous,state,confirmed,seen,row['t'])
        if confirmed and previous and state['inv']['DOWN']>previous['inv']['DOWN']+1e-8: seen=True
        previous={k:state[k] for k in ('inv','cost','payoff')}
        decision=module.decide(state,row['original_operations'],row['active_ask'],row['visible_depth'],episode,confirmed,crossing_owners)
        output.append(dict(t=row['t'],**decision))
        # Only prefix to the first decision is a causal replay. Later baseline states would diverge.
        if decision['eligible']: break
    return episode,output


def keyed_legs(result, trace, news):
    queues=collections.defaultdict(collections.deque)
    for row in trace['demand_final']['full_raw_receipts']:
        assert row['fee']==0
        if row['qty']>0:queues[row['key']].append([row['qty'],row['contractPrice'],row['maker']])
    output=[]
    for event in result['atomic_responsibility_events']:
        for fill in event['fill_rows']:
            remaining=fill['fill_increment']; key=fill['key']
            while remaining>1e-10:
                raw=queues[key][0]; qty=min(remaining,raw[0]); owner=news[key]
                output.append(dict(t=event['t'],key=key,side=fill['side'],qty=qty,cash=qty*raw[1],
                    price=raw[1],born_t=owner['t'],role=owner['role'],route=owner['route']))
                remaining-=qty;raw[0]-=qty
                if raw[0]<=1e-10:queues[key].popleft()
    assert sum(r[0] for q in queues.values() for r in q)<1e-7
    for side in ('UP','DOWN'):same(sum(r['qty'] for r in output if r['side']==side),result['final_inventory'][side])
    same(sum(r['cash'] for r in output),result['final_cost'])
    return output


def baseline_diagnosis():
    folder=R/'lan_worker_returns'/BASE_JOB
    n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz')
    audit=receipt_auditor()(n,tr)
    news={o['key']:dict(t=p['t'],**o) for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW'}
    legs=keyed_legs(n,tr,news)
    cut=min(tr['states'],key=lambda x:min(x['inv'].values())-x['cost'])['t']
    after=[x for x in legs if x['t']>cut]
    def flow(rows):
        return dict(qty=sum(x['qty'] for x in rows),cash=sum(x['cash'] for x in rows),
            own_payoff_lift=sum(x['qty']-x['cash'] for x in rows),owners=sorted({x['key'] for x in rows}))
    roles=collections.defaultdict(list)
    for leg in after:roles[(leg['side'],leg['role'],'born_before_or_at_cut' if leg['born_t']<=cut else 'born_after_cut')].append(leg)
    cancelled=[]
    for owner in audit['orders']:
        if owner['side']!='UP':continue
        for t in owner['cancel_times']:
            if t<=cut:continue
            maintenance=next((x for x in tr['demand_maintenance_rows'] if x['t']==t and x['key']==owner['key']),None)
            if maintenance:cancelled.append(dict(owner=owner,cancel_t=t,maintenance=maintenance))
    out=dict(status='PASS',baseline_job=BASE_JOB,result_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'),
        cut_t=cut,cut_label='Posthoc minimum floor for diagnosis only, never actor input',
        after_flow={s:flow([x for x in after if x['side']==s]) for s in ('UP','DOWN')},
        after_owner_origins=[dict(side=k[0],role=k[1],birth_group=k[2],**flow(v)) for k,v in roles.items()],
        strong_post_cut_cancelled_owners=cancelled,after_legs=after,
        scope='Actual raw/canonical fills and accepted NEW orders. Cancelled unfilled orders are not predicted fills.')
    dump('DIAGNOSIS',out)
    return n,tr


def prepare():
    assert not PACKAGE.exists(),'Frozen experiment exists; do not rebuild or rerun'
    manifest=read(BASE/'manifest.json');assert all(sha(BASE/n)==h for n,h in manifest['files'].items())
    # Dedup: V31 deferred a partial-repair rearm; V32 adjusted growth only; V33 froze all rules.
    dump('PREREGISTERED',dict(status='REGISTERED_BEFORE_NATIVE',question='Does retaining the pre-addition partial-repair floor permit useful bounded repair before a price reversal?',
        market=2028352,baseline_job=BASE_JOB,baseline_manifest_sha256=sha(BASE/'manifest.json'),
        dedup='V25/V26 used a prior both-positive lock. V31 noted partial-repair rearm but V32 changed only growth. V33 transferred unchanged. This changes only prior both-positive detection to prior strong-positive detection; no price veto, cash cap, bootstrap fix or growth change.',
        candidate='Confirmed first Active plus previously observed weak acquisition, followed by a pure confirmed strong acquisition, may create one finite reexposure episode even when the prior weak payoff was negative. Anchor remains the immediately preceding minimum conditional payoff.',
        unchanged=['At most one coordinator Active','First Active selection','Current ask/depth and step','Passive15 minimum notional','Pending and same-plan NEW reservations','Original weak NEW priority','Active only when a legal Passive15 price is unavailable','Repair cash gates OFF','V32 growth and all maintenance modules','Oracle UP bit only; no Target path'],
        max_native_jobs=1,model_fits=0,parameter_search=0,local_native_jobs=0,
        evaluation=['First exact plan divergence at a prefix-causal OWN event','Raw/canonical accounting and all source frames','Actual second Active fill, not decision-only potential','Both conditional payoff branches and full path','Repair lift versus renewed strong cost and subsequent expensive repair'],
        selection='Consumed V33 failure case, mechanism contrast not held-out validation',
        failure_policy='Stop and preserve native exception/UNKNOWN evidence; do not resubmit'))
    PACKAGE.mkdir()
    for name in manifest['files']:
        dst=PACKAGE/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/name,dst)
    (PACKAGE/'coordination.py').write_text(coordinator_source(),encoding='utf-8')
    m=deepcopy(manifest);m.update(version=STEM,maximum_native_jobs=1,paired_markets=[],market=2028352,
        parent_manifest_sha256=sha(BASE/'manifest.json'))
    m['files']={name:sha(PACKAGE/name) for name in manifest['files']}
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    changed=[name for name,h in manifest['files'].items() if m['files'][name]!=h]
    assert changed==['coordination.py'],changed
    sys.path.insert(0,str(PACKAGE));import roles_runtime
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION','UP')
    module=load('partial_coord_component',PACKAGE/'coordination.py')
    module.self_test(crossing_owners)
    n,tr=baseline_diagnosis()
    ep,rows=shadow(module,tr['coordination_rows']);first=rows[-1]
    assert first['eligible'] and not first['conflicts']
    reference=next(x for x in tr['coordination_rows'] if x['t']==first['t'])
    assert reference['original_operations']==[] and reference['state']['owners']==[]
    assert not next(p for p in tr['plans'] if p['t']==first['t'])['operations']
    assert first['quantity']*first['active_ask']>=1 and first['quantity']<=first['visible_depth']
    # Prefix invariance, incomplete/unknown receipt authority, pure fill requirement and role symmetry.
    for count in (1,len(rows)//2,len(rows)):
        prefix_ep,prefix_rows=shadow(module,tr['coordination_rows'][:count]);same(prefix_rows,rows[:count])
    previous=ep['before'];state=ep['after']
    assert module.detect(previous,state,False,True,first['t']) is None
    assert module.detect(previous,state,True,False,first['t']) is None
    mixed=deepcopy(state);mixed['inv']['DOWN']+=1
    assert module.detect(previous,mixed,True,True,first['t']) is None
    from verify_btc5m_transfer_components_v1 import swap
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
    mirrored=module.detect(swap(previous),swap(state),True,True,first['t'])
    same(mirrored,swap(ep))
    roles_runtime.roles.configure('KNOWN_FINAL_DIRECTION','UP')
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):compile(parent.compiled(PACKAGE,mode),'partial_compile_only','exec')
    # module tests may reset role singleton; explicit side for all later diagnostics.
    witness=dict(episode=ep,decision=first,source_index=next(x['index'] for x in tr['intent'] if x['t']==first['t']),
        seconds=(first['t']-START)/1000,baseline_plan=[],candidate_new=dict(side='DOWN',route='ACTIVE',qty=first['quantity'],price=first['active_ask']),
        isolated_full_fill_effect=dict(strong_cost=first['quantity']*first['active_ask'],weak_lift=first['quantity']*(1-first['active_ask'])),
        limitation='Only prefix up to first plan difference is replayed. Full-fill arithmetic is not a native result; later original states are not reused as a counterfactual.')
    dump('WITNESS',witness)
    dump('COMPONENT',dict(status='PASS',changed_modules=changed,files=len(m['files']),manifest_sha256=sha(PACKAGE/'manifest.json'),
        prefix_rows=len(rows),mirrored_partial_anchor=True,unconfirmed_repair_blocks=True,mixed_fill_blocks=True,
        empty_reserved_owners_and_original_plan_at_first=True,syntax_modes=3,first_difference=witness['seconds'],native_jobs=0))
    dump('WAVE',dict(jobs=[job()],manifest_sha256=sha(PACKAGE/'manifest.json'),sequential=True))
    print(json.dumps(dict(status='FROZEN',job=JOB,first=witness['seconds'],candidate=witness['candidate_new'],manifest_sha256=sha(PACKAGE/'manifest.json'))))


def bind_worker():
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM;w.PACKAGE=PACKAGE;w.dump=dump
    return w


def audit():
    w=bind_worker()
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM=STEM;v.PACKAGE=PACKAGE;v.dump=dump
    # Reuse non-parity branch without inventing a second job or dispatching it.
    v.jobs=lambda:[None,job()]
    result=v.inspect_job(1)
    import verify_btc5m_transfer_continuation_v1 as continuation
    continuation.PACKAGE=PACKAGE;continuation.STEM=STEM;continuation.dump=dump;continuation.jobs=lambda:[None,job()]
    continuation.main()
    n=read(R/'lan_worker_returns'/JOB/'result.json');tr=read(R/'lan_worker_returns'/JOB/'clock_trace.json.gz')
    bn=read(R/'lan_worker_returns'/BASE_JOB/'result.json');bt=read(R/'lan_worker_returns'/BASE_JOB/'clock_trace.json.gz')
    first=next(i for i,(a,b) in enumerate(zip(tr['plans'],bt['plans'])) if a!=b)
    witness=read(R/(STEM+'_WITNESS.json'));t=tr['plans'][first]['t']
    assert t==witness['decision']['t']
    assert tr['plans'][:first]==bt['plans'][:first]
    assert [x for x in tr['states'] if x['t']<=t]==[x for x in bt['states'] if x['t']<=t]
    assert len(tr['coordination_submissions'])==1
    active=tr['coordination_submissions'][0]
    same(active['qty'],witness['decision']['quantity']);same(active['price'],witness['decision']['active_ask'])
    audit=read(w.artifact(job(),'AUDIT'))
    fill=next(x for x in audit['receipt_accounting']['orders'] if x['key']==active['key'])
    counts={}; flow_rows={}; addition_timeline={}; growth_at_fill={}
    for label,trace in [('base',bt),('partial',tr)]:
        nr=bn if label=='base' else n
        news={o['key']:dict(t=p['t'],**o) for p in trace['plans'] for o in p['operations'] if o['kind']=='NEW'}
        legs=keyed_legs(nr,trace,news)
        flow_rows[label]={s:{route:dict(qty=sum(x['qty'] for x in legs if x['side']==s and x['route']==route),
            cash=sum(x['cash'] for x in legs if x['side']==s and x['route']==route)) for route in ('PASSIVE','ACTIVE')} for s in ('UP','DOWN')}
        groups=collections.defaultdict(list)
        for leg in legs:
            if leg['side']=='UP' and leg['t']>t:groups[leg['t']].append(leg)
        addition_timeline[label]=[dict(t=k,seconds=(k-START)/1000,qty=sum(x['qty'] for x in rows),
            cash=sum(x['cash'] for x in rows),legs=rows) for k,rows in sorted(groups.items())]
        growth_at_fill[label]=next(x for x in trace['addition_growth_rows'] if x['t']==fill['first_canonical_t'])
        # Fixed baseline diagnostic cut for matched windows, also report candidate's own trough separately.
        cut=read(R/(STEM+'_DIAGNOSIS.json'))['cut_t']
        counts[label]={s:dict(qty=sum(x['qty'] for x in legs if x['t']>cut and x['side']==s),
            cash=sum(x['cash'] for x in legs if x['t']>cut and x['side']==s)) for s in ('UP','DOWN')}
    delta={s:(n['final_inventory'][s]-n['final_cost'])-(bn['final_inventory'][s]-bn['final_cost']) for s in ('UP','DOWN')}
    up_extra=flow_rows['partial']['UP']['PASSIVE']['cash']-flow_rows['base']['UP']['PASSIVE']['cash']
    down_passive_saved=flow_rows['base']['DOWN']['PASSIVE']['cash']-flow_rows['partial']['DOWN']['PASSIVE']['cash']
    down_passive_qty_lost=flow_rows['base']['DOWN']['PASSIVE']['qty']-flow_rows['partial']['DOWN']['PASSIVE']['qty']
    same(delta['UP'],down_passive_saved-up_extra-fill['payment'])
    same(delta['DOWN'],fill['filled']-fill['payment']-(down_passive_qty_lost-down_passive_saved)-up_extra)
    base_audit=read(R/'BTC5M_TRANSFER_STRUCTURAL_V32_V1_20260913_2028352_known_AUDIT.json')
    attribution=dict(status='PASS',full_episode_route_flows=flow_rows,post_decision_strong_fill_timeline=addition_timeline,
        growth_on_actual_active_confirmation=growth_at_fill,unchanged_terminal_strong_qty=n['final_inventory']['UP']==bn['final_inventory']['UP'],
        strong_additional_cash=up_extra,weak_passive_cash_saved=down_passive_saved,weak_passive_qty_reduction=down_passive_qty_lost,
        coordinator_actual_cash=fill['payment'],coordinator_actual_weak_lift=fill['filled']-fill['payment'],
        equations=dict(UP='weak passive cash saved - coordinator cash - extra strong cash',
            DOWN='coordinator weak lift - forgone passive weak lift - extra strong cash'),
        interpretation='The changed receipt path raised V32 growth and moved strong acquisitions earlier to higher prices. Equal terminal strong shares conceal higher strong acquisition cost. No retained or unfilled order is counted as a fill.')
    dump('ECONOMIC_ATTRIBUTION',attribution)
    out=dict(status='COMPLETE',job=JOB,baseline_job=BASE_JOB,first_plan_difference=dict(index=first,t=t,seconds=(t-START)/1000,baseline=bt['plans'][first],candidate=tr['plans'][first]),
        source_state_and_plan_prefix_exact=True,actual_coordinator_owner=fill,baseline_terminal={s:bn['final_inventory'][s]-bn['final_cost'] for s in ('UP','DOWN')},
        candidate_terminal={s:n['final_inventory'][s]-n['final_cost'] for s in ('UP','DOWN')},terminal_delta=delta,
        cash_delta=n['final_cost']-bn['final_cost'],fixed_baseline_cut_flows=counts,execution_audit=result,
        path_comparison={label:{k:x['trajectory'][k] for k in ('negative_floor_area_currency_seconds','minimum_floor','both_positive_seconds')}
            for label,x in [('base',base_audit),('partial',audit)]},economic_attribution=attribution,
        limitations=['Consumed one-market mechanism test, not generalization','Target final observed net side is an oracle condition, not private intent','No exact timestamps invented for missing owner terminal clocks'],
        model_fits=0,parameter_search=0,native_jobs=1,local_native_jobs=0)
    dump('RESULT',out)
    print(json.dumps({k:out[k] for k in ('status','candidate_terminal','terminal_delta','cash_delta','actual_coordinator_owner')}))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','preflight','submit','status','collect','audit']);args=ap.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='audit':audit()
    else:
        w=bind_worker()
        out=w.preflight() if args.action=='preflight' else w.dispatch.cmd_status(w.HOST,JOB) if args.action=='status' else getattr(w,args.action)(0)
        print(json.dumps(out))
