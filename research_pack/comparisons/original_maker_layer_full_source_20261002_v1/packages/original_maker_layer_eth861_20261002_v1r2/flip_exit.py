"""V60 flip exit: after the first observed strategy FLIP, cancel everything, flatten the position and never re-enter.

The native world has one UP book: selling x UP at bid b is the same order as buying x DOWN at 1-b, and with zero
fees both give identical branch payoffs. "Sell everything" is therefore executed as active buys of the short side
until UP and DOWN inventories are equal (both branches then equal the locked result). No new order of any kind
after the user STOP290 cutoff, or when the exit remainder is below the research minimum notional.
Runtime inputs: the FLIP event label, own ledger/inventory, current quotes/book. No Target/winner/future data.
"""
import atexit, gzip, json, math, os
from pathlib import Path

MODE = os.environ.get('V12G_FLIP_EXIT', 'OFF')
assert MODE in ('OFF', 'ON')
CUTOFF_MS = 290_000
LATENCY_MS = 250
EPS = 1e-8
ROLE = 'FLIP_EXIT_FLATTEN'
ROLES = None            # bound by run_variant to module.roles
ROWS = []
KEYS = []


def first_flip_t(roles=None):
    r = ROLES if roles is None else roles
    fl = [e['t'] for e in getattr(r, 'v12g_events', []) if e['kind'] == 'FLIP']
    return fl[0] if fl else None


def active(frame, mode=None, roles=None):
    if (MODE if mode is None else mode) != 'ON':
        return False
    t0 = first_flip_t(roles)
    return t0 is not None and int(frame['t']) >= int(t0) and int(frame['start']) <= int(frame['t'])


def floor_step(x, step):
    return max(0., round(math.floor((max(0., x) + 1e-9) / step) * step, 8))


def plan_exit(frame, sides_pending, inv, live_exit, crossing_fn, owners, step, tick):
    """Pure decision. Returns (op_fields or None, row)."""
    t = int(frame['t']); start = int(frame['start']); end = int(frame['end'])
    row = dict(t=t, index=int(frame['index']), inv=dict(inv), pending=dict(sides_pending))
    if t >= end or t - start >= CUTOFF_MS:
        row['reason'] = 'STOP290_OR_END'; return None, row
    if t + LATENCY_MS >= end:
        row['reason'] = 'ARRIVAL_AFTER_END'; return None, row
    long_side = 'UP' if inv['UP'] >= inv['DOWN'] else 'DOWN'; short = 'DOWN' if long_side == 'UP' else 'UP'
    need = inv[long_side] - inv[short] - sides_pending[short]
    row.update(long=long_side, short=short, need=need)
    if live_exit:
        row['reason'] = 'EXIT_ORDER_IN_FLIGHT'; return None, row
    quotes = (frame.get('quotes') or {}).get(short) or {}
    ask = quotes.get('ask')
    book = frame.get('book') or {}
    if short == 'UP':
        lvl = book.get('asks') or {}; depth = float(lvl[min(lvl)]) if lvl else 0.
    else:
        lvl = book.get('bids') or {}; depth = float(lvl[max(lvl)]) if lvl else 0.
    if ask is None or not 0 < float(ask) < 1 or depth <= 0:
        row['reason'] = 'NO_VALID_BOOK'; return None, row
    p = float(ask)
    q = floor_step(min(need, depth), step)
    row.update(ask=p, depth=depth, quantity=q)
    if q <= EPS or q * p < 1. - EPS:
        row['reason'] = 'FLAT_OR_BELOW_MINIMUM'; return None, row
    if abs(p / tick - round(p / tick)) > 1e-8:
        row['reason'] = 'OFF_TICK'; return None, row
    conflicts = crossing_fn(short, p, owners)
    if conflicts:
        row.update(reason='WAIT_OWN_CROSS', conflicts=conflicts); return None, row
    row['reason'] = 'EXIT_ORDER'
    return dict(side=short, price=p, qty=q), row


def extend(f, policy, ops, validate=None):
    """Called inside the (market-end | STOP290 | flip-exit) branch of Policy.produce, after its CANCELs."""
    if not active(f):
        return ops
    from hft244_pair_route_legality_v1 import crossing_owners
    ledger = f['ledger']
    inv = {s: float(f['own_view']['inv'][s]) for s in ('UP', 'DOWN')}
    pend = {'UP': 0., 'DOWN': 0.}; owners = []; live_exit = False
    for k, c in ledger.carriers.items():
        if c.state == 'TERMINAL':
            continue
        side = ledger.grants[c.parent_id].side
        pend[side] += max(0., float(c.qty) - float(c.filled))
        owners.append(dict(key=k, side=side, price=float(c.limit)))
        live_exit = live_exit or k in KEYS
    wp = f['world_profile']
    op, row = plan_exit(f, pend, inv, live_exit, crossing_owners, owners, float(wp['quantity_step']), float(wp['tick']))
    if op is not None and validate is not None:
        try:
            validate(wp['asset'], 'ACTIVE', op['price'], op['qty'], quantity_step=wp['quantity_step'])
        except Exception as exc:
            row.update(reason='VALIDATE_REJECT', error=repr(exc)[:200]); op = None
    ROWS.append(row)
    if op is None:
        return ops
    n = int(f['own_view']['n']) + sum(o['kind'] == 'NEW' for o in ops)
    new = dict(kind='NEW', key=f"{op['side']}_{n}", parent_id=1 if op['side'] == 'UP' else 2, side=op['side'], route='ACTIVE',
               price=op['price'], qty=op['qty'], role=ROLE)
    KEYS.append(new['key']); row['key'] = new['key']
    return [*ops, new]


def finish(out):
    payload = dict(mode=MODE, cutoff_ms=CUTOFF_MS, first_flip_t=first_flip_t() if ROLES is not None else None, keys=KEYS, rows=ROWS)
    with gzip.GzipFile(filename=str(Path(out) / 'flip_exit_trace.json.gz'), mode='wb', mtime=0) as fh:
        fh.write(json.dumps(payload, sort_keys=True, separators=(',', ':'), default=float).encode())
    return dict(mode=MODE, first_flip_t=payload['first_flip_t'], exit_orders=len(KEYS), frames=len(ROWS), live_eligible=False)


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir() and not (Path(out) / 'flip_exit_trace.json.gz').exists():
        finish(out)


atexit.register(_save_exit)
