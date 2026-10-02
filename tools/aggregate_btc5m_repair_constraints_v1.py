"""Collect the frozen 2x2 comparison; three new cells plus reused V24."""
import json
from prepare_btc5m_repair_constraints_v1 import ROOT,R,BASE,START,ARMS,config,read,sha,get,RET,dump_for
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary


def summarize(result,trace):
    terminal=geometry(result['final_inventory'],result['final_cost'])
    trajectory=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),trace['states'],START,START+300000)
    return dict(terminal=terminal,trajectory={k:trajectory[k] for k in ('both_positive_seconds','negative_floor_area_currency_seconds',
        'minimum_floor','reversed_net_seconds')},core_similarity=result['core_similarity'],
        passive_submits=result['passive_native_submits'],active_submits=result['active_native_submits'])


def main():
    cells={};b,bt=get(BASE);cells['baseline']=summarize(b,bt)
    base_audit=read(R/'BTC5M_COMMITMENT_REPAIR_V1_20260913_RESULT.json')
    assert base_audit['verification']=='PASS'
    cells['baseline'].update(extra_totals=base_audit['extra_totals'],peak_extra_nonterminal_owners=1,native_seconds=54.425,
                             reused_existing_result=True)
    receipts={};scenarios={};elapsed=0.
    for arm in ARMS:
        c=config(arm);audit=read(R/(c['STEM']+'_RESULT.json'));n,nt=get(RET/c['JOB'])
        manifest=read(c['PACKAGE']/'manifest.json')
        assert audit['verification']=='PASS' and audit['manifest_sha256']==sha(c['PACKAGE']/'manifest.json')
        assert all(sha(c['PACKAGE']/name)==h for name,h in manifest['files'].items())
        status=read(R/(c['STEM']+'_COLLECT.json'))['status'];assert status['state']=='succeeded'
        assert read(R/(c['STEM']+'_SUBMIT.json'))['accepted']
        assert read(R/(c['STEM']+'_POSTCHECK.json'))['status']=='PASS'
        elapsed+=status['elapsed_seconds'];cells[arm]=summarize(n,nt)
        cells[arm].update(extra_totals=audit['extra_totals'],peak_extra_nonterminal_owners=audit['peak_extra_nonterminal_owners'],
            native_seconds=status['elapsed_seconds'],concurrent_extra_births=audit['concurrent_extra_births'],
            legal_quote_extra_births=audit['legal_quote_extra_births'],prevented_stale_cancel_frames=len(audit['prevented_stale_cancels']),
            decision_rows_checked=audit['decision_rows_checked'],maintenance_rows_checked=audit['maintenance_rows_checked'],
            result_sha256=sha(RET/c['JOB']/'result.json'),trace_sha256=sha(RET/c['JOB']/'clock_trace.json.gz'),
            manifest_sha256=sha(c['PACKAGE']/'manifest.json'))
        receipts[arm]={k:audit['candidate']['receipts'][k] for k in ('count','complete','owner_accounting_pass','positive_rows','sub_one_dollar_positive_receipts')}
        # This conservative stress scenario has no pending UP fills and all current DOWN fills.
        stress=[]
        for row in nt['commitment_repair_rows']:
            s=row['state'];cash=row['pending_cash']['DOWN'];qty=row['pending_qty']['DOWN']
            floor=min(s['payoff']['UP']-cash,s['payoff']['DOWN']+qty-cash)
            stress.append(dict(t=row['t'],seconds=(row['t']-START)/1000,pending_down_qty=qty,pending_down_cash=cash,
                confirmed_floor=min(s['payoff'].values()),floor_if_only_pending_down_fill=floor,
                change=floor-min(s['payoff'].values()),extra_owner_count=len(row['outstanding'])))
        scenarios[arm]=dict(most_adverse_delta=min(stress,key=lambda x:x['change']),
            interpretation='Conditional risk scenario only, not observed loss or an admission cash budget. Existing per-ticket floor gate does not guarantee a floor bound for every combination of multiple pending fills.')
    combined=cells['both']
    solved=dict(concurrent_repair=combined['concurrent_extra_births']>0 and combined['peak_extra_nonterminal_owners']>1,
                legal_repair_quote=combined['legal_quote_extra_births']>0,
                maintenance_uses_legal_quote=combined['maintenance_rows_checked']>0 and combined['prevented_stale_cancel_frames']>0)
    assert all(solved.values()),'Do not claim an unexercised correction complete'
    deltas={}
    for arm in ARMS:
        deltas[arm]={k:cells[arm]['terminal'][k]-cells['baseline']['terminal'][k] for k in ('up','down','cost','up_net','floor')}
    interaction={k:deltas['both'][k]-deltas['concurrent'][k]-deltas['quote'][k] for k in deltas['both']}
    out=dict(status='COMPLETE',verification='PASS',engineering_constraints_solved=solved,new_native_jobs=3,reused_native_controls=1,
        total_new_native_seconds=elapsed,local_native_jobs=0,model_fits=0,parameter_search=0,runtime_eligible=False,
        cells=cells,terminal_deltas=deltas,interaction=interaction,receipt_checks=receipts,pending_risk_scenarios=scenarios,
        research_candidate=dict(arm='both',package=str(config('both')['PACKAGE'].relative_to(ROOT)),
            manifest_sha256=sha(config('both')['PACKAGE']/'manifest.json'),scope='Both requested engineering corrections exercised and verified; not a policy-performance selection or live deployment'),
        limitations='One consumed direction-oracle market; no held-out evidence. More repair can consume intended-direction upside and reverse net inventory. Absolute payoff target and repeated Active intervention remain unresolved. Pending UP full fill is still a conditional scenario.')
    dump_for('BTC5M_REPAIR_CONSTRAINTS_PANEL_V1_20260913','RESULT',out)
    progress=dict(status='COMPLETE',new_native_jobs=3,submit_count=3,host_native_jobs=0,model_fits=0,parameter_search=0,
        elapsed_native_seconds=elapsed,engineering_constraints_solved=solved,next_native_dispatched=False,
        current_research_package=out['research_candidate'],jobs={a:config(a)['JOB'] for a in ARMS})
    (R/'REPAIR_CONSTRAINTS_PROGRESS_20260913.json').write_text(json.dumps(progress,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',engineering_constraints_solved=solved,native_seconds=elapsed,
        table={a:dict(up=c['terminal']['up'],down=c['terminal']['down'],up_net=c['terminal']['up_net'],
                      both_positive_seconds=c['trajectory']['both_positive_seconds'],extra=c['extra_totals']) for a,c in cells.items()})))


if __name__=='__main__':main()
