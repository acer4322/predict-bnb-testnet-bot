"""Audit one V26 replay, separating the original and additional Active owners."""
import argparse
import collections
import hashlib
import inspect
import json
import types
import verify_btc5m_unbudgeted_opportunity_v1 as unbudgeted
import verify_btc5m_active_repair_opportunity_v1 as previous
from prepare_btc5m_reexposure_coordination_v1 import ROOT,R,PARENT,BASE,START,ARMS,config,read,sha,load,compile_source,get,RET,dump_for,once
from aggregate_btc5m_exposure_intent_ablation_v1 import STREAMS
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary
from hft244_pair_route_legality_v1 import crossing_owners


def accounting_auditor(package,folder):
    source=inspect.getsource(unbudgeted.auditor)
    marker="    module=load('verified_unbudgeted_decide',PACKAGE/'active_opportunity.py')"
    extra="    source=once(source,\"assert len(active) == result['active_native_submits'] == (tag=='active')\",\"assert len(active) == result['active_native_submits'] == 2\")\n"
    source=once(source,marker,extra+marker)
    ns=dict(unbudgeted.__dict__,PACKAGE=package,folder=lambda tag:folder)
    exec(compile(source,'explicit_two_active_owner_audit','exec'),ns)
    return ns['auditor']()


