"""V63 saved-path audit: confirmation gate only before the first FLIP and only on the chosen side."""
from analyze import read


def audit(arm, job):
    r = read(arm / 'result.json'); c = r['v63_confirm']; k = job['env_v12'].get('V12G_CONFIRM_K')
    lt = job['env_v12'].get('V12G_LOWCONV_TH')
    if lt:
        assert c['lowconv_th'] == float(lt) and c['k'] is None
        ev = r['v12g']['events']; fl = [e['t'] for e in ev if e['kind'] == 'FLIP']; dec = next((e for e in ev if e['kind'] == 'DECIDE'), None)
        low = dec is not None and c['m0'] is not None and c['m0'] < float(lt)
        if c['first_veto_t'] is not None:
            assert low and c['first_veto_t'] >= dec['t'] and (not fl or c['first_veto_t'] <= fl[0]), 'LOWCONV_VETO_OUTSIDE_SCOPE'
        fz = {k: c.get(k, 0) for k in ('freeze_vetoes', 'freeze_padd_vetoes', 'freeze_repair_vetoes')}
        if not low: assert c['vetoes'] == 0 and c['padd_vetoes'] == 0 and not any(fz.values())
        if fz['freeze_vetoes'] or fz['freeze_padd_vetoes'] or fz['freeze_repair_vetoes']: assert c.get('lowmode_freeze')
        return dict(lowconv=True, lowconv_low=low, lowconv_m0=c['m0'], lowconv_vetoes=c['vetoes'], lowconv_padd_vetoes=c['padd_vetoes'], **fz)
    if not k:
        assert c['k'] is None and c['vetoes'] == 0 and c['padd_vetoes'] == 0
        return dict(confirm_k=None)
    assert c['k'] == float(k)
    ev = r['v12g']['events']; fl = [e['t'] for e in ev if e['kind'] == 'FLIP']; dec = next((e for e in ev if e['kind'] == 'DECIDE'), None)
    if c['first_veto_t'] is not None:
        assert dec and c['first_veto_t'] >= dec['t'] and (not fl or c['first_veto_t'] <= fl[0])
    tr = read(arm / 'clock_trace.json.gz'); start = tr['tail_new_rows'][0]['start']
    if dec is not None:
        from pathlib import Path
        m = int(read(arm / 'EXECUTION.json')['argv'][read(arm / 'EXECUTION.json')['argv'].index('--market-id') + 1])
        src = read(Path(__file__).resolve().parent.parent / 'v12g_fresh40_flip200_20260928_v61/base/inputs' / f'public_{m}.json.gz')
        px = lambda xs: [float(x[0]) if isinstance(x, (list, tuple)) else float(x) for x in xs]
        end = fl[0] if fl else 10**20; best = None
        for b in src['books']:
            if not (dec['t'] <= b['received_ms'] < end) or not b.get('bids') or not b.get('asks'): continue
            um = (max(px(b['bids'])) + min(px(b['asks']))) / 2; cm = um if dec['side'] == 'UP' else 1 - um
            best = cm if best is None else max(best, cm)
        # the actor samples frames (a subset of books), so its hwm can only be <= the book max, and must track it closely
        assert best is None or (c['hwm'] <= best + 1e-9 and c['hwm'] >= best - 0.05), ('HWM_MISMATCH', c['hwm'], best, dec['side'])
        indep = best
    return dict(confirm_k=c['k'], confirm_vetoes=c['vetoes'], confirm_padd_vetoes=c['padd_vetoes'],
                confirm_first_veto_s=(c['first_veto_t'] - start) / 1000 if c['first_veto_t'] else None,
                confirm_m0=c['m0'], confirm_hwm=c['hwm'], confirm_hwm_books=indep if dec is not None else None, confirm_cap_at_end=(c['k'] * max(0., c['hwm'] - c['m0'])) if c['m0'] is not None else None)
