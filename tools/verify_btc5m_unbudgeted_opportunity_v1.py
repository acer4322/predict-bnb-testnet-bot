"""Read-only native verification, cash-OFF semantics and concurrent path response."""
import bisect
import inspect
import json
import math
import types

import verify_btc5m_active_repair_opportunity_v1 as old
from prepare_btc5m_unbudgeted_opportunity_v1 import ROOT,R,PACKAGE,BASE,STEM,JOB,load,sha,once
from aggregate_btc5m_exposure_intent_ablation_v1 import get,RET,STREAMS
from audit_btc5m_target_core_loop_topology_v1 import read
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary
from audit_btc5m_post_exposure_response_v1 import reconstruct,analyze,response

START,END=old.START,old.END


def folder(tag):return BASE if tag=='control' else RET/JOB


def auditor():
    source=inspect.getsource(old.audit)
    source=once(source,"clock['profit_retention'] == .5", "clock['profit_retention'] == 0. and not clock['cash_budget_enabled'] and not result['cash_budget_enabled']")
    source=once(source,"        assert paid['DOWN'] <= .5*(row['inv']['UP']-paid['UP'])+1e-7\n",'')
    a=source.index('        room = max(0., .5*');b=source.index('\n    demand =',a)
    source=source[:a]+'''        assert row['retention']==0. and row['cash_budget_enabled'] is False
        assert row['available_cash'] is None and row['quantity_cap'] is None
        close(row['admitted'], row['old_quantity_cap'])
'''+source[b:]
    source=once(source,"        assert p['DOWN']+s['pending_cash']['DOWN'] <= .5*(s['inv']['UP']-p['UP'])+1e-7\n",'')
    a=source.index('    for plan in trace[\'plans\']:');b=source.index('    source_path =',a)
    source=source[:a]+source[b:]
    source=once(source,"row['visible_depth'], row['original_operations'], .01, .5, crossing_owners)",
                "row['visible_depth'], row['original_operations'], .01, 0., crossing_owners)")
    source=once(source,"        assert op['qty']*op['price'] <= selected['available_cash']+1e-7",
                "        assert selected['available_cash'] is None and selected['cash_budget_enabled'] is False")
    module=load('verified_unbudgeted_decide',PACKAGE/'active_opportunity.py')
    ns=dict(old.__dict__,PACKAGE=PACKAGE,folder=folder,decide=module.decide)
    exec(compile(source,'explicit_unbudgeted_audit','exec'),ns)
    return ns['audit']


def intervals(states):
    points={START:dict(t=START,inv=dict(UP=0.,DOWN=0.),cost=0.)}
    points.update({r['t']:r for r in states if START<r['t']<=END})
    times=sorted(points);runs=[];current=None
    for t,n in zip(times,times[1:]+[END]):
        s=points[t];g=geometry(s['inv'],s['cost'])
        if min(g['up'],g['down'])>1e-7 and n>t:
            if current is None:current=dict(start=t,end=n,start_geometry=g,minimum_floor=g['floor'],maximum_floor=g['floor'])
            else:
                current['end']=n;current['minimum_floor']=min(current['minimum_floor'],g['floor']);current['maximum_floor']=max(current['maximum_floor'],g['floor'])
        elif current is not None:
            current.update(duration_seconds=(current['end']-current['start'])/1000,exit_geometry=g)
            runs.append(current);current=None
    if current is not None:
        current.update(duration_seconds=(current['end']-current['start'])/1000,exit_geometry=None);runs.append(current)
    return runs


