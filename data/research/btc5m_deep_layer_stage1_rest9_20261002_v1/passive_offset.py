"""ATBID: the frozen V8 manager's passive price is best bid - softplus(w9) * tick (~1 tick below the bid). With
V12G_PASSIVE_OFFSET_TICKS set, a runtime copy of its source (base files untouched) uses best bid - OFFSET * tick instead
(0 = join the best bid). Stale/requote logic uses the same price. Identity when the variable is unset.
r1: joining the bid can make our bid on one side complementary to a live own order on the other side (UP x + DOWN y >= 1 is a
native self-cross and the engine rejects the whole plan). The price is therefore capped at 1 - (highest live opposite-side own
limit) - tick; every capped quote is counted and written to passive_offset_trace.json."""
import atexit, json, math, os
from pathlib import Path

OFFSET = float(os.environ['V12G_PASSIVE_OFFSET_TICKS']) if os.environ.get('V12G_PASSIVE_OFFSET_TICKS') not in (None, '') else None
ENABLED = OFFSET is not None
OLD = "prices[s]=round(math.floor((bid-softplus(w[9])*tick+1e-10)/tick)*tick,10)"
NEW = "prices[s]=__import__('passive_offset').price(s,bid,tick,live,ledger)"
STATS = dict(quotes=0, capped=0)


def instrument(source):
    if not ENABLED: return source
    assert source.count(OLD) == 1
    return source.replace(OLD, NEW, 1)


def price(side, bid, tick, live, ledger):
    p = round(math.floor((bid - OFFSET * tick + 1e-10) / tick) * tick, 10)
    opp = [float(c.limit) for c in live.values() if ledger.grants[c.parent_id].side != side]
    STATS['quotes'] += 1
    if opp:
        cap = round(math.floor((1. - max(opp) - tick + 1e-10) / tick) * tick, 10)
        if cap < p - 1e-12: p = cap; STATS['capped'] += 1
    return p


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if ENABLED and out and Path(out).is_dir():
        (Path(out) / 'passive_offset_trace.json').write_text(json.dumps(dict(offset=OFFSET, **STATS)), encoding='utf-8')


atexit.register(_save_exit)


# r3: final plan pass (inserted before the governor/risk plan verification in envelope), replaying the engine's sequential
# reserve with its native self-cross rule (UP x vs DOWN y crosses when round(x,2) >= round(1-y,2); all non-terminal owners count,
# including cancel-pending ones). Manager passive NEW orders (no 'role') that would cross are lowered below the opposite price
# (dropped under one tick); any other NEW order that would cross an own owner is dropped (the engine would reject the plan).
STATS.update(plan_lowered=0, plan_dropped=0, other_dropped={})


def _cross(side, price, res):
    nat = round(price if side == 'UP' else 1 - price, 2)
    for s_, p_ in res:
        if s_ == side: continue
        opp = round(p_ if s_ == 'UP' else 1 - p_, 2)
        if (nat >= opp) if side == 'UP' else (nat <= opp): return True
    return False


def resolve(f, ops):
    if not ENABLED: return ops
    ledger = f['ledger']; tick = float(f['world_profile']['tick'])
    res = [(ledger.grants[c.parent_id].side, float(c.limit)) for k, c in ledger.carriers.items() if c.state != 'TERMINAL']
    out = []
    for o in ops:
        if o['kind'] != 'NEW': out.append(o); continue
        side = o['side']; p = float(o['price'])
        if _cross(side, p, res):
            if o.get('route') == 'PASSIVE' and 'role' not in o:
                opp = max(p_ for s_, p_ in res if s_ != side)
                p = round(math.floor((1. - opp - tick + 1e-10) / tick) * tick, 10)
                if p < tick - 1e-12 or _cross(side, p, res): STATS['plan_dropped'] += 1; continue
                o = dict(o, price=p); STATS['plan_lowered'] += 1
            else:
                r = str(o.get('role') or o.get('route')); STATS['other_dropped'][r] = STATS['other_dropped'].get(r, 0) + 1; continue
        res.append((side, p)); out.append(o)
    return out
