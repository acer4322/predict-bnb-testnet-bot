"""Independent saved-path audit: immutable physical targets and continuation supply."""
from analyze import read,differences

def audit(arm,mode):
    ticket=float(read(arm/'EXECUTION.json')['env_v12'].get('V12G_PASSIVE_TICKET','15'))
    trace=read(arm/'clock_trace.json.gz');policy=read(arm/'work_continuation_trace.json.gz')
    result=read(arm/'result.json');execution=read(arm/'execution_clock.json')
    assert policy['mode']==mode
    works=trace['demand_final']['works']
    if mode=='OFF':
        assert not policy['rows'] and not policy['orders']
        return dict(work_mode=mode,work_count=len(works),continuation_new=0)
    for work in works:
        side=work['physical_side'];initial=work['initial_state']
        expected=initial['inv'][side]+initial['pending_qty'][side]+work['activation']['requested']
        assert abs(work['target']-expected)<1e-7
        if work['status']=='CONFIRMED_TARGET_REACHED':
            assert work['end_state']['inv'][side]>=work['target']-1e-7,work
    for row in trace['demand_rows']:
        progress=row.get('progress')
        if not progress:continue
        work=next(w for w in works if w['id']==row['work_id'])
        side=work['physical_side']
        assert side==progress['physical_side']
        assert abs(progress['remaining_confirmed']-max(0.,work['target']-row['state']['inv'][side]))<1e-7
    accepted=[r for r in policy['rows'] if r['eligible']]
    assert len(accepted)==len(policy['orders'])
    if mode=='BIND':assert not accepted
    plans={}
    assert len(trace['plans'])==len(trace['direction_rows'])
    for p,frame in zip(trace['plans'],trace['direction_rows']):
        assert p['t']==frame['t']
        for operation in p['operations']:
            if operation['kind']=='NEW':
                assert operation['key'] not in plans,'DUPLICATE_NEW_OWNER_KEY'
                plans[operation['key']] = (p,frame['index'],operation)
    filled=0.;allocated_old=0.;allocated_new=0.;cash=0.
    for row,record in zip(accepted,policy['orders']):
        state=row['state'];side=row['side'];work=next(w for w in works if w['id']==row['work_id'])
        assert not any(o['side']==side for o in state['owners'])
        assert state['pending_qty'][side]<=1e-8 and state['pending_cash'][side]<=1e-8
        assert not any(o['kind']=='NEW' and o['side']==side for o in row['original_operations'])
        assert row['capacity']['economically_eligible']
        assert abs(row['old_target']-work['target'])<1e-7
        assert 0<row['old_remaining']<ticket
        assert abs(row['inherited_quantity']+row['independent_quantity']-ticket)<1e-7
        plan,index,operation=plans[record['key']]
        assert plan['t']==row['t'] and index==row['index']
        assert operation['qty']==ticket and operation['route']=='PASSIVE'
        # Last in passive commitment phase, before qualified active service.
        same=[o for o in plan['operations'] if o['kind']=='NEW' and o['side']==side and o.get('role') not in ('ACTIVE_BOUNDED_PARTIAL_REPAIR','V65_ZONE_MIRROR')]  # V59 BPR / V65 mirror audited separately
        assert operation in same
        extras=[o for o in same if o['key']!=record['key']]
        if extras:
            from qualified_audit import audit as qualified_audit
            q=read(arm/'qualified_work_trace.json.gz')
            certified={o['key']:o for o in q['orders']}
            assert len(extras)==1
            for extra in extras:
                if extra['role']=='ACTIVE_QUALIFIED_RATIO_RESTORATION':
                    # V68r1: the original qualified first repair (birth of the finite qualified work) in the same plan
                    assert q['work'] and extra['key']==q['work']['original_key']
                    assert plan['operations'].index(operation)<plan['operations'].index(extra)
                    continue
                assert extra['role']=='ACTIVE_QUALIFIED_FINITE_CONTINUATION'
                assert extra['key'] in certified
                assert plan['operations'].index(operation)<plan['operations'].index(extra)
                qr=next(r for r in q['rows'] if r.get('new_key')==extra['key'])
                assert qr['index']==index and operation in qr['original_operations']
                assert qr['planned']['pending_qty'][side]>=operation['qty']-1e-8
                assert qr['planned']['pending_cash'][side]>=operation['qty']*operation['price']-1e-8
                owner=next(o for o in qr['planned']['owners'] if o['key']==operation['key'])
                assert owner['state']=='SAME_PLAN_UNCONFIRMED' and owner['qty']==operation['qty']
                assert qr['state']['inv'][side]+qr['planned']['pending_qty'][side]+extra['qty']<=q['work']['target']+1e-7
                assert qr['final_eligible'] and qr['eligible']
        assert len(same)==1+len(extras)
        carrier=execution['carriers'][record['key']]
        assert carrier['state']=='TERMINAL'
        qty=carrier['filled'];assert qty<=ticket+1e-7
        old=min(qty,record['inherited_quantity']);fresh=max(0.,qty-old)
        assert abs(old+fresh-qty)<1e-7
        filled+=qty;allocated_old+=old;allocated_new+=fresh
        cash+=carrier['payment']+carrier['fees']
    return dict(work_mode=mode,work_count=len(works),physical_false_completion=0,
                direction_withdrawals=sum(w['status']=='WITHDRAWN_DIRECTION_CHANGED_WITH_RESIDUAL' for w in works),
                continuation_new=len(accepted),continuation_filled_qty=filled,continuation_cash=cash,
                old_residual_service=allocated_old,independent_service=allocated_new,
                uncompleted_works=sum(w['status']!='CONFIRMED_TARGET_REACHED' for w in works))
