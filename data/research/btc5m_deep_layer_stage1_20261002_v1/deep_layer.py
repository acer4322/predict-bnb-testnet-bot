"""Preregistered DEEP v1: physical-side bid minus two ticks, fixed15, unfilled TTL2000ms.
Runtime inputs are current public book, OWN ledger and current plan only.
No replacement while an owner is nonterminal, including same-plan CANCEL.
"""
import atexit
import json
import os
from pathlib import Path

MODE = os.environ.get('V12G_DEEP_LAYER', 'OFF')
assert MODE in ('ON', 'OFF')
ENABLED = MODE == 'ON'
ROLE = 'DEEP_LAYER'
TICKET = 15.0
TTL_MS = 2000
EPS = 1e-8
KEYSET = set()
ORDERS = {}
STATS = dict(attempts=0, placed=0, freeze=0, STOP290=0, risk_floor=0,
             self_cross=0, maintenance_conflict=0, governor=0, occupied=0,
             below_price=0, venue_validation=0, missing_book=0, ttl_cancel=0)
SERVICE = None
OWNED_OLD = "acc=ledger.account(pid[s]);owned=float(v['inv'][s])+acc['reserved_qty'];"
MAINT_OLD = "    for k,c in live.items():\n     s=ledger.grants[c.parent_id].side;stale="


def instrument(source):
    if not ENABLED:
        return source
    assert source.count(OWNED_OLD) == source.count(MAINT_OLD) == 1
    return source.replace(OWNED_OLD, OWNED_OLD[:-1]+"-__import__('deep_layer').pending(ledger,s);", 1).replace(
        MAINT_OLD, "    for k,c in live.items():\n     if __import__('deep_layer').owns(k):continue\n     s=ledger.grants[c.parent_id].side;stale=", 1)


def owns(key):
    return ENABLED and key in KEYSET


def pending(ledger, side):
    if not ENABLED:
        return 0.0
    return sum(max(0.0, float(c.qty)-float(c.filled)) for key, c in ledger.carriers.items()
               if key in KEYSET and c.state != 'TERMINAL' and ledger.grants[c.parent_id].side == side)


def install(ctx, roles, frozen, governor_q, risk_q, with_plan):
    global SERVICE
    assert SERVICE is None
    SERVICE = (ctx, roles, frozen, governor_q, risk_q, with_plan)


def on_plan(frame, ops):
    if not ENABLED:
        return ops
    assert SERVICE is not None
    ctx, roles, frozen, governor_q, risk_q, with_plan = SERVICE
    from tools.pair_core_asset_route_sizing_v2 import validate_size
    from tools.hft244_pair_route_legality_v1 import crossing_owners
    return extend(frame, ops, roles, frozen, ctx.snapshot, with_plan,
                  governor_q, risk_q, validate_size, crossing_owners)


