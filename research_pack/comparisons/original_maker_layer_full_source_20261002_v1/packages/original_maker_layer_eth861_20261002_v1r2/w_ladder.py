"""CG3 candidate WL (weak-side passive ladder), high-conviction markets only: after DECIDE, before the first FLIP and before 290 s,
keep up to K passive tickets resting on the current weak side at GAP, 2*GAP, ... below its best bid (never at the best bid), price
<= PXMAX, while weak shares + all pending weak shares < RATIO x strong shares. Filled levels are re-posted; orders that drift out of
the band [wbid - (K+1)*GAP, wbid - tick] are cancelled; all ladder orders are cancelled at the first FLIP. Runtime inputs: own
ledger/inventory, current book, the strategy's own DECIDE/FLIP labels. No clock rule beyond STOP290.
r1: the frozen V8 manager is instrumented (runtime copy of its source; base files untouched) so that its maintenance loop skips
ladder keys and its owned_map excludes ladder pending (otherwise it cancels every ladder order as stale and suppresses its own
weak-side bids). With the ladder disabled the instrumentation is the identity."""
import atexit, gzip, json, os
from pathlib import Path

RATIO = float(os.environ['V12G_WL_RATIO']) if os.environ.get('V12G_WL_RATIO') else None
ENABLED = RATIO is not None
K = int(os.environ.get('V12G_WL_LEVELS') or 4)
GAP = float(os.environ.get('V12G_WL_GAP') or 0.02)
PXMAX = float(os.environ.get('V12G_WL_PXMAX') or 0.45)
TICKET = float(os.environ.get('V12G_PASSIVE_TICKET') or 15.)
CUTOFF_MS = 290_000; LATENCY_MS = 250; EPS = 1e-8
ROLE = 'CG3_W_LADDER'
KEYSET = set()
STATE = dict(keys=[], orders=[], cancels=[], reasons={}, rows=0, flip_retired=False)


OWNED_OLD = "acc=ledger.account(pid[s]);owned=float(v['inv'][s])+acc['reserved_qty'];"
OWNED_NEW = "acc=ledger.account(pid[s]);owned=float(v['inv'][s])+acc['reserved_qty']-__import__('w_ladder').pending(ledger,s);"
MAINT_OLD = "    for k,c in live.items():\n     s=ledger.grants[c.parent_id].side;stale="
MAINT_NEW = "    for k,c in live.items():\n     if __import__('w_ladder').owns(k):continue\n     s=ledger.grants[c.parent_id].side;stale="


def instrument(source):
    if not ENABLED: return source
    assert source.count(OWNED_OLD) == 1 and source.count(MAINT_OLD) == 1
    return source.replace(OWNED_OLD, OWNED_NEW, 1).replace(MAINT_OLD, MAINT_NEW, 1)


def owns(k):
    return ENABLED and k in KEYSET


def pending(ledger, side):
    if not ENABLED: return 0.
    out = 0.
    for k in KEYSET:
        c = ledger.carriers.get(k)
        if c is None or c.state == 'TERMINAL' or ledger.grants[c.parent_id].side != side: continue
        out += max(0., float(c.qty) - float(c.filled))
    return out


def levels(wbid, tick, k=None, gap=None, pxmax=None):
    k = K if k is None else k; gap = GAP if gap is None else gap; pxmax = PXMAX if pxmax is None else pxmax
    out = []
    for i in range(1, k + 1):
        lv = round(round((wbid - i * gap) / tick) * tick, 10)
        if tick - EPS <= lv <= pxmax + EPS: out.append(lv)
    return out


def in_band(price, wbid, tick, k=None, gap=None):
    k = K if k is None else k; gap = GAP if gap is None else gap
    return wbid - (k + 1) * gap - EPS <= price <= wbid - tick + EPS


def need(inv_strong, inv_weak, pend_weak, ratio=None):
    return (RATIO if ratio is None else ratio) * inv_strong - inv_weak - pend_weak


def _why(r):
    STATE['reasons'][r] = STATE['reasons'].get(r, 0) + 1


