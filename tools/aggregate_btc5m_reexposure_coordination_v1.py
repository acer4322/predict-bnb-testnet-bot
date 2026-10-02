"""Read-only V26 comparison and canonical recovery/re-exposure attribution."""
import json
from prepare_btc5m_reexposure_coordination_v1 import ROOT,R,BASE,START,ARMS,config,read,sha,get,RET,dump_for
from aggregate_btc5m_repair_constraints_v1 import summarize
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs,close

STEM='BTC5M_REEXPOSURE_COORDINATION_PANEL_V1_20260913'


def snap(s):
    return dict(t=s['t'],seconds=(s['t']-START)/1000,**geometry(s['inv'],s['cost']))


def event_path(trace,cut):
    initial=max((s for s in trace['states'] if s['t']<=cut),key=lambda s:s['t'])
    previous=initial;changes=[];transitions=[]
    for s in trace['states']:
        if s['t']<=cut:continue
        before,after=geometry(previous['inv'],previous['cost']),geometry(s['inv'],s['cost'])
        qty={k:s['inv'][k]-previous['inv'][k] for k in ('UP','DOWN')}
        cash=s['cost']-previous['cost']
        if any(abs(q)>1e-8 for q in qty.values()) or abs(cash)>1e-8:
            row=dict(t=s['t'],seconds=(s['t']-START)/1000,qty=qty,cost=cash,before=before,after=after,
                     payoff_delta={k:after[k]-before[k] for k in ('up','down')})
            changes.append(row)
            if (before['floor']>0)!=(after['floor']>0):transitions.append(row)
        previous=s
    metrics=path_summary(initial,trace['states'],cut,START+300000)
    return dict(initial=snap(initial),changed_events=changes,positive_transitions=transitions,
                summary={k:v for k,v in metrics.items() if k!='changed_rows'})


def role_comparison(baseline,candidate,cut):
    def births(trace):
        out={}
        for plan in trace['plans']:
            if plan['t']<cut:continue
            for op in plan['operations']:
                if op['kind']!='NEW' or op['side']!='DOWN' or op['route']!='PASSIVE':continue
                key=(plan['t'],op['price'],op['qty']);assert key not in out
                out[key]=op
        return out
    bb,cb=births(baseline),births(candidate)
    owners=[{o['key']:o for o in tr['demand_final']['all_final_carriers']} for tr in (baseline,candidate)]
    rows=[]
    for clock in sorted(bb.keys() & cb.keys()):
        before,after=bb[clock],cb[clock]
        if before['role']==after['role']:continue
        records=[]
        for index,(tr,op) in enumerate(((baseline,before),(candidate,after))):
            cancels=[x['t'] for x in tr['native_actions'] if x.get('key')==op['key'] and x['kind']=='CANCEL']
            row=next(x for x in tr['commitment_repair_rows'] if x['t']==clock[0])
            demand=next(x for x in tr['demand_rows'] if x['t']==clock[0])
            records.append(dict(birth=op,owner=owners[index][op['key']],cancel_times=cancels,
                quantity_capacity=demand['eligibility']['quantity_capacity'],payoff=row['state']['payoff'],
                pending_qty=row['pending_qty'],repair_reason=row['reason'],repair_need=row['money_quantity_need']))
        cancel=records[0]['cancel_times']
        witness={}
        if cancel:
            when=cancel[0]
            witness=dict(t=when,seconds=(when-START)/1000,
                baseline=[x for x in baseline['demand_maintenance_rows'] if x['t']==when and x['key']==before['key']],
                candidate=[x for x in candidate['demand_maintenance_rows'] if x['t']==when and x['key']==after['key']],
                candidate_overlay=[x for x in candidate['commitment_maintenance_rows'] if x['t']==when and x['key']==after['key']])
        rows.append(dict(t=clock[0],seconds=(clock[0]-START)/1000,price=clock[1],qty=clock[2],
            baseline=records[0],candidate=records[1],maintenance_at_baseline_cancel=witness,
            filled_delta=records[1]['owner']['filled']-records[0]['owner']['filled']))
    return rows


