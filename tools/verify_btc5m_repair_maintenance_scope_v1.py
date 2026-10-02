"""Audit V27 maintenance scope against pinned V26, with both Actives identical."""
import argparse
import hashlib
import json
import math
import types
from prepare_btc5m_repair_maintenance_scope_v1 import ROOT,R,PARENT,BASE,START,ARMS,config,read,sha,load,compile_source,get,RET,dump_for
from verify_btc5m_reexposure_coordination_v1 import accounting_auditor
import verify_btc5m_active_repair_opportunity_v1 as previous
from aggregate_btc5m_exposure_intent_ablation_v1 import STREAMS
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary
from hft244_pair_route_legality_v1 import crossing_owners


def verify(arm):
    c=config(arm);p=c['PACKAGE'];stem=c['STEM'];folder=RET/c['JOB'];dump=lambda s,o:dump_for(stem,s,o)
    m=read(p/'manifest.json');parent=read(PARENT/'manifest.json')
    assert sha(PARENT/'manifest.json')==m['parent_manifest_sha256']
    assert all(sha(p/n)==h for n,h in m['files'].items()) and all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert all(sha(p/n)==h for n,h in parent['files'].items() if n!='money_runner.py')
    assert sha(BASE/'result.json')==m['baseline_result_sha256'] and sha(BASE/'clock_trace.json.gz')==m['baseline_trace_sha256']
    b,bt=get(BASE);n,nt=get(folder);assert n['theta']==b['theta'] and not n['cash_budget_enabled']
    assert all(n[k] is None for k in ('capital_cap',))
    source=compile_source(p);remote='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+p.name
    for a,z in [(str(p).replace('\\','\\\\'),remote.replace('\\','\\\\')),(str(p),remote),(p.as_posix(),remote.replace('\\','/'))]:source=source.replace(a,z)
    assert n['clock_smoke']['transformed_source_sha256']==hashlib.sha256(source.encode()).hexdigest()
    obs=read(R/(stem+'_OBSERVATION.json'));first=obs['first'];cut=first['t'];gate=obs['gate_terminal_t']
    plans={r['t']:r['operations'] for r in nt['plans']};bp={r['t']:r['operations'] for r in bt['plans']}
    assert plans.keys()==bp.keys() and min(t for t in plans if plans[t]!=bp[t])==cut
    keys=(*STREAMS,'intent','money_rows','money_events','demand_rows','demand_events','demand_owner_rows',
        'demand_plan_rows','demand_maintenance_rows','profit_budget_rows','commitment_repair_rows','commitment_repair_submissions')
    prefix={k:[r for r in bt[k] if r['t']<cut]==[r for r in nt[k] if r['t']<cut] for k in keys}
    assert all(prefix.values()),[k for k,v in prefix.items() if not v]
    for k in ('opportunity_rows','opportunity_submissions','coordination_rows','coordination_episode','coordination_submissions'):
        assert bt[k]==nt[k], k
    first_active=nt['opportunity_submissions'][0]['key'];second=nt['coordination_submissions'][0]['key']
    terminal={}
    for r in nt['demand_owner_rows']:
        if r['state']=='TERMINAL':terminal.setdefault(r['key'],r['t'])
    assert terminal[second]==gate
    for key in (first_active,second):
        for stream in ('demand_owner_rows',):assert [r for r in bt[stream] if r['key']==key]==[r for r in nt[stream] if r['key']==key]
        for stream in ('full_raw_receipts','all_final_carriers'):
            assert [r for r in bt['demand_final'][stream] if r['key']==key]==[r for r in nt['demand_final'][stream] if r['key']==key]
    src=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(src)==m['oracle_source_sha256'];books={r['received_ms']:r for r in read(src)['books']}
    demand={r['t']:r for r in nt['demand_rows']};maint={(r['t'],r['key']):r for r in nt['demand_maintenance_rows']}
    repair=load('audit_v27_parent_'+arm,p/'commitment_repair.py');scope=load('audit_v27_scope_'+arm,p/'maintenance_scope.py')
    assert scope.MODE==ARMS[arm] and repair.CONCURRENT and repair.LEGAL_QUOTE
    submissions=nt['commitment_repair_submissions'];births={r['key']:r for r in submissions}
    for row in nt['commitment_repair_rows']:
        t=row['t'];book=books[t];assert book['source_ms']<=t and row['state']==demand[t]['state']
        outstanding=[s['key'] for s in submissions if s['t']<t and terminal.get(s['key'],float('inf'))>t]
        assert row['outstanding']==outstanding
        expected=repair.decide(row['state'],row['original_operations'],demand[t]['eligibility']['price'],round(1-book['best_bid'],10),t>=terminal[first_active],outstanding,crossing_owners)
        for k,v in expected.items():previous.same_value(v,row[k])
        actual=[o for o in plans[t] if not (o['kind']=='NEW' and o['key']==second)]
        if row['eligible']:
            sub=next(s for s in submissions if s['t']==t)
            assert actual[:-1]==row['original_operations'] and actual[-1]=={k:v for k,v in sub.items() if k!='t'}
        else:assert actual==row['original_operations']
    # Inherited overlay maintenance is used only before the new canonical gate.
    assert nt['commitment_maintenance_rows']==[r for r in bt['commitment_maintenance_rows'] if r['t']<gate]
    threshold=.01*(1+math.log1p(math.exp(n['theta'][11])))
    owner_routes={r['key']:r['route'] for r in nt['demand_final']['all_final_carriers']}
    scoped={(r['t'],r['key']):r for r in nt['maintenance_scope_rows']}
    expected_keys={(t,k) for (t,k),r in maint.items() if t>=gate and r['side']=='DOWN' and owner_routes[k]=='PASSIVE'}
    assert scoped.keys()==expected_keys and len(scoped)==len(nt['maintenance_scope_rows'])
    actions=[]
    for (t,key),row in scoped.items():
        assert row['active_key']==second and row['active_state']=='TERMINAL' and row['active_filled']==130.45
        assert row['owner_route']=='PASSIVE' and row['is_extra']==(key in births and births[key]['t']<t)
        previous.close(row['threshold'],threshold);previous.close(row['quote']['raw_price'],demand[t]['eligibility']['price'])
        book=books[t];assert book['source_ms']<=t
        expected=scope.decide(row['quote']['raw_price'],round(1-book['best_bid'],10),row['limit'],threshold,row['is_extra'],repair.legal_quote)
        for k,v in expected.items():previous.same_value(v,row[k])
        assert row['raw_stale']==(abs(row['limit']-row['quote']['raw_price'])>threshold+1e-9)
        observed=maint[(t,key)];assert observed['stale']==row['stale']
        previous.close(observed['current_passive_price'],row['price']);assert observed['owner_state']==row['owner_state']
        cancelled=any(o['kind']=='CANCEL' and o['key']==key for o in plans[t])
        must_cancel=(row['stale'] or observed['surplus']) and observed['cancellable'] and row['owner_state']!='CANCEL_PENDING'
        if must_cancel:assert cancelled
        if row['changed_stale'] and observed['cancellable'] and not observed['surplus'] and row['owner_state']!='CANCEL_PENDING':
            assert cancelled==row['stale']
            actions.append(dict(t=t,seconds=(t-START)/1000,key=key,is_extra=row['is_extra'],cancelled=cancelled,
                old_stale=row['original_stale'],new_stale=row['stale'],old_price=row['original_price'],new_price=row['price']))
    actual_first=scoped[(cut,first['key'])]
    for k in ('mode','quote','original_price','original_stale','price','stale','is_extra'):previous.same_value(first[k],actual_first[k])
    assert min(r['t'] for r in actions)==cut
    verified=accounting_auditor(p,folder)('active',n,nt)
    normal=geometry(b['final_inventory'],b['final_cost']);delta={k:verified['terminal'][k]-normal[k] for k in ('up','down','cost','up_net')}
    diagnose=types.FunctionType(previous.diagnose.__code__,dict(previous.diagnose.__globals__,STEM=stem),'maintenance_flow')
    flow=diagnose(dict(control=(b,bt),active=(n,nt)),delta)
    fd=read(R/(stem+'_FLOW_DIAGNOSTIC.json'));fd['interpretation']='Same original and restore Active receipts, maintenance scope only; endogenous later Passive execution and OWN changes. No cash cap or new sizing rule.';dump('FLOW_DIAGNOSTIC',fd)
    post=read(R/(stem+'_POSTCHECK.json'));assert post['status']=='PASS' and post['native_sha256']==m['native_sha256']
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert n['sizing_installation']==pins and all(sha(ROOT/'tools'/name)==p['before'] for name,p in pins.items())
    out=dict(status='COMPLETE',verification='PASS',arm=arm,manifest_sha256=sha(p/'manifest.json'),prefix=prefix,
        new_native_jobs=1,local_native_jobs=0,model_fits=0,parameter_search=0,runtime_eligible=False,
        first_difference_seconds=(cut-START)/1000,both_active_streams_and_receipts_identical=True,
        maintenance_scope_rows_checked=len(scoped),repair_rows_checked=len(nt['commitment_repair_rows']),
        changed_cancel_decisions=actions,baseline=dict(terminal=normal,trajectory=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),bt['states'],START,START+300000)),
        candidate=verified,terminal_delta=delta,flow_diagnosis=flow,
        limitations='Consumed single restore-Active market. Same repair degree, different post-canonical maintenance scope; no learned recurrent policy or Target objective.')
    dump('RESULT',out)
    print(json.dumps(dict(status='PASS',arm=arm,terminal=verified['terminal'],delta=delta,maintenance_rows=len(scoped),
        changed_cancel_decisions=len(actions),trajectory={k:verified['trajectory'][k] for k in ('both_positive_seconds','negative_floor_area_currency_seconds','minimum_floor','reversed_net_seconds')},flow=flow)))
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('arm',choices=ARMS);verify(p.parse_args().arm)
