"""Complete the frozen panel without fitting or changing either native policy."""
import json

import btc5m_frozen_new_market_panel_v1 as d
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs
from verify_btc5m_tail_acquisition_v1 import direction_metrics
from verify_btc5m_transfer_components_v1 import same


def main():
    opening=d.read(d.R/(d.opening.STEM+'_RESULT.json'));refs={r['market']:r for r in opening['rows']}
    results=[];traces={};audits={};hashes={}
    for j in d.jobs():
        w=d.worker(j['version'],j);folder=d.R/'lan_worker_returns'/j['job_id']
        a=d.read(w.artifact(j,'AUDIT'));m=d.read(w.artifact(j,'MECHANISM_AUDIT'));post=d.read(w.artifact(j,'POSTCHECK'))
        assert a['execution_status']=='PASS' and m['status']=='PASS' and post['status']=='PASS'
        n=d.read(folder/'result.json');tr=d.read(folder/'clock_trace.json.gz');package=d.PACKAGES[j['version']]
        manifest=d.read(package/'manifest.json');assert all(d.sha(package/f)==h for f,h in manifest['files'].items())
        assert d.read(w.artifact(j,'COLLECT'))['status']['state']=='succeeded'
        assert n['theta']==manifest['theta'] and n['fixed_train_share_unit']==manifest['fixed_qref']
        public=d.read(package/f"inputs/public_{j['market']}.json.gz");start=public['market']['window_start_ms'];end=public['market']['window_end_ms']
        side=a['selected_direction'];target=d.opening.sign(refs[j['market']]['final_net'])
        if j['mode']!='NO_DIRECTION':assert side==target
        trkey=(j['market'],j['arm']);traces[trkey]=tr;audits[trkey]=a
        legs=canonical_legs(n,tr);window=[]
        for lo,hi in ((0,200),(200,300)):
            pp=[p for p in tr['plans'] if start+lo*1000<=p['t']<start+hi*1000]
            ff=[f for f in legs if start+lo*1000<=f['t']<start+hi*1000]
            window.append(dict(start=lo,end=hi,flow={s:dict(new=sum(o.get('side')==s for p in pp for o in p['operations'] if o['kind']=='NEW'),
                qty=sum(f['qty'] for f in ff if f['side']==s),cash=sum(f['cash'] for f in ff if f['side']==s)) for s in ('UP','DOWN')}))
        first_net=n['clock_smoke']['amplitude_birth']
        births=[dict(t=p['t'],**o) for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW']
        before=[o for o in births if first_net is None or o['t']<first_net['t']]
        one=dict(market=j['market'],arm=j['arm'],job_id=j['job_id'],selected_direction=side,target_final_direction=target,direction_matches_target=side==target,
            terminal=a['terminal'],trajectory=a['trajectory'],direction_aware=direction_metrics(tr['states'],start,end,side),
            payoff_over_cost={s:a['terminal'][s.lower()]/a['terminal']['cost'] if a['terminal']['cost'] else None for s in ('UP','DOWN')},
            frames=a['frames'],new_orders=a['submits'],active_orders=a['active_submits'],raw_receipts=a['raw_receipts'],native_seconds=a['native_elapsed_seconds'],
            first_confirmed_net=first_net,opening_new_before_first_net=before,flow_windows=window,mechanism=m,
            all_owners_terminal=a['all_owners_terminal'],all_pending_zero=a['all_pending_zero'],missing_terminal_clocks=a['terminal_timing']['missing_owner_observation_clocks'])
        results.append(one);hashes[j['job_id']]=dict(result=d.sha(folder/'result.json'),trace=d.sha(folder/'clock_trace.json.gz'),audit=d.sha(w.artifact(j,'AUDIT')),mechanism=d.sha(w.artifact(j,'MECHANISM_AUDIT')))
    pairs=[]
    for mid in d.MARKETS:
        a=traces[mid,'v43_known'];b=traces[mid,'v44_known'];c=traces[mid,'v44_no_direction']
        public=d.read(d.PACKAGES['V44']/f'inputs/public_{mid}.json.gz');start=public['market']['window_start_ms'];end=public['market']['window_end_ms']
        assert len(a['plans'])==len(b['plans'])==len(c['plans'])
        assert len(a['states'])==len(b['states'])==len(c['states'])
        first=next((i for i,(x,y) in enumerate(zip(a['plans'],b['plans'])) if x!=y),None)
        if first is not None:
            cut=b['plans'][first]['t'];assert 3*(cut-start)>=2*(end-start)
            same(a['plans'][:first],b['plans'][:first]);same([s for s in a['states'] if s['t']<=cut],[s for s in b['states'] if s['t']<=cut])
        else:
            same(a['plans'],b['plans']);same(a['states'],b['states'])
        aa=audits[mid,'v43_known'];bb=audits[mid,'v44_known']
        exact={k:b[k]==c[k] for k in ('plans','states','native_actions','demand_final')}
        pairs.append(dict(market=mid,first_tail_difference_seconds=(b['plans'][first]['t']-start)/1000 if first is not None else None,
            common_prefix_plans=first if first is not None else len(b['plans']),pre_intervention_states_exact=True,
            terminal_delta={k:bb['terminal'][k]-aa['terminal'][k] for k in ('up','down','cost','inventory_up','inventory_down')},
            floor_area_delta=bb['trajectory']['negative_floor_area_currency_seconds']-aa['trajectory']['negative_floor_area_currency_seconds'],
            both_positive_seconds_delta=bb['trajectory']['both_positive_seconds']-aa['trajectory']['both_positive_seconds'],
            known_vs_no_direction_action_equality=exact,
            independence='Identical action paths, when present, are one realized behavior, not independent confirmation of direction learning.'))
    out=dict(status='COMPLETE',execution='PASS',native_jobs=len(results),native_seconds=sum(r['native_seconds'] for r in results),
        markets=list(d.MARKETS),results=results,paired_tail_effect=pairs,
        target_reference=[{k:refs[mid][k] for k in ('market','inventory','cost','payoff','final_net','features')} for mid in d.MARKETS],
        source_hashes=hashes,opening_study_sha256=d.sha(d.R/(d.opening.STEM+'_RESULT.json')),
        selection='Two adjacent latest common-coverage markets selected before Target direction/PnL; not a representative test or a clean-success-case cohort.',
        interpretation='Evaluate conditional outcomes, direction, activity and path shape jointly. Current NO_DIRECTION has known UP bootstrap asymmetry. Private Target direction formation remains unidentified.',
        model_fits=0,parameter_search=0,local_native_jobs=0,plots=0,
        unchanged_research_constraints=['Exact work dust completion','Five-Active ceiling','Fixed15 versus historical Target ticket differences','No new general direction predictor'])
    d.dump('RESULT',out);d.dump('PROGRESS',dict(status='COMPLETE',submissions=6,native_completed=6,pending_jobs=[]))
    print(json.dumps(dict(status='COMPLETE',native_seconds=out['native_seconds'],rows=[{k:r[k] for k in ('market','arm','selected_direction','terminal','active_orders')} for r in results],pairs=pairs),ensure_ascii=False))


if __name__=='__main__':main()