def extend(frame, ops, roles, high_mode, snapshot, with_plan, governor_q, risk_q, validate, crossing):
    if not ENABLED or not high_mode():
        return ops
    t = int(frame['t']); start = int(frame['start']); end = int(frame['end']); STATE['rows'] += 1
    if roles.side is None or not start <= t < end: return ops
    ledger = frame['ledger']; canc = frame.get('cancellable', {})
    planned_cancel = {o['key'] for o in ops if o['kind'] == 'CANCEL'}
    live = [k for k in STATE['keys'] if k in ledger.carriers and ledger.carriers[k].state != 'TERMINAL']
    def cancel(k, reason):
        c = ledger.carriers[k]
        if c.state == 'CANCEL_PENDING' or not canc.get(k, False) or k in planned_cancel: return None
        planned_cancel.add(k); STATE['cancels'].append(dict(t=t, key=k, reason=reason))
        return {'kind': 'CANCEL', 'key': k, 'origin': 'WHOLE_POLICY', 'reason': reason}
    if any(e['kind'] == 'FLIP' for e in getattr(roles, 'v12g_events', [])):
        STATE['flip_retired'] = True
        add = [x for x in (cancel(k, 'CG3_WL_FLIP_RETIRE') for k in live) if x]
        _why('FIRST_FLIP'); return [*ops, *add]
    if t - start >= CUTOFF_MS or t + LATENCY_MS >= end: _why('STOP290_OR_ARRIVAL'); return ops   # hard stop cancels everything itself
    S = roles.strong; Wk = roles.weak
    book = frame.get('book') or {}; bids = book.get('bids') or {}; asks = book.get('asks') or {}
    if not bids or not asks: return ops
    wbid = float(max(bids)) if Wk == 'UP' else round(1. - float(min(asks)), 10)
    wp = frame['world_profile']; tick = float(wp['tick'])
    add = []
    for k in live:
        c = ledger.carriers[k]; px = float(c.limit)
        if not in_band(px, wbid, tick):
            x = cancel(k, 'CG3_WL_REQUOTE')
            if x: add.append(x)
    resting = [k for k in live if k not in planned_cancel]
    held = {round(float(ledger.carriers[k].limit), 6) for k in resting}
    if len(resting) >= K: _why('LADDER_FULL'); return [*ops, *add]
    lv = next((x for x in levels(wbid, tick) if round(x, 6) not in held), None)
    if lv is None: _why('NO_FREE_LEVEL'); return [*ops, *add]
    planned = with_plan(snapshot(frame, ledger), [*ops, *add])
    inv = {s: float(frame['own_view']['inv'][s]) for s in ('UP', 'DOWN')}
    if need(inv[S], inv[Wk], float(planned['pending_qty'][Wk])) < TICKET - EPS: _why('COVERED'); return [*ops, *add]
    q = TICKET
    if TICKET * lv < 1. - EPS: _why('BELOW_MIN_NOTIONAL'); return [*ops, *add]
    if min(float(governor_q(planned, Wk, lv, q)), float(risk_q(planned, Wk, lv, q))) < q - EPS: _why('GOVERNOR_OR_RISK'); return [*ops, *add]
    if crossing(Wk, lv, [{'key': o['key'], 'side': o['side'], 'price': o['limit']} for o in planned['owners']]): _why('OWN_CROSS'); return [*ops, *add]
    try: validate(wp['asset'], 'PASSIVE', lv, q, quantity_step=wp['quantity_step'])
    except Exception: _why('VALIDATE_REJECT'); return [*ops, *add]
    n = int(frame['own_view']['n']) + sum(o['kind'] == 'NEW' for o in ops)
    new = {'kind': 'NEW', 'key': f'{Wk}_{n}', 'parent_id': roles.pid(Wk), 'side': Wk, 'route': 'PASSIVE', 'price': lv, 'qty': q, 'role': ROLE}
    STATE['keys'].append(new['key']); KEYSET.add(new['key']); STATE['orders'].append(dict(t=t, wbid=wbid, inv_strong=inv[S], inv_weak=inv[Wk], **new)); _why('ORDER')
    return [*ops, *add, new]


def finish(out):
    payload = dict(enabled=ENABLED, ratio=RATIO, k=K, gap=GAP, pxmax=PXMAX, orders=STATE['orders'], cancels=STATE['cancels'], reasons=STATE['reasons'], rows=STATE['rows'])
    with gzip.GzipFile(filename=str(Path(out) / 'w_ladder_trace.json.gz'), mode='wb', mtime=0) as fh:
        fh.write(json.dumps(payload, sort_keys=True, separators=(',', ':'), default=float).encode())
    return dict(enabled=ENABLED, ratio=RATIO, k=K, gap=GAP, pxmax=PXMAX, orders=len(STATE['orders']), cancels=len(STATE['cancels']))


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir() and not (Path(out) / 'w_ladder_trace.json.gz').exists(): finish(out)


atexit.register(_save_exit)