def verify(arm):
    c=config(arm);package=c['PACKAGE'];job=c['JOB'];stem=c['STEM'];dump=lambda suffix,obj:dump_for(stem,suffix,obj)
    m=read(package/'manifest.json');parent=read(PARENT/'manifest.json')
    assert sha(PARENT/'manifest.json')==m['parent_manifest_sha256']
    assert all(sha(package/n)==h for n,h in m['files'].items()) and all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert sha(BASE/'result.json')==m['baseline_result_sha256'] and sha(BASE/'clock_trace.json.gz')==m['baseline_trace_sha256']
    b,bt=get(BASE);n,nt=get(RET/job);assert n['theta']==b['theta'] and n['cash_budget_enabled'] is False
    transformed=compile_source(package);worker='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+package.name
    for a,d in [(str(package).replace('\\','\\\\'),worker.replace('\\','\\\\')),(str(package),worker),(package.as_posix(),worker.replace('\\','/'))]:transformed=transformed.replace(a,d)
    assert n['clock_smoke']['transformed_source_sha256']==hashlib.sha256(transformed.encode()).hexdigest()
    obs=read(R/(stem+'_OBSERVATION.json'));first=nt['coordination_rows'][-1];t=first['t']
    for k,v in obs['first'].items():
        if k not in ('seconds','book_source_ms'):previous.same_value(v,first[k])
    previous.same_value(obs['episode'],nt['coordination_episode'])
    assert n['coordination_episode']==nt['coordination_episode'] and n['coordination_submissions']==nt['coordination_submissions']
    assert len(nt['coordination_submissions'])==1 and sum(r['eligible'] for r in nt['coordination_rows'])==1
    keys=(*STREAMS,'intent','money_rows','money_events','demand_rows','demand_events','demand_owner_rows',
        'demand_plan_rows','demand_maintenance_rows','profit_budget_rows','commitment_repair_rows','commitment_repair_submissions','commitment_maintenance_rows')
    prefix={k:[r for r in bt[k] if r['t']<t]==[r for r in nt[k] if r['t']<t] for k in keys}
    assert all(prefix.values()),[k for k,v in prefix.items() if not v]
    assert bt['opportunity_rows']==nt['opportunity_rows'] and bt['opportunity_submissions']==nt['opportunity_submissions']
    plans={p['t']:p['operations'] for p in nt['plans']};baseplans={p['t']:p['operations'] for p in bt['plans']}
    assert min(k for k in plans if plans[k]!=baseplans[k])==t
    added=nt['coordination_submissions'][0];key=added['key']
    assert plans[t][:-1]==baseplans[t]==first['original_operations'] and plans[t][-1]=={k:v for k,v in added.items() if k!='t'}
    assert added['qty']==first['quantity'] and added['route']=='ACTIVE'
    source=read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    assert sha(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')==m['oracle_source_sha256']
    books={x['received_ms']:x for x in source['books']};demand={d['t']:d for d in nt['demand_rows']};terminal={}
    for row in nt['demand_owner_rows']:
        if row['state']=='TERMINAL':terminal.setdefault(row['key'],row['t'])
    active=nt['opportunity_submissions'][0]['key'];module=load('coordinator_check_'+arm,package/'coordination.py')
    assert module.MODE==ARMS[arm]
    prev=None;seen=False;episode=None
    for row in nt['coordination_rows']:
        when=row['t'];state=row['state'];confirmed=when>=terminal[active];book=books[when]
        assert book['source_ms']<=when and state==demand[when]['state']
        if episode is None:episode=module.detect(prev,state,confirmed,seen,when)
        if confirmed and prev and state['inv']['DOWN']>prev['inv']['DOWN']+1e-8:seen=True
        prev={k:state[k] for k in ('inv','cost','payoff')}
        expected=module.decide(state,row['original_operations'],round(1-book['best_bid'],10),dict(book['bids'])[book['best_bid']],episode,confirmed,crossing_owners)
        for k,v in expected.items():previous.same_value(v,row[k])
    assert episode==nt['coordination_episode']
    # The existing V25 repair actor must still produce its exact pure decisions.
    repair=load('v25_repair_check_'+arm,package/'commitment_repair.py');submissions=nt['commitment_repair_submissions']
    for row in nt['commitment_repair_rows']:
        when=row['t'];book=books[when]
        outstanding=[s['key'] for s in submissions if s['t']<when and terminal.get(s['key'],float('inf'))>when]
        assert row['outstanding']==outstanding and row['state']==demand[when]['state']
        expected=repair.decide(row['state'],row['original_operations'],demand[when]['eligibility']['price'],round(1-book['best_bid'],10),when>=terminal[active],outstanding,crossing_owners)
        for k,v in expected.items():previous.same_value(v,row[k])
        original_plan=[o for o in plans[when] if o.get('key')!=key or o['kind']!='NEW']
        if row['eligible']:
            assert original_plan[:-1]==row['original_operations']
            sub=next(s for s in submissions if s['t']==when)
            assert original_plan[-1]=={k:v for k,v in sub.items() if k!='t'}
        else:assert original_plan==row['original_operations']
    maintenance={(r['t'],r['key']):r for r in nt['demand_maintenance_rows']}
    for row in nt['commitment_maintenance_rows']:
        quote=repair.legal_quote(row['quote']['raw_price'],round(1-books[row['t']]['best_bid'],10))
        previous.same_value(quote,row['quote'])
        assert row['new_stale']==(abs(row['limit']-quote['price'])>row['threshold']+1e-9)
        observed=maintenance[(row['t'],row['key'])]
        assert observed['stale']==row['new_stale'] and observed['current_passive_price']==quote['price']
    verified=accounting_auditor(package,RET/job)('active',n,nt)
    active_owners=[o for o in verified['receipts']['orders'] if o['route']=='ACTIVE']
    assert {o['key'] for o in active_owners}=={active,key}
    new_owner=next(o for o in active_owners if o['key']==key)
    raw=[r for r in nt['demand_final']['full_raw_receipts'] if r['key']==key and r['qty']>0]
    assert all(r['contractPrice']<=added['price']+1e-8 for r in raw)
    old_owner=next(o for o in active_owners if o['key']==active)
    b_old=next(o for o in bt['demand_final']['all_final_carriers'] if o['key']==active)
    for k,v in b_old.items():previous.same_value(v,old_owner[k])
    normal=geometry(b['final_inventory'],b['final_cost']);final=verified['terminal']
    delta={k:final[k]-normal[k] for k in ('up','down','cost','up_net')}
    diagnose=types.FunctionType(previous.diagnose.__code__,dict(previous.diagnose.__globals__,STEM=stem),'reexposure_flow')
    flow=diagnose(dict(control=(b,bt),active=(n,nt)),delta)
    fd=read(R/(stem+'_FLOW_DIAGNOSTIC.json'));fd['interpretation']='Same V25 repaired controller, plus one receipt-triggered Active and endogenous future feedback. No cash budget or UP admission policy change.';dump('FLOW_DIAGNOSTIC',fd)
    post=read(R/(stem+'_POSTCHECK.json'));assert post['status']=='PASS' and post['native_sha256']==m['native_sha256']
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert n['sizing_installation']==pins and all(sha(ROOT/'tools'/name)==p['before'] for name,p in pins.items())
    direct_down=new_owner['filled']-new_owner['payment']-new_owner['fees'];direct_up=-new_owner['payment']-new_owner['fees']
    out=dict(status='COMPLETE',verification='PASS',arm=arm,new_native_jobs=1,local_native_jobs=0,model_fits=0,parameter_search=0,
        runtime_eligible=False,manifest_sha256=sha(package/'manifest.json'),prefix=prefix,original_active_unchanged=True,
        coordination_rows_checked=len(nt['coordination_rows']),v25_repair_rows_checked=len(nt['commitment_repair_rows']),
        maintenance_rows_checked=len(nt['commitment_maintenance_rows']),episode=episode,first=first,
        additional_active=new_owner,additional_raw_receipts=raw,
        baseline=dict(terminal=normal,trajectory=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),bt['states'],START,START+300000)),
        candidate=verified,terminal_delta=delta,flow_diagnosis=flow,
        direct_vs_downstream=dict(direct_down=direct_down,direct_up=direct_up,downstream_down=delta['down']-direct_down,downstream_up=delta['up']-direct_up),
        limitations='One extra Active at one causal reexposure episode in one consumed oracle market. Not a learned repeating Active policy or Target objective. Minimum vs restore references are preregistered mechanism contrasts.')
    dump('RESULT',out)
    print(json.dumps(dict(arm=arm,status='PASS',additional_active=new_owner,terminal=final,delta=delta,
        direct_vs_downstream=out['direct_vs_downstream'],trajectory={k:verified['trajectory'][k] for k in ('both_positive_seconds','negative_floor_area_currency_seconds','minimum_floor','reversed_net_seconds')})))
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('arm',choices=ARMS);a=p.parse_args();verify(a.arm)
