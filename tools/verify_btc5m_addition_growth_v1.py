"""V32 full accounting and causal growth rule verification, read-only."""
import collections
import hashlib
import math
import types
from prepare_btc5m_addition_growth_v1 import *
import verify_btc5m_held_amplitude_v1 as old
import verify_btc5m_active_repair_opportunity_v1 as previous
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary

END=START+300000


def bound(name):
    fn=getattr(old,name)
    return types.FunctionType(fn.__code__,dict(fn.__globals__,PACKAGE=PACKAGE,BASE=BASE,JOB=JOB,STEM=STEM),name)


def main():
    m=read(PACKAGE/'manifest.json');parent=read(PARENT/'manifest.json')
    assert sha(PARENT/'manifest.json')==m['parent_manifest_sha256']
    assert all(sha(PACKAGE/k)==v for k,v in m['files'].items())
    assert all(sha(PARENT/k)==v and sha(PACKAGE/k)==v for k,v in parent['files'].items() if k!='money_runner.py')
    assert sha(BASE/'result.json')==m['baseline_result_sha256'] and sha(BASE/'clock_trace.json.gz')==m['baseline_trace_sha256']
    b,bt=get(BASE);n,nt=get(RET/JOB);assert n['theta']==b['theta'] and n['capital_cap'] is None
    source_path=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(source_path)==m['oracle_source_sha256'];inp=read(source_path)
    assert [r['t'] for r in nt['plans']]==[r['received_ms'] for r in inp['books']]
    assert n['clock_smoke']['actual_replay_frames']==1462 and n['source_frames']==1487.
    source=compile_source(PACKAGE);remote='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+PACKAGE.name
    for a,z in [(str(PACKAGE).replace('\\','\\\\'),remote.replace('\\','\\\\')),(str(PACKAGE),remote),(PACKAGE.as_posix(),remote.replace('\\','/'))]:source=source.replace(a,z)
    assert hashlib.sha256(source.encode()).hexdigest()==n['clock_smoke']['transformed_source_sha256']
    runner=load('v32_intent',PACKAGE/'money_runner.py');intent=runner.ExposureIntent('ORACLE_UP')
    module=load('v32_growth',PACKAGE/'addition_growth.py');assert module.self_test()['status']=='PASS';controller=module.AdditionGrowth()
    grows=nt['addition_growth_rows'];mapped={r['t']:r for r in grows};observed={r['t']:r for r in nt['observations']}
    assert len(grows)==len(mapped)==len(nt['intent'])==1453
    for row in nt['intent']:
        inv=row['inv'];t=row['t'];cost=row['cost']
        legacy=math.tanh(n['theta'][3]*((inv['UP']-inv['DOWN'])/(1+sum(inv.values()))))
        previous.close(legacy,row['legacy_exposure'])
        exposure=intent.apply(legacy,0.,dict(t=t,index=row['index'],own_view=dict(inv=inv)))
        previous.close(exposure,row['applied_exposure'])
        raw=mapped[t]['original_desired'];gross=observed[t]['gross']
        previous.close(raw['UP'],gross*(1+exposure)/2);previous.close(raw['DOWN'],gross*(1-exposure)/2)
        effective=controller.update(t,inv,cost,raw)
        previous.same_value(controller.rows[-1],mapped[t]);previous.same_value(effective,observed[t]['desired'])
        previous.close(effective['DOWN'],raw['DOWN'])
        # Independent expression, including the stored causal first-repair anchor.
        gain=max(0.,inv['UP']-cost);loss=max(0.,cost-inv['DOWN']);weight=1. if loss<=1e-8 else gain/(gain+loss)
        previous.close(weight,mapped[t]['weight'])
        if controller.anchor:
            anchor=controller.anchor['original_strong_desired']
            previous.close(effective['UP'],min(raw['UP'],anchor+weight*max(0.,raw['UP']-anchor)))
    assert len(intent.amplitude_rows)==len(nt['exposure_amplitude_rows'])
    for a,z in zip(intent.amplitude_rows,nt['exposure_amplitude_rows']):previous.same_value(a,z)
    previous.same_value(intent.amplitude_birth,n['clock_smoke']['amplitude_birth'])
    bp={r['t']:r['operations'] for r in bt['plans']};np={r['t']:r['operations'] for r in nt['plans']}
    assert bp.keys()==np.keys();cut=min(t for t in np if np[t]!=bp[t])
    prefix={k:[r for r in bt[k] if r['t']<cut]==[r for r in nt[k] if r['t']<cut] for k in ('states','plans','native_actions','demand_owner_rows')}
    assert all(prefix.values()),prefix
    bd=next(r for r in bt['demand_rows'] if r['t']==cut);nd=next(r for r in nt['demand_rows'] if r['t']==cut)
    assert bd['state']==nd['state'];previous.close(bd['original_desired']['DOWN'],nd['original_desired']['DOWN'])
    checked=bound('accounting_auditor')()('active',n,nt)
    works=bound('automatic_audit')(n,nt);rules=bound('inherited')(n,nt)
    downstream=bound('bottlenecks')(n,nt,checked);dump('BOTTLENECKS',downstream)
    checked['receipt_time_trajectory']=old.automatic.receipt_time_path(n,nt)
    terminals=[o['terminal_observed_t'] for o in checked['receipts']['orders']];assert all(t is not None for t in terminals)
    delta={k:checked['terminal'][k]-geometry(b['final_inventory'],b['final_cost'])[k] for k in ('up','down','cost','up_net')}
    # This equality is an observed result of this contrast, not an invariant of future treatments.
    new_down=[r for r in previous.canonical_legs(n,nt) if r['side']=='DOWN']
    old_down=[r for r in previous.canonical_legs(b,bt) if r['side']=='DOWN']
    fields=('exchange_ts','receive_ts','qty','contractPrice','maker','fee')
    raw_down=lambda tr:[{k:r[k] for k in fields} for r in tr['demand_final']['full_raw_receipts'] if r['side']==-1 and r['qty']>1e-8]
    down_parity=dict(canonical_economic_legs_identical=new_down==old_down,canonical_legs=len(new_down),
        raw_economic_receipts_identical=raw_down(nt)==raw_down(bt),raw_positive_receipts=len(raw_down(nt)),
        note='Owner keys and unfilled NEW/cancel plans may differ. Economic receipt fields only; not full owner-trace equality.')
    diagnose=types.FunctionType(previous.diagnose.__code__,dict(previous.diagnose.__globals__,STEM=STEM),'v32_flow')
    flows=diagnose(dict(control=(b,bt),active=(n,nt)),delta)
    fd=read(R/(STEM+'_FLOW_DIAGNOSTIC.json'));fd['interpretation']='Only post-anchor UP desired growth is scaled. Endogenous later UP/DOWN execution, maintenance and Active occurrence may change. No cash budget change.';dump('FLOW_DIAGNOSTIC',fd)
    post=read(R/(STEM+'_POSTCHECK.json'));assert post['status']=='PASS' and post['native_sha256']==m['native_sha256']
    pins=read(R/'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')['private_file_changes']
    assert n['sizing_installation']==pins and all(sha(ROOT/'tools'/name)==pin['before'] for name,pin in pins.items())
    target_path=R/'BTC5M_POST_EXPOSURE_RESPONSE_V1_20260913_RESULT.json';target=read(target_path)['target']['2026085']
    phase=bound('phases');new_up=[o for p in nt['plans'] for o in p['operations'] if o['kind']=='NEW' and o['side']=='UP']
    out=dict(status='COMPLETE',verification='PASS',new_native_jobs=1,reused_native_controls=1,local_native_jobs=0,
        model_fits=0,parameter_search=0,figures=0,runtime_eligible=False,
        native_elapsed_seconds=read(R/(STEM+'_COLLECT.json'))['status']['elapsed_seconds'],manifest_sha256=sha(PACKAGE/'manifest.json'),
        growth_anchor=controller.anchor,growth_rows=len(grows),growth_reduced_rows=sum(r['effective_desired']['UP']<r['original_desired']['UP']-1e-8 for r in grows),
        first_plan_fork=dict(t=cut,seconds=(cut-START)/1000,prefix=prefix,same_pre_plan_state=True,baseline=bp[cut],candidate=np[cut],
            baseline_demand=bd,candidate_demand=nd,growth=mapped[cut]),
        baseline=dict(terminal=geometry(b['final_inventory'],b['final_cost']),trajectory=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),bt['states'],START,END),
            post_exposure=previous.analyze(previous.reconstruct(previous.canonical_legs(b,bt)),START,END),phases=phase(b,bt),core_similarity=b['core_similarity']),
        candidate=dict(checked,phases=phase(n,nt),up_new_orders=len(new_up)),terminal_delta=delta,flow_diagnosis=flows,
        finite_works=works,inherited_rules=rules,
        down_flow_parity=down_parity,bottlenecks={k:v for k,v in downstream.items() if k!='later_legal_active_observations'},
        timing=dict(actual_book_frames=1462,fixed_training_clock_denominator=1487.,last_canonical_terminal_seconds=(max(terminals)-START)/1000,
            note='Canonical cleanup observation after market end is not exact exchange terminal time.'),
        target=dict(terminal=target['terminal'],peak=target['peak'],source_sha256=sha(target_path)),
        worker_postcheck='PASS',limitations='Consumed single oracle-direction market; current payoff ratio is a heuristic controller, not learned Target intent. Cross-market matched direction/no-direction evaluation remains separate.')
    dump('RESULT',out);dump('PROGRESS',dict(status='COMPLETE',verification='PASS',job_id=JOB,new_native_jobs=1,model_fits=0,parameter_search=0))
    print(json.dumps(dict(status='PASS',terminal=checked['terminal'],delta=delta,first_plan_seconds=(cut-START)/1000,
        active_submits=checked['active_submits'],passive_submits=checked['passive_submits'],up_new_orders=len(new_up),core_similarity=checked['core_similarity'],
        trajectory={k:checked['trajectory'][k] for k in ('both_positive_seconds','negative_floor_area_currency_seconds','minimum_floor','reversed_net_seconds')}),indent=2))
    return out


if __name__=='__main__':main()
