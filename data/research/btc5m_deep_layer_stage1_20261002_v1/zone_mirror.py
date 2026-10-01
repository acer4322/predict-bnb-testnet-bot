"""V65 zone mirror: while the DECIDE-side mid is in the uncertain zone [0.48, 0.65] (after DECIDE, before the first FLIP,
before STOP290), confirmed fills of the chosen side in the zone are mirrored by ACTIVE buys of the other side so the zone
buy mix stays ~50/50. Chosen-side expansion itself is never blocked. Runtime inputs: own ledger/inventory, current book,
the strategy's own DECIDE/FLIP labels. No clock rule, no Target/winner/future data."""
import atexit, gzip, json, math, os
from pathlib import Path

ENABLED = os.environ.get('V12G_ZONE_MIRROR', 'OFF') == 'ON'
assert os.environ.get('V12G_ZONE_MIRROR', 'OFF') in ('OFF', 'ON')
LO, HI = 0.48, 0.65
ALPHA = float(os.environ.get('V12G_ZONE_ALPHA') or 1.0)   # V66: other-side zone fills target = ALPHA * chosen-side zone fills
assert 0. < ALPHA <= 1.
ROUTE = os.environ.get('V12G_ZONE_ROUTE', 'ACTIVE')   # V67: PASSIVE = one ticket resting at the other side's best bid, re-quoted when left behind
assert ROUTE in ('ACTIVE', 'PASSIVE')
TICKET = float(os.environ.get('V12G_PASSIVE_TICKET') or 15.)
CUTOFF_MS = 290_000; LATENCY_MS = 250; EPS = 1e-8
ROLE = 'V65_ZONE_MIRROR'
STATE = dict(last_inv=None, zoneF=0., zoneW=0., keys=[], rows=[], orders=[], ended=None)


def floor_step(x, step):
    return max(0., round(math.floor((max(0., x) + 1e-9) / step) * step, 8))


def plan(zoneF, zoneW, ask, depth, step, alpha=None):
    """Pure sizing: buy the other side up to ALPHA x chosen-side zone fills, limited by visible depth; None if below $1."""
    need = (ALPHA if alpha is None else alpha) * zoneF - zoneW
    if ask is None or not 0 < ask < 1 or depth <= 0 or need <= EPS:
        return None
    q = floor_step(min(need, depth), step)
    return q if q > EPS and q * ask >= 1. - EPS else None


