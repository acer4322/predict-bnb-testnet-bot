"""Audit the one collected V24 native job; never replay or fit locally."""
import collections
import hashlib
import json
import types
import verify_btc5m_unbudgeted_opportunity_v1 as unbudgeted
import verify_btc5m_active_repair_opportunity_v1 as previous
from prepare_btc5m_commitment_repair_probe_v1 import ROOT,R,PACKAGE,PARENT,BASE,JOB,STEM,START,read,sha,dump,compile_source
from aggregate_btc5m_exposure_intent_ablation_v1 import get,RET,STREAMS
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary
from btc5m_commitment_repair_probe_v1 import decide
from hft244_pair_route_legality_v1 import crossing_owners


def main():
    manifest=read(PACKAGE/'manifest.json');parent=read(PARENT/'manifest.json')
    assert all(sha(PACKAGE/n)==h for n,h in manifest['files'].items())
    assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert sha(BASE/'result.json')==manifest['baseline_result_sha256'] and sha(BASE/'clock_trace.json.gz')==manifest['baseline_trace_sha256']
    b,bt=get(BASE);n,nt=get(RET/JOB)
    assert n['theta']==b['theta'] and n['cash_budget_enabled'] is False
    transformed=compile_source(PACKAGE);worker='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+PACKAGE.name
    for a,c in [(str(PACKAGE).replace('\\','\\\\'),worker.replace('\\','\\\\')),(str(PACKAGE),worker),(PACKAGE.as_posix(),worker.replace('\\','/'))]:
        transformed=transformed.replace(a,c)
    assert n['clock_smoke']['transformed_source_sha256']==hashlib.sha256(transformed.encode()).hexdigest()
    first=nt['commitment_repair_first'];t=first['t'];obs=read(R/(STEM+'_OBSERVATION.json'))['first']
    for k,v in obs.items():
        if k not in ('seconds','book_source_ms'):previous.same_value(v,first[k])
    keys=(*STREAMS,'intent','money_rows','money_events','demand_rows','demand_events','demand_owner_rows',
          'demand_plan_rows','demand_maintenance_rows','profit_budget_rows')
    prefix={k:[r for r in bt[k] if r['t']<t]==[r for r in nt[k] if r['t']<t] for k in keys}
    assert all(prefix.values()),[k for k,v in prefix.items() if not v]
    assert bt['opportunity_rows']==nt['opportunity_rows'] and bt['opportunity_submissions']==nt['opportunity_submissions']
    assert next(p['operations'] for p in bt['plans'] if p['t']==t)==first['original_operations']
    assert first['state']==next(d['state'] for d in bt['demand_rows'] if d['t']==t)
    assert n['commitment_repair_first']==first and n['commitment_repair_submissions']==nt['commitment_repair_submissions']
    source=read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    books={x['received_ms']:x for x in source['books']};plans={p['t']:p['operations'] for p in nt['plans']}
    submissions=nt['commitment_repair_submissions'];sub_at={s['t']:s for s in submissions}
    assert len(sub_at)==len(submissions)
    active=nt['opportunity_submissions'][0]['key']
    terminal={}
    for row in nt['demand_owner_rows']:
        if row['state']=='TERMINAL':terminal.setdefault(row['key'],row['t'])
    checked=0
    for row in nt['commitment_repair_rows']:
        t=row['t'];book=books[t];assert book['source_ms']<=t
        previous.close(row['ask'],round(1-book['best_bid'],10))
        outstanding=[s['key'] for s in submissions if s['t']<t and terminal.get(s['key'],float('inf'))>t]
        assert len(outstanding)<=1 and row['outstanding']==outstanding
        expected=decide(row['state'],row['original_operations'],row['price'],row['ask'],t>=terminal[active],outstanding,crossing_owners)
        for k,v in expected.items():previous.same_value(v,row[k])
        if row['eligible']:
            op=sub_at[t];assert op['role']=='PASSIVE_CURRENT_COMMITMENT_REPAIR' and op['qty']==15.
            assert plans[t][:-1]==row['original_operations'] and plans[t][-1]=={k:v for k,v in op.items() if k!='t'}
            checked+=1
        else:assert plans[t]==row['original_operations']
    assert checked==len(submissions)
    factory=types.FunctionType(unbudgeted.auditor.__code__,dict(unbudgeted.auditor.__globals__,PACKAGE=PACKAGE,folder=lambda tag:RET/JOB),'bound_commitment_auditor')
    verified=factory()('active',n,nt)
    extra_keys={s['key'] for s in submissions}
    extras=[c for c in verified['receipts']['orders'] if c['key'] in extra_keys]
    assert len(extras)==len(submissions)
    raw=[r for r in nt['demand_final']['full_raw_receipts'] if r['key'] in extra_keys and r['qty']>0]
    limits={s['key']:s['price'] for s in submissions}
    assert all(r['maker']==1 and r['contractPrice']<=limits[r['key']]+1e-8 for r in raw)
    normal=geometry(b['final_inventory'],b['final_cost']);final=verified['terminal']
    delta={k:final[k]-normal[k] for k in ('up','down','cost','up_net')}
    diagnose=types.FunctionType(previous.diagnose.__code__,dict(previous.diagnose.__globals__,STEM=STEM),'commitment_flow')
    flow=diagnose(dict(control=(b,bt),active=(n,nt)),delta)
    fd=read(R/(STEM+'_FLOW_DIAGNOSTIC.json'))
    fd['interpretation']='Same Active and cash gates OFF. Recurrent commitment repair admissions and subsequent OWN/maintenance/native feedback, not a cash budget change.'
    dump('FLOW_DIAGNOSTIC',fd)
    basepath=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),bt['states'],START,START+300000)
    direct_down=sum(c['filled']-c['payment']-c['fees'] for c in extras)
    direct_up=-sum(c['payment']+c['fees'] for c in extras)
    counts=collections.Counter(r['reason'] for r in nt['commitment_repair_rows'])
    episodes=[]
    for s,owner in zip(submissions,[next(c for c in extras if c['key']==x['key']) for x in submissions]):
        entry=next(r for r in nt['commitment_repair_rows'] if r['t']==s['t'])
        episodes.append(dict(seconds=(s['t']-START)/1000,submission=s,owner=owner,
            old_filled_quantity_capacity=entry['old_filled_quantity_capacity'],confirmed_payoff_floor=entry['confirmed_payoff_floor'],
            conditional_payoff=entry['conditional_payoff'],deterioration_debt=entry['deterioration_debt'],
            usable_pending_up_cash=entry['usable_pending_up_cash']))
    post=read(R/(STEM+'_POSTCHECK.json'));assert post['status']=='PASS' and post['native_sha256']==manifest['native_sha256']
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert n['sizing_installation']==pins and all(sha(ROOT/'tools'/name)==p['before'] for name,p in pins.items())
    out=dict(status='COMPLETE',verification='PASS',new_native_jobs=1,reused_native_controls=1,local_native_jobs=0,
        model_fits=0,parameter_search=0,runtime_eligible=False,manifest_sha256=sha(PACKAGE/'manifest.json'),
        prefix=prefix,existing_active_unchanged=True,decision_rows_checked=len(nt['commitment_repair_rows']),
        reason_counts=dict(counts),maximum_extra_nonterminal_owners=1,extra_orders=episodes,
        extra_totals=dict(submitted=len(extras),filled_qty=sum(c['filled'] for c in extras),
                          payment=sum(c['payment'] for c in extras),raw_positive_receipts=len(raw)),
        baseline=dict(terminal=normal,trajectory=basepath,submits=b['submits'],passive_submits=b['passive_native_submits'],
                      active_submits=b['active_native_submits'],core_similarity=b['core_similarity']),
        candidate=verified,terminal_delta=delta,flow_diagnosis=flow,
        every_confirmed_state_identical=bt['states']==nt['states'],
        direct_vs_downstream=dict(direct_down=direct_down,direct_up=direct_up,
            downstream_down=delta['down']-direct_down,downstream_up=delta['up']-direct_up),
        limitations='Current-commitment floor preservation, not a learned terminal payoff target. Full-fill commitment scenario is conditional. One consumed market, repeated owner admissions under unchanged maintenance; no architecture or cross-market success claim.')
    dump('RESULT',out)
    print(json.dumps(dict(status='PASS',extra_totals=out['extra_totals'],reason_counts=dict(counts),
        terminal_delta=delta,baseline=normal,candidate=final,
        both_positive_seconds=dict(baseline=basepath['both_positive_seconds'],candidate=verified['trajectory']['both_positive_seconds']),
        direct_vs_downstream=out['direct_vs_downstream'],flow_changed_buckets=flow['flow_changed_buckets'])))


if __name__=='__main__':main()
