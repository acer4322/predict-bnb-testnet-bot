"""CG4 saved-path audit: chop freeze armed only at/after start + 60 s from its own count; vetoes only while armed, after arming and
before the release FLIP; no NEW order between the first veto and the release (or the cutoff)."""
from analyze import read


def audit(arm, job):
    r = read(arm / 'result.json'); tr = read(arm / 'clock_trace.json.gz'); c = r['cg4_chop']; start = tr['tail_new_rows'][0]['start']
    k = job['env_v12'].get('V12G_CHOP_K')
    if not k:
        assert c['k'] is None and c['vetoes'] == 0; return dict(cg4_k=None)
    assert c['k'] == int(k) and c['armed'] is not None and c['arm_t'] >= start + 60000
    assert c['armed'] == (c['cross'] >= c['k'])
    if not c['armed']: assert c['vetoes'] == 0
    if c['vetoes']:
        assert c['first_veto_t'] >= c['arm_t'] and (c['released_t'] is None or c['first_veto_t'] < c['released_t'])
        end = c['released_t'] if c['released_t'] is not None else start + 290000
        news = [o.get('role') or o['route'] for p in tr['plans'] for o in p['operations'] if o['kind'] == 'NEW' and c['arm_t'] <= p['t'] < end]
    else:
        news = []
    return dict(cg4_new_while_frozen=len(news), cg4_new_while_frozen_channels=sorted(set(news)), cg4_k=c['k'], cg4_cross=c['cross'], cg4_frames60=c['frames60'], cg4_armed=c['armed'], cg4_arm_s=(c['arm_t'] - start) / 1000,
                cg4_released_s=None if c['released_t'] is None else (c['released_t'] - start) / 1000, cg4_vetoes=c['vetoes'])
