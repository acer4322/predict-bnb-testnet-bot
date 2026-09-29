"""Offline paired structural results, with Target labels confined to this scorer."""
import collections
from prepare_btc5m_transfer_structural_v1 import *
from run_btc5m_transfer_structural_worker_v1 import jobs,artifact
from btc5m_exposure_suppression_metrics_v1 import geometry


def main():
    wave=jobs();rows=[];pairs=[]
    labels=read(R/'BTC5M_CORE_LOOP_TRANSFER_AB_V1_20260913_OFFLINE_LABELS.json')['labels']
    for j in wave[1:]:
        a=read(artifact(j,'AUDIT'));assert a['execution_status']=='PASS'
        n=read(R/'lan_worker_returns'/j['job_id']/'result.json')
        tr=read(R/'lan_worker_returns'/j['job_id']/'clock_trace.json.gz')
        # Legacy path helper's 'reversed' means physical DOWN dominance, not reversal of a DOWN intent.
        trajectory=dict(a['trajectory'])
        trajectory['physical_down_dominant_seconds']=trajectory.pop('reversed_net_seconds')
        trajectory['physical_down_loss_area_currency_seconds']=trajectory.pop('fixed_down_loss_area_currency_seconds')
        public=read(PACKAGE/f'inputs/public_{j["market"]}.json.gz')
        start,end=public['market']['window_start_ms'],public['market']['window_end_ms']
        points={start:dict(inv=dict(UP=0.,DOWN=0.))}
        points.update({x['t']:x for x in tr['states'] if start<=x['t']<=end})
        times=sorted(points);sign=1 if a['selected_direction']=='UP' else -1
        birth=a['direction_birth']['t'] if a['direction_birth'] else end
        trajectory['held_direction_reversed_seconds']=sum(max(0,nxt-max(t,birth))/1000 for t,nxt in zip(times,times[1:]+[end])
            if sign*(points[t]['inv']['UP']-points[t]['inv']['DOWN']) < -1e-8)
        first=read(PACKAGE/f'inputs/public_{j["market"]}.json.gz')['books'][0]
        first_plan=next((p for p in tr['plans'] if any(o['kind']=='NEW' for o in p['operations'])),None)
        bootstrap=dict(first_plan=first_plan,first_capacity_rows=[{k:v for k,v in x.items() if k!='state'}
            for x in tr['money_rows'] if first_plan is not None and x['t']==first_plan['t']],
            note='In NO_DIRECTION, the unresolved-role fallback is UP and the weak quantity cap is zero at empty inventory. This makes the first-OWN latch a default-UP bootstrap, not a market direction predictor.')
        rows.append(dict(market=j['market'],arm=j['arm'],direction=a['selected_direction'],
            target_direction=labels[str(j['market'])]['side'],direction_agrees=a['selected_direction']==labels[str(j['market'])]['side'],
            terminal=a['terminal'],activity=a['structural_activity'],flow=a['flow'],economic_flags=a['economic_flags'],
            trajectory=trajectory,post_worst=a['post_worst'],timing=a['terminal_timing'],coverage=a['frame_coverage'],
            held_amplitude=n['clock_smoke']['held_amplitude'],direction_birth=a['direction_birth'],
            first_book={k:first.get(k) for k in ('received_ms','source_ms','best_bid','best_ask')},bootstrap=bootstrap,
            seconds=a['native_elapsed_seconds'],submits=a['submits'],active_submits=a['active_submits'],
            raw_receipts=a['raw_receipts'],audit_file=artifact(j,'AUDIT').name,job_id=j['job_id']))
    for mid in MARKETS:
        a,b=[next(r for r in rows if r['market']==mid and r['arm']==arm) for arm in ('known','no_direction')]
        ta=read(R/'lan_worker_returns'/a['job_id']/'clock_trace.json.gz')
        tb=read(R/'lan_worker_returns'/b['job_id']/'clock_trace.json.gz')
        tests={k:ta[k]==tb[k] for k in ('plans','states','native_actions','demand_owner_rows','demand_final')}
        first_fork=next((dict(position=i,t=x['t'],known=x,no_direction=y) for i,(x,y) in enumerate(zip(ta['plans'],tb['plans'])) if x!=y),None)
        label=labels[str(mid)]
        pairs.append(dict(market=mid,known_direction=a['direction'],no_direction=b['direction'],
            same_direction=a['direction']==b['direction'],full_economic_execution_parity=tests,
            first_plan_fork=first_fork,target_offline_terminal=geometry(label['final_inventory'],label['cost']),
            no_direction_minus_known={k:b['terminal'][k]-a['terminal'][k] for k in ('up','down','cost','up_net')}))
    summary=dict(status='COMPLETE',verification='PASS',candidate='V32 frozen without further tuning',
        paired_native_jobs=8,port_parity_jobs=1,local_native_jobs=0,model_fits=0,parameter_search=0,figures=0,
        native_seconds=sum(x['seconds'] for x in rows),
        parity_seconds=read(artifact(wave[0],'AUDIT'))['native_elapsed_seconds'],
        execution_passes=len(rows),both_sides_filled=sum(x['activity']['both_sides_filled'] for x in rows),
        no_direction_label_agreements=sum(x['direction_agrees'] for x in rows if x['arm']=='no_direction'),
        no_direction_observed_sides=dict(collections.Counter(x['direction'] for x in rows if x['arm']=='no_direction')),
        no_direction_interpretation='No Target input, but default-UP startup plus weak-side zero capacity drives the first-OWN side. This is a fixed-start baseline, not autonomous Target direction identification.',
        both_negative_branches=sum(x['economic_flags']['both_terminal_branches_negative'] for x in rows),
        held_direction_reversals=sum(bool(x['economic_flags']['held_direction_reversed']) for x in rows),
        unresolved_final_owners=0,final_pending_qty_cash=0,
        missing_exact_terminal_clocks=sum(len(x['timing']['missing_owner_observation_clocks']) for x in rows),
        total_source_frames=sum(x['coverage']['source_frames'] for x in rows),
        total_closure_plans=sum(x['coverage']['closure_plans'] for x in rows),
        rows=rows,pairs=pairs,manifest_sha256=sha(PACKAGE/'manifest.json'),
        limitations=['Consumed four-market engineering transfer, not unseen graduation.',
            'Known arm sees final observed Target net side, not settlement winner.',
            'NO_DIRECTION first confirmed OWN side is a causal bootstrap baseline, not identified Target alpha.',
            'Native fee/queue/execution model remains inherited; no deployment or live setting change.',
            'Missing owner terminal observation timestamps stay UNKNOWN; canonical final state/accounting verified separately.',
            'Repair-then-add counts are descriptive fill transitions; neither profitable terminal branches nor repeated activity alone proves the target core loop.'])
    dump('RESULT',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('rows','pairs','limitations')}))
    print(json.dumps([dict(market=x['market'],arm=x['arm'],direction=x['direction'],up=x['terminal']['up'],down=x['terminal']['down'],cost=x['terminal']['cost'],cycles=x['activity']['repair_then_add_transitions']) for x in rows]))


if __name__=='__main__':main()