def extend(frame, ops, roles, frozen, snapshot, with_plan, governor_q, risk_q, validate, crossing):
    if not ENABLED:
        return ops
    t, start, end = (int(frame[k]) for k in ('t', 'start', 'end'))
    if not start <= t < end:
        return ops
    ledger = frame['ledger']
    out = list(ops)
    cancel_keys = {o['key'] for o in out if o['kind'] == 'CANCEL'}
    live = {k: c for k, c in ledger.carriers.items() if k in KEYSET and c.state != 'TERMINAL'}
    for k, c in live.items():
        # Partial fills retain the owner until canonical terminal, or universal STOP290.
        if t-ORDERS[k]['place_t'] >= TTL_MS and float(c.filled) <= EPS and t-start < 290000:
            if c.state != 'CANCEL_PENDING' and frame.get('cancellable', {}).get(k, False) and k not in cancel_keys:
                out.append(dict(kind='CANCEL', key=k, origin='WHOLE_POLICY', reason='DEEP_TTL'))
                cancel_keys.add(k)
                STATS['ttl_cancel'] += 1
    if t-start >= 290000:
        STATS['attempts'] += 2
        STATS['STOP290'] += 2
        return out  # inherited universal cancel-all owns the cancellation branch
    book = frame.get('book') or {}
    bids, asks = book.get('bids') or {}, book.get('asks') or {}
    wp = frame['world_profile']
    tick = float(wp['tick'])
    from passive_offset import _cross
    for side in ('UP', 'DOWN'):
        STATS['attempts'] += 1
        if frozen():
            STATS['freeze'] += 1
            continue
        if any(ledger.grants[c.parent_id].side == side for c in live.values()):
            STATS['occupied'] += 1
            continue
        if not bids or not asks:
            STATS['missing_book'] += 1
            continue
        bid = float(max(bids)) if side == 'UP' else round(1.0-float(min(asks)), 10)
        price = round(bid-2*tick, 10)
        if price < 0.01-EPS:
            STATS['below_price'] += 1
            continue
        planned = with_plan(snapshot(frame, ledger), out)
        if any(o['side'] == side and abs(float(o['limit'])-price) < EPS for o in planned['owners']):
            STATS['maintenance_conflict'] += 1
            continue
        if len(planned['owners']) >= wp['max_live_owners']:
            STATS['maintenance_conflict'] += 1
            continue
        if float(governor_q(planned, side, price, TICKET)) < TICKET-EPS:
            STATS['governor'] += 1
            continue
        if float(risk_q(planned, side, price, TICKET)) < TICKET-EPS:
            STATS['risk_floor'] += 1
            continue
        owners = [dict(key=o['key'], side=o['side'], price=o['limit']) for o in planned['owners']]
        # Native r3 rounds both UP-native prices to two decimals; CANCEL never releases res.
        if crossing(side, price, owners) or _cross(side, price, [(o['side'], float(o['limit'])) for o in planned['owners']]):
            STATS['self_cross'] += 1
            continue
        try:
            validate(wp['asset'], 'PASSIVE', price, TICKET, quantity_step=wp['quantity_step'])
        except Exception:
            STATS['venue_validation'] += 1
            continue
        n = int(frame['own_view']['n'])+sum(o['kind'] == 'NEW' for o in out)
        key = f'{side}_{n}'
        op = dict(kind='NEW', key=key, parent_id=roles.pid(side), side=side,
                  route='PASSIVE', price=price, qty=TICKET, role=ROLE)
        KEYSET.add(key)
        ORDERS[key] = dict(side=side, price=price, best_bid=bid, tick=tick, place_t=t,
                           freeze_at_placement=bool(frozen()))
        STATS['placed'] += 1
        out.append(op)
    return out


def finish(out):
    import gzip
    out = Path(out)
    p = out/'clock_trace.json.gz'
    if not p.exists():
        return dict(enabled=ENABLED, complete=False, stats=dict(STATS))
    trace = json.loads(gzip.decompress(p.read_bytes()))
    final = trace.get('demand_final') or {}
    carriers = {c['key']: c for c in final.get('all_final_carriers', [])}
    receipts = final.get('full_raw_receipts', [])
    accepted = {o['key'] for p in trace['plans'] for o in p['operations']
                if o['kind'] == 'NEW' and o.get('role') == ROLE}
    rows = []
    for key, original in ORDERS.items():
        if key not in accepted:
            continue
        cs = [dict(t=p['t'], reason=o.get('reason')) for p in trace['plans'] for o in p['operations']
              if o['kind'] == 'CANCEL' and o['key'] == key]
        fs = [dict(exchange_ns=r['exchange_ts'], receive_ns=r['receive_ts'],
                   exchange_ms=r['exchange_ts']/1000000, receive_ms=r['receive_ts']/1000000,
                   price=r['contractPrice'], qty=r['qty'], maker=r['maker'])
              for r in receipts if r['key'] == key and r['qty'] > 0]
        c = carriers.get(key, {})
        rows.append(dict(order_seq=len(rows)+1, **original, qty=TICKET,
                         cancel_t=cs[0]['t'] if cs else None, cancels=cs, fills=fs,
                         filled_qty=float(c.get('filled', 0)), terminal_status=c.get('state', 'UNKNOWN')))
    payload = dict(method='DEEP_LAYER_v1', enabled=ENABLED, time_unit='place/cancel ms; canonical fill ns and ms',
                   ttl_ms=TTL_MS, ticks_below_bid=2, ticket=TICKET, stats=dict(STATS), orders=rows,
                   accepted_orders=len(accepted), complete=final.get('receipt_history_complete', False))
    (out/'deep_layer_trace.json').write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    return dict(enabled=ENABLED, complete=payload['complete'], orders=len(rows),
                filled_orders=sum(r['filled_qty'] > EPS for r in rows),
                filled_qty=sum(r['filled_qty'] for r in rows), stats=dict(STATS))


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir() and not (Path(out)/'deep_layer_trace.json').exists():
        finish(out)


atexit.register(_save_exit)
