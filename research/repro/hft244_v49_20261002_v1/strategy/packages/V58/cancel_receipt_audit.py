"""Postrun independent owner/reservation reconciliation; read-only native evidence."""
import hashlib,json
from pathlib import Path
from analyze import read
P=Path(__file__).resolve().parent;R=P.parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    plan=read(P/'PROTOCOL.json');out=R/'lan_worker_returns'/plan['job_id'];rows=[];sources={}
    assert read(out/'RESULT.json')['status']=='COMPLETE_V58_30'
    for m in plan['markets']:
        arm=out/'arms'/f'v12g58_STOP290_{m}'
        pol=read(arm/'stop290_trace.json.gz');clock=read(arm/'execution_clock.json')
        tr=read(arm/'clock_trace.json.gz');pending=0;nonempty=0;requested={};last={}
        assert pol['mode']=='ON' and pol['rows']
        for g in pol['rows']:
            owners=g['owners'];before=g['reservations_before'];after=g['reservations_after']
            assert before==after
            qty=sum(x['remaining'] for x in owners)
            cash=sum(x['remaining']*x['limit'] for x in owners)
            assert abs(qty-sum(a['reserved_qty'] for a in before.values()))<1e-7,(m,g['t'],'qty')
            assert abs(cash-sum(a['reserved_cash'] for a in before.values()))<1e-7,(m,g['t'],'cash')
            nonempty+=bool(owners)
            pending+=sum(o['state']=='CANCEL_PENDING' for o in owners)
            for o in owners:last[o['key']]=o
            for op in g['operations']:
                assert op['kind']=='CANCEL'
                requested.setdefault(op['key'],[]).append(g['t'])
        for key in requested:
            assert clock['carriers'][key]['state']=='TERMINAL',key
        native_cancel=[x for x in tr['native_actions'] if x['t']>=pol['rows'][0]['start']+290000]
        for n in ('stop290_trace.json.gz','execution_clock.json','clock_trace.json.gz'):
            sources[(arm/n).relative_to(R).as_posix()]=sha(arm/n)
        rows.append(dict(market=m,frames=len(pol['rows']),nonempty_frames=nonempty,
            first_pending_owners=len(pol['rows'][0]['owners']),cancel_pending_owner_observations=pending,
            cancel_requests=sum(map(len,requested.values())),cancelled_owner_keys=sorted(requested),
            all_requested_terminal=True,final_live_owners=len(pol['rows'][-1]['owners']),
            requested_final_filled={k:clock['carriers'][k]['filled'] for k in requested},
            native_after_cut_actions=native_cancel))
    summary=dict(status='PASS',rows=rows,source_hashes=sources,
        markets_with_nonempty_frames=sum(x['nonempty_frames']>0 for x in rows),
        cancel_requests=sum(x['cancel_requests'] for x in rows),
        cancel_pending_owner_observations=sum(x['cancel_pending_owner_observations'] for x in rows),
        all_rows_reserved_quantity_cash_rebuilt=True,
        limitation='Observed zero-fee replay only; fills before cancellation confirmation remain valid responsibility. Terminal carrier evidence is not a live venue connectivity test.')
    (P/'CANCEL_RECEIPT_AUDIT.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('rows','source_hashes')}))
if __name__=='__main__':main()