def supplemental(result,trace,anchor_t):
    rows=reconstruct(old.canonical_legs(result,trace))
    times=[r['t'] for r in rows];i=bisect.bisect_right(times,anchor_t)-1
    anchor=dict(rows[i],t=anchor_t)
    states={s['t']:s for s in trace['states']}
    before_t=max(t for t in states if t<anchor_t)
    runs=intervals(trace['states'])
    path=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),trace['states'],START,END)
    old.close(sum(x['duration_seconds'] for x in runs),path['both_positive_seconds'])
    plan_joint=sum({'UP','DOWN'} <= {o['side'] for o in p['operations'] if o['kind']=='NEW'} for p in trace['plans'])
    demand={r['t']:r['state'] for r in trace['demand_rows']}
    at=max(t for t in demand if t<=anchor_t);s=demand[at]
    concurrent=sum({'UP','DOWN'} <= {o['side'] for o in p['operations'] if o['kind']=='NEW'}
                   and demand[p['t']]['inv']['UP']>demand[p['t']]['inv']['DOWN']
                   and demand[p['t']]['payoff']['DOWN']<0 for p in trace['plans'] if p['t'] in demand)
    return dict(both_positive_intervals=runs,both_positive_seconds=path['both_positive_seconds'],
                intervention_clock=anchor_t,before=geometry(states[before_t]['inv'],states[before_t]['cost']),
                at_anchor=geometry(anchor['inv'],anchor['cost']),
                after_intervention_windows={str(s):response(rows,anchor,END,s) for s in (5,15,30)},
                until_end=response(rows,anchor,END,(END-anchor_t)/1000),
                open_commitments_at_anchor=dict(t=at,pending_qty=s['pending_qty'],pending_cash=s['pending_cash'],
                    confirmed_payoff=s['payoff'],
                    down_payoff_if_all_current_pending_fill=s['payoff']['DOWN']+s['pending_qty']['DOWN']-sum(s['pending_cash'].values()),
                    scope='Pre-plan ledger at this observation, full-fill scenario only, not a prediction'),
                joint_up_down_new_plans=plan_joint,
                joint_up_add_down_repair_with_existing_up_net=concurrent,
                meaning='Both-positive means current filled inventory only. Pending/new additions can change this structure.')


