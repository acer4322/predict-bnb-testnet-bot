"""Offline same-market account and 290-second policy verification."""
import hashlib,importlib.util,json,statistics
from pathlib import Path
from analyze import read,audit_path
from governor_audit import audit as governor_audit
from stop_audit import audit as stop_audit
P=Path(__file__).resolve().parent;R=P.parent;sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
helper=R/'v12g_passive10_uncapped_20260928_v55/summarize.py'
spec=importlib.util.spec_from_file_location('receipt_summary',helper);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

def main():
    plan=read(P/'PROTOCOL.json');out=R/'lan_worker_returns'/plan['job_id']
    assert read(P/'CROSS_MACHINE_VERIFIED.json')['status']=='PASS'
    for n,v in read(P/'MANIFEST.json')['files'].items():assert sha(P/n)==v,n
    for n,v in read(P/'REMOTE_HASHES.json').items():assert sha(out/n)==v,n
    result=read(out/'RESULT.json');assert result['status']=='COMPLETE_V58_30' and len(result['paths'])==30
    parent=R/'v12g_qualified_work_continuation_20260927_v50'
    assert sha(parent/'COMPARISON.json')==plan['parent_comparison_sha256']
    oldrows={x['market']:x for x in read(parent/'COMPARISON.json')['rows'] if x['arm']=='V50'}
    meta={x['market_id']:x for x in read(R/'v12g_fresh30_generalization_20260927_v45/base/input_summary.json')['records']}
    assert audit_path(out/'arms/v12g58_INERT_2629327',R/plan['baseline']['2629327']['local_source'])['status']=='PASS'
    rows=[];checks=[];pins={helper.relative_to(R).as_posix():sha(helper)}
    for m in plan['markets']:
        base=R/plan['baseline'][str(m)]['local_source']
        for n,v in plan['baseline'][str(m)]['files'].items():assert sha(base/n)==v,n
        for label,path in [('V50_BASE',base),('STOP290',out/'arms'/f'v12g58_STOP290_{m}')]:
            z=h.account(path,meta[m]);z.update(arm=label,market=m,source=path.relative_to(R).as_posix(),winner=oldrows[m]['winner'])
            z['winner_payoff']=z[z['winner']];gain=max(z['UP'],0)+max(z['DOWN'],0);loss=max(-z['UP'],0)+max(-z['DOWN'],0)
            z.update(P=gain,L=loss,positive_exceeds_loss=gain>loss and gain>1e-8)
            tr=read(path/'clock_trace.json.gz');clock=read(path/'execution_clock.json');start,end=meta[m]['window'];cut=start+290000
            z['new_at_or_after290']=sum(o['kind']=='NEW' for p in tr['plans'] if p['t']>=cut for o in p['operations'])
            z['exchange_fill_receipts_after_expiry']=sum(x['qty']>0 and x['exchange_ts']>=end*1000000 for x in clock['receipts'])
            z['last_new_seconds']=max((p['t']-start)/1000 for p in tr['plans'] if any(o['kind']=='NEW' for o in p['operations']))
            if label=='STOP290':
                job=next(x for x in plan['jobs'] if x['market']==m and x['arm']==label)
                a=audit_path(path);assert a['status']=='PASS';a.update(governor_audit(path,base,'BASE',None,compare_preflip=False));a.update(stop_audit(path,base,job))
                checks.append(dict(market=m,**a));z.update(a)
                assert not z['new_at_or_after290'] and not z['exchange_fill_receipts_after_expiry']
            rows.append(z)
            for n in ('result.json','clock_trace.json.gz','execution_clock.json','stop290_trace.json.gz'):
                if (path/n).exists():pins[(path/n).relative_to(R).as_posix()]=sha(path/n)
    aggregates={a:{g:h.aggregate([x for x in rows if x['arm']==a and x['market'] in mids]) for g,mids in plan['groups'].items()} for a in ('V50_BASE','STOP290')}
    pairs=[]
    for m in plan['markets']:
        b=next(x for x in rows if x['market']==m and x['arm']=='V50_BASE');n=next(x for x in rows if x['market']==m and x['arm']=='STOP290')
        pairs.append(dict(market=m,base=b['winner_payoff'],stop290=n['winner_payoff'],delta=n['winner_payoff']-b['winner_payoff'],cost_delta=n['cost']-b['cost'],
            cancels=n['stop290_cancel_requests'],reaction_lag_ms=n['cutoff_reaction_lag_ms'],pre_cut_orders_fill_receipts_after290=n['precut_orders_filled_after_cutoff_receipts']))
    for a,groups in aggregates.items():
        for g,v in groups.items():
            group=[x for x in rows if x['arm']==a and x['market'] in plan['groups'][g]]
            v.update(worst2_mean=statistics.mean(sorted(x['winner_payoff'] for x in group)[:2]),worst5_mean=statistics.mean(sorted(x['winner_payoff'] for x in group)[:5]),payoff_per100_cost=100*sum(x['winner_payoff'] for x in group)/sum(x['cost'] for x in group))
    summary=dict(status='COMPLETE_ORIGINAL_SCALE_STOP290_PAIRED29',rows=rows,checks=checks,aggregates=aggregates,paired=pairs,
        improved=sum(x['delta']>1e-7 for x in pairs),worse=sum(x['delta']<-1e-7 for x in pairs),same=sum(abs(x['delta'])<=1e-7 for x in pairs),
        native_paths=30,native_reruns=0,model_fits=0,live_changes=0,inert_full_parity=True,all_pre290_prefix_equal=True,
        new_after290=0,postexpiry_fills=0,owners=0,native_cancel_requests=sum(x['cancels'] for x in pairs),native_nonempty_cutoff_markets=sum(x['stop290_first_pending_owners']>0 for x in checks),normal_profit_retention_pct=100*aggregates['STOP290']['BASE_NO_FLIP']['winner_mean']/aggregates['V50_BASE']['BASE_NO_FLIP']['winner_mean'],source_hashes=pins,legacy_failed_paths=sum(bool(x['failed_checks']) for x in result['paths']),
        elapsed_seconds=result['elapsed_seconds'],resource_samples=result['resource_samples'])
    (P/'COMPARISON.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(aggregates={a:aggregates[a]['VALID_PAIRED'] for a in aggregates},paired=pairs,legacy=summary['legacy_failed_paths'],elapsed_seconds=result['elapsed_seconds'])))
if __name__=='__main__':main()