def extend(frame, ops, roles, snapshot, with_plan, governor_q, risk_q, validate, crossing):
    if not ENABLED:
        return ops
    t = int(frame['t']); start = int(frame['start']); end = int(frame['end'])
    side = roles.side
    if side is None or not start <= t < end:
        return ops
    if any(e['kind'] == 'FLIP' for e in getattr(roles, 'v12g_events', [])):
        if STATE['ended'] is None: STATE['ended'] = dict(t=t, reason='FIRST_FLIP')
        return ops
    W = 'DOWN' if side == 'UP' else 'UP'
    inv = {s: float(frame['own_view']['inv'][s]) for s in ('UP', 'DOWN')}
    last = STATE['last_inv'] or dict(inv); STATE['last_inv'] = dict(inv)
    book = frame.get('book') or {}; bids = book.get('bids') or {}; asks = book.get('asks') or {}
    if not bids or not asks:
        return ops
    um = (max(bids) + min(asks)) / 2.; cm = um if side == 'UP' else 1. - um
    in_zone = LO - EPS <= cm <= HI + EPS
    if in_zone:
        STATE['zoneF'] += inv[side] - last[side]; STATE['zoneW'] += inv[W] - last[W]
    row = dict(t=t, cm=cm, in_zone=in_zone, zoneF=STATE['zoneF'], zoneW=STATE['zoneW'])
    STATE['rows'].append(row)
    if not in_zone:
        row['reason'] = 'OUT_OF_ZONE'; return ops
    if t - start >= CUTOFF_MS or t + LATENCY_MS >= end:
        row['reason'] = 'STOP290_OR_ARRIVAL'; return ops
    ledger = frame['ledger']
    live = [k for k in STATE['keys'] if k in ledger.carriers and ledger.carriers[k].state != 'TERMINAL']
    if live and ROUTE == 'PASSIVE':
        k = live[-1]; c = ledger.carriers[k]
        wbid = float(max(bids)) if W == 'UP' else round(1. - float(min(asks)), 10)
        if c.state != 'CANCEL_PENDING' and frame.get('cancellable', {}).get(k, False) and float(c.limit) < wbid - 1e-9 \
                and not any(o['kind'] == 'CANCEL' and o['key'] == k for o in ops):
            row['reason'] = 'REQUOTE_CANCEL'
            return [*ops, {'kind': 'CANCEL', 'key': k, 'origin': 'WHOLE_POLICY', 'reason': 'V67_ZONE_MIRROR_REQUOTE'}]
    if live:
        row['reason'] = 'MIRROR_IN_FLIGHT'; return ops
    if ROUTE == 'ACTIVE' and any(o['kind'] == 'NEW' and o.get('route') == 'ACTIVE' for o in ops):
        row['reason'] = 'EXISTING_ACTIVE_PLAN_PRIORITY'; return ops
    ask = ((frame.get('quotes') or {}).get(W) or {}).get('ask')
    depth = float(asks[min(asks)]) if W == 'UP' else float(bids[max(bids)])
    wp = frame['world_profile']; step = float(wp['quantity_step'])
    if ROUTE == 'PASSIVE':
        wbid = float(max(bids)) if W == 'UP' else round(1. - float(min(asks)), 10)
        need = ALPHA * STATE['zoneF'] - STATE['zoneW']
        if not 0 < wbid < 1 or need < TICKET - EPS or TICKET * wbid < 1. - EPS:
            row['reason'] = 'BALANCED_OR_BELOW_TICKET'; return ops
        p = wbid; q = TICKET
    else:
        q = plan(STATE['zoneF'], STATE['zoneW'], None if ask is None else float(ask), depth, step)
        if q is None:
            row['reason'] = 'BALANCED_OR_BELOW_MINIMUM'; return ops
        p = float(ask)
    planned = with_plan(snapshot(frame, ledger), ops)
    g_ = float(governor_q(planned, W, p, q, ROUTE)); r_ = float(risk_q(planned, W, p, q, ROUTE))
    if ROUTE == 'PASSIVE':
        if min(g_, r_) < q - EPS:
            row['reason'] = 'GOVERNOR_OR_RISK_FLOOR'; return ops
    else:
        q = floor_step(min(q, g_, r_), step)
        if q <= EPS or q * p < 1. - EPS:
            row['reason'] = 'GOVERNOR_OR_RISK_FLOOR'; return ops
    owners = [{'key': o['key'], 'side': o['side'], 'price': o['limit']} for o in planned['owners']]
    if crossing(W, p, owners):
        row['reason'] = 'WAIT_OWN_CROSS'; return ops
    if abs(p / float(wp['tick']) - round(p / float(wp['tick']))) > 1e-8:
        row['reason'] = 'OFF_TICK'; return ops
    try:
        validate(wp['asset'], ROUTE, p, q, quantity_step=wp['quantity_step'])
    except Exception as exc:
        row.update(reason='VALIDATE_REJECT', error=repr(exc)[:200]); return ops
    n = int(frame['own_view']['n']) + sum(o['kind'] == 'NEW' for o in ops)
    new = {'kind': 'NEW', 'key': f'{W}_{n}', 'parent_id': roles.pid(W), 'side': W, 'route': ROUTE, 'price': p, 'qty': q, 'role': ROLE}
    STATE['keys'].append(new['key']); STATE['orders'].append(dict(t=t, cm=cm, zoneF=STATE['zoneF'], zoneW=STATE['zoneW'], **new))
    row.update(reason='MIRROR_ORDER', key=new['key'], qty=q, price=p)
    return [*ops, new]


def finish(out):
    payload = dict(enabled=ENABLED, alpha=ALPHA, route=ROUTE, lo=LO, hi=HI, zoneF=STATE['zoneF'], zoneW=STATE['zoneW'], orders=STATE['orders'], ended=STATE['ended'], rows=STATE['rows'])
    with gzip.GzipFile(filename=str(Path(out) / 'zone_mirror_trace.json.gz'), mode='wb', mtime=0) as fh:
        fh.write(json.dumps(payload, sort_keys=True, separators=(',', ':'), default=float).encode())
    return dict(enabled=ENABLED, alpha=ALPHA, route=ROUTE, orders=len(STATE['orders']), zoneF=STATE['zoneF'], zoneW=STATE['zoneW'],
                ordered_qty=sum(o['qty'] for o in STATE['orders']), live_eligible=False)


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir() and not (Path(out) / 'zone_mirror_trace.json.gz').exists():
        finish(out)


atexit.register(_save_exit)