def main():
    m=read(PACKAGE/'manifest.json')
    assert all(sha(PACKAGE/n)==h for n,h in m['files'].items())
    assert sha(BASE/'result.json')==m['baseline_result_sha256'] and sha(BASE/'clock_trace.json.gz')==m['baseline_trace_sha256']
    c,ct=get(BASE);a,at=get(RET/JOB)
    assert c['theta']==a['theta'] and c['clock_smoke']['profit_retention']==0.
    first=at['opportunity_first'];obs=read(R/(STEM+'_OBSERVATION.json'))['first']
    for k in ('t','quantity','active_ask','quantity_cap','payoff_cap','visible_depth'):
        old.close(first[k],obs[k])
    assert first['available_cash'] is None
    assert sum(r['eligible'] for r in at['opportunity_rows'])==1 and at['opportunity_rows'][-1]==first
    t=first['t']
    prefix={k:[x for x in ct[k] if x['t']<t]==[x for x in at[k] if x['t']<t]
            for k in (*STREAMS,'intent','money_rows','demand_rows','demand_events','demand_owner_rows','demand_plan_rows','demand_maintenance_rows')}
    assert all(prefix.values()),[k for k,v in prefix.items() if not v]
    left=[r for r in ct['profit_budget_rows'] if r['t']<t];right=[r for r in at['profit_budget_rows'] if r['t']<t]
    assert len(left)==len(right)
    for x,y in zip(left,right):
        for k in x:
            if k not in ('available_cash','quantity_cap'):old.same_value(x[k],y[k])
        assert y['available_cash'] is None and y['quantity_cap'] is None and not y['cash_budget_enabled']
    cp=next(p for p in ct['plans'] if p['t']==t);ap=next(p for p in at['plans'] if p['t']==t)
    assert ap['operations'][:-1]==cp['operations'] and ap['operations'][-1]['route']=='ACTIVE'
    old.same_value(first['state'],next(r['state'] for r in ct['demand_rows'] if r['t']==t))
    ar=auditor()('active',a,at)
    previous=read(R/'BTC5M_FIXED15_CORE_LOOP_V2_20260913_RESULT.json')['arms']['control']
    cr=dict(terminal=previous['terminal'],paid_by_side=previous['paid_by_side'],retention=previous['up_profit_retention'],
            trajectory=previous['trajectory'],post_exposure=previous['post_exposure'],
            receipts=old.receipt_auditor()(c,ct),submits=c['submits'],passive_submits=c['passive_native_submits'],active_submits=0,
            core_similarity=c['core_similarity'],native_safety=c['safety_gate'],reused_existing_result=True)
    for k,v in geometry(c['final_inventory'],c['final_cost']).items():old.same_value(v,cr['terminal'][k])
    delta={k:ar['terminal'][k]-cr['terminal'][k] for k in ('up','down','cost','up_net')}
    # Same observation clock for both arms, avoiding different self-selected peaks.
    active_order=ar['direct_active']['order'];anchor=active_order['first_canonical_t'] or t
    supp={tag:supplemental(r,tr,anchor) for tag,(r,tr) in dict(control=(c,ct),active=(a,at)).items()}
    diagnose=types.FunctionType(old.diagnose.__code__,dict(old.diagnose.__globals__,R=R,STEM=STEM),old.diagnose.__name__)
    flow=diagnose(dict(control=(c,ct),active=(a,at)),delta)
    diagnostic=read(R/(STEM+'_FLOW_DIAGNOSTIC.json'))
    diagnostic['interpretation']='Cash gate OFF in both branches. Changes are from one causal Active intervention and subsequent own-state/controller/native path feedback; not budget competition.'
    (R/(STEM+'_FLOW_DIAGNOSTIC.json')).write_text(json.dumps(diagnostic,indent=2)+'\n',encoding='utf-8')
    post=read(R/(STEM+'_POSTCHECK.json'))
    assert post['status']=='PASS' and post['native_sha256']==m['native_sha256']
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert a['sizing_installation']==pins and all(sha(ROOT/'tools'/n)==v['before'] for n,v in pins.items())
    direct=ar['direct_active']
    target_file=R/'BTC5M_POST_EXPOSURE_RESPONSE_V1_20260913_RESULT.json'
    existing_target=read(target_file);target=existing_target['target']['2026085'];w=target['peak']['windows']['30']
    target_context=dict(source_result_sha256=sha(target_file),input_sha256=target['source_sha256'],
        peak_seconds=target['peak']['seconds'],peak_next30={k:w[k] for k in
        ('weak_payoff_change','weak_payoff_after','strong_payoff_after','ongoing_strong_acquisition','weak_acquisition','both_routes_on_weak','net_retained_fraction')},
        existing_prefix_summary=existing_target['selected_market_prefix_summary'],
        interpretation='Observed overlap and incomplete repair support a working hypothesis. Private intent and frequency of small-loss or large-success outcomes are not established. Global peak response is descriptive and selection-biased.')
    assert target_context['input_sha256']==m['oracle_source_sha256']
    out=dict(status='COMPLETE',verification='PASS',new_native_jobs=1,reused_native_controls=1,local_native_jobs=0,model_fits=0,
             explicit_cash_gate_off_both_routes=True,prefix=prefix,budget_semantic_prefix_rows=len(left),
             manifest_sha256=sha(PACKAGE/'manifest.json'),worker_postcheck_pass=True,
             arms=dict(control=cr,active=ar),supplemental=supp,terminal_delta=delta,flow_diagnosis=flow,target_context=target_context,
             direct_vs_downstream=dict(direct_down=direct['weak_payoff_gain'],direct_up=-direct['strong_payoff_drag'],
                                       downstream_down=delta['down']-direct['weak_payoff_gain'],downstream_up=delta['up']+direct['strong_payoff_drag']),
             limitation='Single consumed oracle market, one Active; not success-frequency evidence or a recurrent learned policy.')
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(out,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',delta=delta,direct=out['direct_vs_downstream'],
                         arms={tag:dict(terminal=arm['terminal'],submits=arm['submits'],score=arm['core_similarity'],
                                        both_positive_seconds=supp[tag]['both_positive_seconds'],
                                        intervals=len(supp[tag]['both_positive_intervals'])) for tag,arm in out['arms'].items()}),indent=2))


if __name__=='__main__':main()
