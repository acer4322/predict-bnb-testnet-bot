"""Readback of phase scaling and retained finite repair debt on each path."""
from analyze import read
def audit(arm,job):
    pol=read(arm/'demand_scale_trace.json.gz');tr=read(arm/'clock_trace.json.gz');r=read(arm/'result.json')
    return validate(pol,tr,r,job)

def validate(pol,tr,r,job):
    assert pol['mode']==job['scale_mode'] and pol['factor']==.2
    assert r['v56_demand_scale']['rows']==len(pol['rows'])>0
    demand=tr['demand_rows'];assert len(demand)==len(pol['rows'])
    directions={(x['t'],x['index']):x['side'] for x in tr['direction_rows']}
    preserved=0;changed=0;goals={}
    growth=tr['addition_growth_rows'];hold=tr['growth_hold_rows']
    assert len(growth)==len(hold)==len(demand)
    for x,g,h,d in zip(pol['rows'],growth,hold,demand):
        assert x['t']==d['t'] and x['side']==directions[(x['t'],x['index'])]
        scaled=pol['mode']=='BOTH' or(pol['mode']=='OPEN' and x['side'] is None)or(pol['mode']=='POST' and x['side'] is not None)
        for s in ('UP','DOWN'):
            assert abs(x['scaled'][s]-x['raw'][s]*(.2 if scaled else 1.))<1e-8
        changed+=int(x['raw']!=x['scaled'])
        strong=x['side'] or 'UP';weak='DOWN' if strong=='UP' else 'UP'
        # Growth keeps its physical anchor across flips; follow each actual seam.
        assert x['t']==g['t']==h['t']==d['t'] and x['index']==h['index']
        assert x['scaled']==g['original_desired']
        assert g['effective_desired']==h['input_desired']
        assert h['effective_desired']==d['original_desired']
        fixed=g['strong'];other='DOWN' if fixed=='UP' else 'UP'
        assert g['effective_desired'][other]==g['original_desired'][other]
        for s in ('UP','DOWN'):
            assert 0<=h['effective_desired'][s]<=g['effective_desired'][s]+1e-8<=g['original_desired'][s]+2e-8
        for s in ('UP','DOWN'):
            assert d['effective_desired'][s]>=d['original_desired'][s]-1e-8
        prog=d.get('progress')
        if prog:
            wid=d['work_id'];target=prog['target']
            if wid in goals:assert goals[wid]==target
            else:goals[wid]=target
            assert abs(target-prog['initial_down']-prog['initial_committed_down']-prog['new_request'])<1e-7
            if prog['status']=='ACTIVE':
                assert d['effective_desired'][weak]>=target-1e-7
                preserved+=int(target>x['scaled'][weak]+1e-8)
    assert changed==r['v56_demand_scale']['changed']
    return dict(demand_scale_mode=pol['mode'],demand_scale_frames=len(pol['rows']),scaled_frames=changed,
        finite_goal_above_scaled_raw_frames=preserved,finite_goal_targets_verified=len(goals),raw_scale_runtime_verified=True)
