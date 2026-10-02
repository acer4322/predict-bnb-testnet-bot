"""Read-only saved-state checks independent of candidate implementation."""
from analyze import read

def audit(arm,mode):
    tr=read(arm/'clock_trace.json.gz');pol=read(arm/'work_continuation_trace.json.gz')
    records={o['key']:o for o in pol['orders']}; checked=0; eligible=0
    active={};statuses={};observations={}
    for dr in tr['demand_rows']:
        state=dr['state'];t=dr['t']
        if mode=='OFF':assert 'continuation_intents' not in dr
        for r in dr.get('continuation_intents',[]):
            observations.setdefault((t,r['key']),[]).append(r)
            o=records[r['key']];side=o['side'];opp='DOWN' if side=='UP' else 'UP'
            assert abs(r['immutable_target']-o['independent_inventory_interval'][1])<1e-7
            assert abs(r['remaining_confirmed']-max(0.,r['immutable_target']-state['inv'][side]))<1e-7
            own=[a for a in state['owners'] if a['key']==o['key']]
            qty=sum(a['qty'] for a in own);cash=sum(a['qty']*a['limit'] for a in own)
            assert abs(qty-r['own_pending_qty'])<1e-7 and abs(cash-r['own_pending_cash'])<1e-7
            if r['eligible']:
                assert r['status']=='ACTIVE' and dr['continuation_weak']==side
                assert r['owner_state']!='TERMINAL' and r['remaining_confirmed']>0
                assert state['payoff'][side]<0 and r['remaining_confirmed']<=min(r['quantity_capacity'],r['money_capacity'])+1e-7
                assert dr['effective_desired'][side]>=r['immutable_target']-1e-7
                active[t,o['key']]=r;eligible+=1
            previous=statuses.get(o['key'],'ACTIVE')
            if previous!='ACTIVE':assert r['status']==previous,'WITHDRAWN_INTENT_RESURRECTED'
            statuses[o['key']]=r['status'];checked+=1
    # Maintenance logs have t/key but no source index. Never assign an ambiguous
    # same-millisecond maintenance row to an arbitrarily last demand observation.
    maintained=[];ambiguous=[]
    for r in tr['demand_maintenance_rows']:
        candidates=observations.get((r['t'],r['key']),[])
        if candidates and all(c['eligible'] for c in candidates):maintained.append(r)
        elif any(c['eligible'] for c in candidates):ambiguous.append(r)
    # If other pending orders create genuine surplus it remains cancellable.
    return dict(intent_mode=mode,intent_rows=checked,active_intent_frames=eligible,
                maintained_active_intents=len(maintained),active_intent_surplus=sum(r['surplus'] for r in maintained),
                active_intent_stale=sum(r['stale'] for r in maintained),intent_final_status=statuses,
                ambiguous_millisecond_maintenance=len(ambiguous))
