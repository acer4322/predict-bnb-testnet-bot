"""Artifact validation independent of the runtime guard's saved decisions."""
from analyze import read,audit_path,differences

def bound(state):
    return {s:float(state['payoff'][s])-float(state['pending_cash']['DOWN' if s=='UP' else 'UP']) for s in ('UP','DOWN')}

def audit(arm,baseline):
    r=read(arm/'result.json');trace=read(arm/'clock_trace.json.gz');pol=read(arm/'risk_floor_trace.json.gz')
    assert pol['mode']=='ON';assert not read(arm/'postflip_repair_trace.json.gz')['enabled']
    assert len(pol['plans'])==len(trace['plans'])==len(trace['direction_rows'])
    flips=[e for e in r['v12g']['events'] if e['kind']=='FLIP'];first=flips[0]['t'] if flips else None
    floor=None;frames={(f['t'],f['index']):f for f in pol['frames']};new_count=0;enabled_count=0
    for p,plan,role in zip(pol['plans'],trace['plans'],trace['direction_rows']):
        assert p['t']==plan['t']==role['t'] and p['index']==role['index']
        assert not differences(p['operations'],plan['operations'])
        assert p['enabled']==(first is not None and p['t']>=first)
        state=p['state'];initial=bound(state);cash=dict(state['pending_cash'])
        if p['enabled']:
            enabled_count+=1
            assert floor is None or min(initial.values())>=floor-1e-7
            floor=min(initial.values()) if floor is None else max(floor,min(initial.values()))
            assert abs(p['floor']-floor)<1e-7
            f=frames[(p['t'],p['index'])];assert abs(f['floor']-floor)<1e-7
            assert not differences(f['state'],state),'FRAME_SNAPSHOT_CHANGED'
            assert all(e['t']<=p['t'] for e in f['observed_flips'])
        else:assert p['floor'] is None
        n=0
        for op in plan['operations']:
            if op['kind']!='NEW':continue
            cash[op['side']]+=op['qty']*op['price'];n+=1
            if p['enabled']:
                reserved={s:state['payoff'][s]-cash['DOWN' if s=='UP' else 'UP'] for s in ('UP','DOWN')}
                assert min(reserved.values())>=floor-1e-7,('NEW_EXCEEDS_FLOOR',p['index'],op,reserved,floor)
        assert n==p['checked_new'];new_count+=n
        assert not differences(p['reserved_after'],{s:state['payoff'][s]-cash['DOWN' if s=='UP' else 'UP'] for s in ('UP','DOWN')})
    assert len(frames)==enabled_count
    end={s:r['final_inventory'][s]-r['final_cost'] for s in ('UP','DOWN')}
    if flips:
        assert pol['initial']['t']==first and abs(pol['floor']-floor)<1e-7
        assert min(end.values())>=floor-1e-7
    else:
        assert pol['initial'] is None and not pol['frames'] and not pol['decisions']
    prefix='NEW_WORK_POLICY_FULL_PATH_NOT_ASSUMED_PARITY'
    return dict(prefix_audit=prefix,actual_flips=len(flips),checked_new=new_count,protected_frames=enabled_count,
        initial_floor=pol['initial']['floor'] if pol['initial'] else None,final_reserved_floor=floor,
        blocked=sum(not x['allowed'] for x in pol['decisions']),decisions=len(pol['decisions']))
