"""Independent action/receipt cutoff audit; unchanged prefix until the first V59 BPR NEW or the cutoff."""
from analyze import read,differences

def audit(arm,baseline,job):
    pol=read(arm/'stop290_trace.json.gz');tr=read(arm/'clock_trace.json.gz');clock=read(arm/'execution_clock.json');r=read(arm/'result.json')
    assert pol['mode']==job['stop_mode'] and pol['cutoff_ms']==290000
    if job['stop_mode']=='OFF':
        assert not pol['rows'];return dict(stop290_mode='OFF',stop290_control=True)
    assert pol['rows'] and r['v57_stop290']['frames']==len(pol['rows'])
    start=pol['rows'][0]['start'];end=pol['rows'][0]['end'];cut=start+290000
    lateplans=[(i,p) for i,p in enumerate(tr['plans']) if p['t']>=cut]
    assert len(lateplans)==len(pol['rows'])
    cancellations=[];never_cancellable=[]
    for (i,p),g in zip(lateplans,pol['rows']):
        assert p['t']==g['t'] and i==g['index']
        assert not differences(p['operations'],g['operations'])
        assert all(o['kind']=='CANCEL' for o in p['operations'])
        expected={o['key'] for o in g['owners'] if o['state']!='CANCEL_PENDING' and o['cancellable']}
        actual=[o['key'] for o in p['operations']]
        assert len(actual)==len(set(actual)) and set(actual)==expected
        assert g['reservations_before']==g['reservations_after']
        cancellations.extend((o['key'],p['t']) for o in p['operations'])
        never_cancellable.extend(o['key'] for o in g['owners'] if not o['cancellable'] and o['state']!='CANCEL_PENDING')
    # Exact physical decisions and observations before the user cutoff.
    old=read(baseline/'clock_trace.json.gz')
    # V59: the unchanged prefix ends at the first bounded-partial-repair NEW (or the cutoff, whichever is earlier).
    bt=[p['t'] for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW' and o.get('role')=='ACTIVE_BOUNDED_PARTIAL_REPAIR']
    fx=read(arm/'flip_exit_trace.json.gz');bt+=[fx['rows'][0]['t']] if fx['rows'] else []
    fv=r.get('v63_confirm',{}).get('first_veto_t');bt+=[fv] if fv is not None else []
    zm=read(arm/'zone_mirror_trace.json.gz');bt+=[zm['orders'][0]['t']] if zm['orders'] else []
    pre=min([cut]+bt)
    if job['env_v12'].get('V12G_DEMAND_SCALE_MODE','OFF')!='OFF':pre=start  # V67 size arm differs from the first frame: no prefix identity claimed
    for key in ('plans','states','native_actions','direction_rows','demand_rows','intent'):
        a=[x for x in old[key] if x['t']<pre];b=[x for x in tr[key] if x['t']<pre]
        assert not differences(a,b,key),('PRE290_PREFIX',key,differences(a,b,key))
    births={int(o['key'].rsplit('_',1)[1]):(p,o) for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW'}
    assert all(p['t']<cut for p,o in births.values())
    late=[x for x in clock['receipts'] if x['qty']>0 and x['exchange_ts']>=end*1000000]
    assert not late,('POSTEXPIRY_EXCHANGE_FILL',late[:3])
    aftercut=[x for x in clock['receipts'] if x['qty']>0 and x['exchange_ts']>=cut*1000000]
    assert all(births[x['order_id']][0]['t']<cut for x in aftercut)
    assert all(c['state']=='TERMINAL' for c in clock['carriers'].values()) and r['unresolved_owners']==0
    return dict(stop290_mode='ON',stop290_new=0,stop290_cancel_requests=len(cancellations),stop290_frames=len(lateplans),
        cutoff_reaction_lag_ms=lateplans[0][1]['t']-cut,pre290_full_prefix=not bt,unchanged_prefix_until_ms=pre-start,postexpiry_exchange_fills=0,
        precut_orders_filled_after_cutoff_receipts=len(aftercut),deferred_noncancellable_keys=sorted(set(never_cancellable)),
        stop290_first_pending_owners=len(pol['rows'][0]['owners']),stop290_final_pending_owners=len(pol['rows'][-1]['owners']))