def main():
    b,bt=get(BASE);audits={};pairs=dict(baseline=(b,bt));cells=dict(baseline=summarize(b,bt));elapsed=0.
    assert read(R/'BTC5M_REPAIR_CONSTRAINTS_BOTH_V1_20260913_RESULT.json')['verification']=='PASS'
    cells['baseline']['reused_existing_result']=True
    for arm in ARMS:
        c=config(arm);a=read(R/(c['STEM']+'_RESULT.json'));m=read(c['PACKAGE']/'manifest.json')
        assert a['verification']=='PASS' and a['manifest_sha256']==sha(c['PACKAGE']/'manifest.json')
        assert all(sha(c['PACKAGE']/n)==h for n,h in m['files'].items())
        assert read(R/(c['STEM']+'_SUBMIT.json'))['accepted']
        status=read(R/(c['STEM']+'_COLLECT.json'))['status'];assert status['state']=='succeeded'
        assert read(R/(c['STEM']+'_POSTCHECK.json'))['status']=='PASS'
        elapsed+=status['elapsed_seconds'];n,nt=get(RET/c['JOB']);pairs[arm]=(n,nt);audits[arm]=a
        cells[arm]=dict(summarize(n,nt),native_seconds=status['elapsed_seconds'],
            additional_active=a['additional_active'],terminal_delta=a['terminal_delta'],
            direct_vs_downstream=a['direct_vs_downstream'],flow=a['flow_diagnosis'],
            manifest_sha256=a['manifest_sha256'],result_sha256=sha(RET/c['JOB']/'result.json'),
            trace_sha256=sha(RET/c['JOB']/'clock_trace.json.gz'))
    minimum,restore=audits['minimum'],audits['restore']
    assert minimum['episode']==restore['episode'] and minimum['first']['t']==restore['first']['t']
    cut=minimum['first']['t'];anchor=minimum['episode']['anchor_floor']
    paths={arm:event_path(t,cut) for arm,(_,t) in pairs.items()}
    immediate={};later_flows={}
    for arm,a in audits.items():
        owner=a['additional_active'];t=owner['first_canonical_t'];trace=pairs[arm][1]
        before=max((s for s in trace['states'] if s['t']<t),key=lambda s:s['t'])
        after=next(s for s in trace['states'] if s['t']==t)
        immediate[arm]=dict(before=snap(before),after=snap(after),reference_floor=anchor,
            reference_shortfall=anchor-geometry(after['inv'],after['cost'])['floor'],
            birth_to_canonical_ms=t-cut,
            down_shortfall_recovered_fraction=a['direct_vs_downstream']['direct_down']/(anchor-minimum['episode']['after']['payoff']['DOWN']))
        leg_totals={s:dict(qty=0.,cash=0.) for s in ('UP','DOWN')}
        for leg in canonical_legs(pairs[arm][0],trace):
            if leg['t']<=t:continue
            for k in ('qty','cash'):leg_totals[leg['side']][k]+=leg[k]
        after_g=geometry(after['inv'],after['cost']);terminal=cells[arm]['terminal']
        paid=sum(r['cash'] for r in leg_totals.values())
        for side in ('UP','DOWN'):close(terminal[side.lower()]-after_g[side.lower()],leg_totals[side]['qty']-paid)
        later_flows[arm]=dict(by_side=leg_totals,down_payoff_from_up_acquisition=-leg_totals['UP']['cash'],
            down_payoff_from_down_acquisition=leg_totals['DOWN']['qty']-leg_totals['DOWN']['cash'],
            net_down_change=terminal['down']-after_g['down'])
    # Isolate downstream PASSIVE differences independently of the added ACTIVE.
    passive={};birth_differences={};down_price_flows={}
    for arm,(n,trace) in pairs.items():
        # Canonical leg route records the realized maker/taker route.
        legs=[x for x in canonical_legs(n,trace) if x['route']=='MAKER' and x['t']>=cut]
        passive[arm]={s:dict(qty=sum(x['qty'] for x in legs if x['side']==s),
                            cash=sum(x['cash'] for x in legs if x['side']==s)) for s in ('UP','DOWN')}
        priced={}
        for leg in legs:
            if leg['side']!='DOWN' or leg['qty']<1e-8:continue
            price=str(round(leg['cash']/leg['qty'],8));entry=priced.setdefault(price,dict(qty=0.,cash=0.))
            for k in entry:entry[k]+=leg[k]
        down_price_flows[arm]=priced
    for arm in ARMS:
        birth_differences[arm]={s:{k:passive[arm][s][k]-passive['baseline'][s][k] for k in ('qty','cash')} for s in ('UP','DOWN')}
        change=birth_differences[arm];cash=sum(r['cash'] for r in change.values())
        for side in ('UP','DOWN'):
            close(change[side]['qty']-cash,audits[arm]['direct_vs_downstream']['downstream_'+side.lower()])
    roles={arm:role_comparison(bt,pairs[arm][1],cut) for arm in ARMS}
    # Same-time owner role differences can be replacements; reconcile by paid price too.
    price_deltas={arm:{price:{k:down_price_flows[arm].get(price,{}).get(k,0.)-
        down_price_flows['baseline'].get(price,{}).get(k,0.) for k in ('qty','cash')}
        for price in down_price_flows[arm].keys() | down_price_flows['baseline'].keys()} for arm in ARMS}
    for arm in ARMS:
        for k in ('qty','cash'):close(sum(v[k] for v in price_deltas[arm].values()),birth_differences[arm]['DOWN'][k])
    diagnosis=dict(status='PASS',new_native_jobs=0,trigger=minimum['episode'],immediate=immediate,
        paths=paths,later_canonical_flows=later_flows,passive_totals=passive,passive_deltas_vs_baseline=birth_differences,
        matched_passive_births_with_changed_role=roles,down_maker_price_flows=down_price_flows,
        down_maker_price_deltas=price_deltas,
        interpretation='Canonical flow accounting attributes actual paths; restoring an earlier floor is an experimental OWN reference, not a learned Target goal. Future UP pending supply is not credited as realized protection.')
    dump_for('BTC5M_REEXPOSURE_COORDINATION_PATH_V1_20260913','RESULT',diagnosis)
    out=dict(status='COMPLETE',verification='PASS',new_native_jobs=2,reused_native_controls=1,
        total_new_native_seconds=elapsed,local_native_jobs=0,model_fits=0,parameter_search=0,runtime_eligible=False,
        episode=minimum['episode'],cells=cells,immediate=immediate,later_canonical_flows=later_flows,
        passive_deltas_vs_baseline=birth_differences,
        changed_role_positive_fill_rows={arm:[dict(seconds=r['seconds'],price=r['price'],qty=r['qty'],
            baseline_key=r['baseline']['birth']['key'],candidate_key=r['candidate']['birth']['key'],filled_delta=r['filled_delta'])
            for r in rows if r['filled_delta']>1e-8] for arm,rows in roles.items()},
        diagnostics='BTC5M_REEXPOSURE_COORDINATION_PATH_V1_20260913_RESULT.json',
        limitations='One consumed direction-oracle market and one additional causal Active episode. No held-out result or learned recurrent coordinator; the more profitable arm need not preserve UP intent.')
    dump_for(STEM,'RESULT',out)
    progress=dict(status='COMPLETE',new_native_jobs=2,submit_count=2,resubmit_count=0,host_native_jobs=0,
        model_fits=0,parameter_search=0,elapsed_native_seconds=elapsed,next_native_dispatched=False,
        baseline='fixed15-core-loop-2026085-repair-both-20260913-v1',jobs={arm:config(arm)['JOB'] for arm in ARMS},
        research_selection='Neither new arm promoted to Target-matching or live policy; retain the fixed degree contrasts and diagnose direction-retention coordination.')
    (R/'REEXPOSURE_COORDINATION_PROGRESS_20260913.json').write_text(json.dumps(progress,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',elapsed=elapsed,
        table={arm:dict(up=c['terminal']['up'],down=c['terminal']['down'],up_net=c['terminal']['up_net'],
                       both_positive=c['trajectory']['both_positive_seconds'],core=c['core_similarity']) for arm,c in cells.items()},
        immediate=immediate,later=later_flows,passive_deltas=birth_differences,
        transitions={arm:[dict(seconds=e['seconds'],up=e['after']['up'],down=e['after']['down'],qty=e['qty'])
                          for e in p['positive_transitions']] for arm,p in paths.items()})))


if __name__=='__main__':main()
