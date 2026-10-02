"""Read-only accounting and mechanism audit of each frozen V25 panel cell."""
import argparse
import collections
import hashlib
import json
import types
import verify_btc5m_unbudgeted_opportunity_v1 as unbudgeted
import verify_btc5m_active_repair_opportunity_v1 as previous
from prepare_btc5m_repair_constraints_v1 import ROOT,R,PARENT,BASE,START,ARMS,config,read,sha,load,compile_source,get,RET,dump_for
from aggregate_btc5m_exposure_intent_ablation_v1 import STREAMS
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary
from hft244_pair_route_legality_v1 import crossing_owners


def verify(arm):
    cfg=config(arm);package=cfg['PACKAGE'];job=cfg['JOB'];stem=cfg['STEM']
    dump=lambda suffix,obj:dump_for(stem,suffix,obj)
    manifest=read(package/'manifest.json');parent=read(PARENT/'manifest.json')
    assert sha(PARENT/'manifest.json')==manifest['parent_manifest_sha256']
    assert all(sha(package/n)==h for n,h in manifest['files'].items())
    assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert sha(BASE/'result.json')==manifest['baseline_result_sha256'] and sha(BASE/'clock_trace.json.gz')==manifest['baseline_trace_sha256']
    assert sha(package/'commitment_base.py')==sha(PARENT/'commitment_repair.py')
    b,bt=get(BASE);n,nt=get(RET/job)
    assert n['theta']==b['theta'] and n['cash_budget_enabled'] is False
    transformed=compile_source(package);worker='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+package.name
    for a,c in [(str(package).replace('\\','\\\\'),worker.replace('\\','\\\\')),(str(package),worker),(package.as_posix(),worker.replace('\\','/'))]:transformed=transformed.replace(a,c)
    assert n['clock_smoke']['transformed_source_sha256']==hashlib.sha256(transformed.encode()).hexdigest()
    bp={p['t']:p['operations'] for p in bt['plans']};np={p['t']:p['operations'] for p in nt['plans']}
    assert bp.keys()==np.keys()
    difference=min(t for t in bp if bp[t]!=np[t]);obs=read(R/(stem+'_OBSERVATION.json'))
    assert difference==obs['t'],(difference,obs['t'])
    keys=(*STREAMS,'intent','money_rows','money_events','demand_rows','demand_events','demand_owner_rows',
          'demand_plan_rows','demand_maintenance_rows','profit_budget_rows')
    prefix={k:[r for r in bt[k] if r['t']<difference]==[r for r in nt[k] if r['t']<difference] for k in keys}
    assert all(prefix.values()),[k for k,v in prefix.items() if not v]
    assert bt['opportunity_rows']==nt['opportunity_rows'] and bt['opportunity_submissions']==nt['opportunity_submissions']
    module=load('audit_constraints_'+arm,package/'commitment_repair.py')
    assert (module.CONCURRENT,module.LEGAL_QUOTE)==ARMS[arm]
    source=read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    assert sha(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')==manifest['oracle_source_sha256']
    books={x['received_ms']:x for x in source['books']};demand={d['t']:d for d in nt['demand_rows']}
    submissions=nt['commitment_repair_submissions'];sub_at={s['t']:s for s in submissions};terminal={}
    assert len(sub_at)==len(submissions)
    for row in nt['demand_owner_rows']:
        if row['state']=='TERMINAL':terminal.setdefault(row['key'],row['t'])
    active=nt['opportunity_submissions'][0]['key'];peak=0;concurrent_births=0;quote_births=0
    for row in nt['commitment_repair_rows']:
        t=row['t'];book=books[t];assert book['source_ms']<=t
        previous.close(row['ask'],round(1-book['best_bid'],10))
        assert row['state']==demand[t]['state']
        outstanding=[s['key'] for s in submissions if s['t']<t and terminal.get(s['key'],float('inf'))>t]
        assert row['outstanding']==outstanding and not row['missing_reserved_owners']
        raw=demand[t]['eligibility']['price']
        expected=module.decide(row['state'],row['original_operations'],raw,row['ask'],t>=terminal[active],outstanding,crossing_owners)
        for k,v in expected.items():previous.same_value(v,row[k])
        if row['eligible']:
            op=sub_at[t];assert op['qty']==15. and op['role']=='PASSIVE_CURRENT_COMMITMENT_REPAIR'
            assert np[t][:-1]==row['original_operations'] and np[t][-1]=={k:v for k,v in op.items() if k!='t'}
            concurrent_births+=bool(outstanding);quote_births+=row['quote']['changed']
        else:assert np[t]==row['original_operations']
        peak=max(peak,len(outstanding)+int(row['eligible']))
        if module.CONCURRENT:assert row['reason']!='WAIT_FOR_EXTRA_OWNER_TERMINAL'
        else:assert len(outstanding)+int(row['eligible'])<=1
        if module.LEGAL_QUOTE and row['active_confirmed'] and raw<.07-1e-8 and row['ask']>.07+1e-8:
            assert row['price']==.07 and row['reason']!='FIXED15_NEW_PRICE_INVALID'
    assert len(submissions)==sum(r['eligible'] for r in nt['commitment_repair_rows'])
    assert n['commitment_repair_submissions']==submissions and n['commitment_repair_first']==nt['commitment_repair_first']
    maintenance={ (r['t'],r['key']):r for r in nt['demand_maintenance_rows'] }
    kept=[]
    for row in nt['commitment_maintenance_rows']:
        assert module.LEGAL_QUOTE and row['key'] in {s['key'] for s in submissions}
        ask=round(1-books[row['t']]['best_bid'],10)
        expected=module.legal_quote(row['quote']['raw_price'],ask)
        previous.same_value(row['quote'],expected)
        assert row['old_stale']==(abs(row['limit']-expected['raw_price'])>row['threshold']+1e-9)
        assert row['new_stale']==(abs(row['limit']-expected['price'])>row['threshold']+1e-9)
        original=maintenance[(row['t'],row['key'])]
        assert original['stale']==row['new_stale'] and original['current_passive_price']==expected['price']
        if row['old_stale'] and not row['new_stale'] and not original['surplus'] and original['cancellable']:
            assert not any(o['kind']=='CANCEL' and o['key']==row['key'] for o in np[row['t']])
            kept.append(dict(t=row['t'],key=row['key'],old_price=expected['raw_price'],new_price=expected['price']))
    factory=types.FunctionType(unbudgeted.auditor.__code__,dict(unbudgeted.auditor.__globals__,PACKAGE=package,folder=lambda tag:RET/job),'bound_constraints_accounting')
    verified=factory()('active',n,nt)
    extras=[c for c in verified['receipts']['orders'] if c['key'] in {s['key'] for s in submissions}]
    assert len(extras)==len(submissions)
    normal=geometry(b['final_inventory'],b['final_cost']);final=verified['terminal']
    delta={k:final[k]-normal[k] for k in ('up','down','cost','up_net')}
    diagnose=types.FunctionType(previous.diagnose.__code__,dict(previous.diagnose.__globals__,STEM=stem),'constraints_flow')
    flow=diagnose(dict(control=(b,bt),active=(n,nt)),delta)
    diagnostic=read(R/(stem+'_FLOW_DIAGNOSTIC.json'))
    diagnostic['interpretation']='V24 reused baseline; same Active and cash gates OFF. Declared repair concurrency/quote corrections and subsequent OWN/native feedback only.'
    dump('FLOW_DIAGNOSTIC',diagnostic)
    post=read(R/(stem+'_POSTCHECK.json'));assert post['status']=='PASS' and post['native_sha256']==manifest['native_sha256']
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert n['sizing_installation']==pins and all(sha(ROOT/'tools'/name)==p['before'] for name,p in pins.items())
    direct_down=sum(c['filled']-c['payment']-c['fees'] for c in extras)
    direct_up=-sum(c['payment']+c['fees'] for c in extras)
    out=dict(status='COMPLETE',verification='PASS',arm=arm,new_native_jobs=1,local_native_jobs=0,model_fits=0,
        parameter_search=0,runtime_eligible=False,manifest_sha256=sha(package/'manifest.json'),first_difference_seconds=(difference-START)/1000,
        prefix=prefix,existing_active_unchanged=True,decision_rows_checked=len(nt['commitment_repair_rows']),
        reason_counts=dict(collections.Counter(r['reason'] for r in nt['commitment_repair_rows'])),
        peak_extra_nonterminal_owners=peak,concurrent_extra_births=concurrent_births,legal_quote_extra_births=quote_births,
        maintenance_rows_checked=len(nt['commitment_maintenance_rows']),prevented_stale_cancels=kept,
        extra_orders=extras,extra_totals=dict(submitted=len(extras),filled_qty=sum(c['filled'] for c in extras),payment=sum(c['payment'] for c in extras)),
        baseline=dict(terminal=normal,trajectory=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),bt['states'],START,START+300000)),
        candidate=verified,terminal_delta=delta,flow_diagnosis=flow,
        extra_order_contributions=dict(down=direct_down,up=direct_up,scope='Gross contribution of all overlay owners, not incremental vs V24 which already had overlay fills'),
        limitations='Engineering constraint audit on one consumed market. Full pending UP scenario is not a prediction. No absolute loss-repair objective or Target architecture identified; structural pass does not imply economic improvement.')
    dump('RESULT',out)
    summary={k:out[k] for k in ('arm','verification','first_difference_seconds','extra_totals','peak_extra_nonterminal_owners','concurrent_extra_births','legal_quote_extra_births','terminal_delta')}
    summary.update(terminal=final,trajectory={k:verified['trajectory'][k] for k in ('both_positive_seconds','negative_floor_area_currency_seconds','minimum_floor','reversed_net_seconds')},
                   prevented_stale_cancel_frames=len(kept),flow_changed_buckets=flow['flow_changed_buckets'])
    print(json.dumps(summary));return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('arm',choices=ARMS);a=p.parse_args();verify(a.arm)
