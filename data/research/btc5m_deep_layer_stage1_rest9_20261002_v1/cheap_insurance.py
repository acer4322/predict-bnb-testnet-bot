"""CG2-A cheap-underdog insurance (high-conviction markets only): after DECIDE and before 290 s, while the current weak side's
ask <= PX and weak inventory (+ own pending) < RATIO x strong inventory, buy the weak side ACTIVE (visible depth, one in flight).
Runtime inputs: own ledger/inventory, current book/quotes, the strategy's own DECIDE label. No clock rule beyond STOP290."""
import atexit, gzip, json, math, os
from pathlib import Path

PX = float(os.environ['V12G_INS_PX']) if os.environ.get('V12G_INS_PX') else None
RATIO = float(os.environ.get('V12G_INS_RATIO') or .8)
ENABLED = PX is not None
CUTOFF_MS = 290_000; LATENCY_MS = 250; EPS = 1e-8
ROLE = 'CG2_CHEAP_INSURANCE'
STATE = dict(keys=[], orders=[], rows=0, reasons={})


def floor_step(x, step):
    return max(0., round(math.floor((max(0., x) + 1e-9) / step) * step, 8))


def plan(inv_strong, inv_weak, pend_weak, ask, depth, step, px=None, ratio=None):
    px = PX if px is None else px; ratio = RATIO if ratio is None else ratio
    if ask is None or not 0 < ask <= px + 1e-9 or depth <= 0:
        return None
    need = ratio * inv_strong - inv_weak - pend_weak
    q = floor_step(min(need, depth), step)
    return q if q > EPS and q * ask >= 1. - EPS else None


def _why(r):
    STATE['reasons'][r] = STATE['reasons'].get(r, 0) + 1


def extend(frame, ops, roles, high_mode, snapshot, with_plan, governor_q, risk_q, validate, crossing):
    if not ENABLED or not high_mode():
        return ops
    t = int(frame['t']); start = int(frame['start']); end = int(frame['end']); STATE['rows'] += 1
    if roles.side is None or not start <= t < end: return ops
    if t - start >= CUTOFF_MS or t + LATENCY_MS >= end: _why('STOP290_OR_ARRIVAL'); return ops
    ledger = frame['ledger']
    if any(k in ledger.carriers and ledger.carriers[k].state != 'TERMINAL' for k in STATE['keys']): _why('IN_FLIGHT'); return ops
    if any(o['kind'] == 'NEW' and o.get('route') == 'ACTIVE' for o in ops): _why('EXISTING_ACTIVE'); return ops
    S = roles.strong; Wk = roles.weak
    ask = ((frame.get('quotes') or {}).get(Wk) or {}).get('ask')
    book = frame.get('book') or {}; bids = book.get('bids') or {}; asks = book.get('asks') or {}
    if not bids or not asks: return ops
    depth = float(asks[min(asks)]) if Wk == 'UP' else float(bids[max(bids)])
    planned = with_plan(snapshot(frame, ledger), ops)
    inv = {s: float(frame['own_view']['inv'][s]) for s in ('UP', 'DOWN')}
    wp = frame['world_profile']; step = float(wp['quantity_step'])
    q = plan(inv[S], inv[Wk], float(planned['pending_qty'][Wk]), None if ask is None else float(ask), depth, step)
    if q is None: _why('NOT_CHEAP_OR_COVERED'); return ops
    p = float(ask)
    q = floor_step(min(q, float(governor_q(planned, Wk, p, q)), float(risk_q(planned, Wk, p, q))), step)
    if q <= EPS or q * p < 1. - EPS: _why('GOVERNOR_OR_RISK'); return ops
    if crossing(Wk, p, [{'key': o['key'], 'side': o['side'], 'price': o['limit']} for o in planned['owners']]): _why('OWN_CROSS'); return ops
    if abs(p / float(wp['tick']) - round(p / float(wp['tick']))) > 1e-8: _why('OFF_TICK'); return ops
    try: validate(wp['asset'], 'ACTIVE', p, q, quantity_step=wp['quantity_step'])
    except Exception: _why('VALIDATE_REJECT'); return ops
    n = int(frame['own_view']['n']) + sum(o['kind'] == 'NEW' for o in ops)
    new = {'kind': 'NEW', 'key': f'{Wk}_{n}', 'parent_id': roles.pid(Wk), 'side': Wk, 'route': 'ACTIVE', 'price': p, 'qty': q, 'role': ROLE}
    STATE['keys'].append(new['key']); STATE['orders'].append(dict(t=t, inv_strong=inv[S], inv_weak=inv[Wk], **new)); _why('ORDER')
    return [*ops, new]


def finish(out):
    payload = dict(enabled=ENABLED, px=PX, ratio=RATIO, orders=STATE['orders'], reasons=STATE['reasons'], rows=STATE['rows'])
    with gzip.GzipFile(filename=str(Path(out) / 'cheap_insurance_trace.json.gz'), mode='wb', mtime=0) as fh:
        fh.write(json.dumps(payload, sort_keys=True, separators=(',', ':'), default=float).encode())
    return dict(enabled=ENABLED, px=PX, ratio=RATIO, orders=len(STATE['orders']), ordered_qty=sum(o['qty'] for o in STATE['orders']))


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir() and not (Path(out) / 'cheap_insurance_trace.json.gz').exists(): finish(out)


atexit.register(_save_exit)
