"""V59 stage-1 component tests for bpr.py. Host-only: no DLL, no runner, no native, no Target/winner data.
Frozen constants: K=4.15, RETAIN=0.5 (V58 env). Crossing = frozen hft244_pair_route_legality_v1.crossing_owners."""
import os
os.environ['V12X_K'] = '4.15'
os.environ['V12X_RETAIN'] = '0.5'
import hashlib, importlib.util, json, math, random, sys
from copy import deepcopy
from pathlib import Path

P = Path(__file__).resolve().parent
R = P.parent
sys.path.insert(0, str(P)); sys.path.insert(1, str(P / 'overlay'))
os.environ.pop('V12G_BPR', None); os.environ.pop('BTC5M_LAN_RESULT_DIR', None)
import economics as E
import bpr as B
assert E.K == 4.15 and E.RETAIN == 0.5

LEG = R / 'v12g_fresh30_generalization_20260927_v45' / 'base' / 'hft244_pair_route_legality_v1.py'
spec = importlib.util.spec_from_file_location('legality', LEG); leg = importlib.util.module_from_spec(spec); spec.loader.exec_module(leg)
crossing = leg.crossing_owners
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
checks = []
EPS = 1e-6
START, END = 1_000_000, 1_300_000


def ok(name, cond, info=None):
    assert cond, (name, info)
    checks.append(name)


def state_of(inv, cost, owners):
    pq = {'UP': 0., 'DOWN': 0.}; pc = {'UP': 0., 'DOWN': 0.}
    for o in owners:
        r = float(o.get('remaining', o['qty'])); pq[o['side']] += r; pc[o['side']] += r * o['limit']
    return {'inv': dict(inv), 'cost': cost, 'payoff': {s: inv[s] - cost for s in inv}, 'pending_qty': pq,
            'pending_cash': pc, 'owners': [dict(o) for o in owners]}


def frame(sec, index=0):
    return {'t': START + int(round(sec * 1000)), 'index': index, 'start': START, 'end': END}


def step_bpr(b, st, sec, ask=.30, depth=1000., peak=200., ops=(), flips=0, count=0, strong='UP', idx=0, key=None):
    weak = 'DOWN' if strong == 'UP' else 'UP'
    planned = E.with_plan(st, list(ops))
    row = E.proposal(planned, strong, ask, depth, peak, .01)
    f = frame(sec, idx)
    op, d = b.propose(f, planned, row, strong, weak, ask, peak, .01, list(ops), crossing, flips, count)
    if op is not None:
        op['key'] = key or f"{op['side']}_{len(b.orders) + 1000}"
        b.accepted(f, op)
    return op, d, row, planned


# Base: strong UP, weak DOWN. G=150, H=-150, peak 200 -> floor 100. Full repair unaffordable at 0.30.
INV = {'UP': 600., 'DOWN': 300.}; COST = 450.
BASE = state_of(INV, COST, [])

# ---------------------------------------------------------------- 1 partial feasible birth
b = B.BPR('ON')
op, d, row, pl = step_bpr(b, BASE, 30., key='DOWN_1')
ok('trigger_full_unaffordable', row['reason'] == 'FULL_RESTORATION_NOT_AFFORDABLE', row['reason'])
q_exp = B.floor_step(min(row['needed_qty'], row['total_cap'], 1000.), .01)
ok('birth_partial_qty', op is not None and abs(op['qty'] - q_exp) < EPS and op['qty'] < row['needed_qty'], (op, q_exp))
ok('birth_keeps_retained_floor', row['worst_G'] - op['qty'] * op['price'] >= row['retained_G_floor'] - EPS)
ok('birth_active_route_at_ask', op['route'] == 'ACTIVE' and op['price'] == .30 and op['side'] == 'DOWN')
w = b.works[0]
ok('work_identity', w['work_id'] == 1 and w['side'] == 'DOWN' and w['other'] == 'UP' and abs(w['target'] - q_exp) < EPS
   and abs(w['budget_cash'] - q_exp * .30) < EPS and w['birth_state'] == pl)

# ---------------------------------------------------------------- 2 budget too small
b2 = B.BPR('ON')
op2, d2, row2, _ = step_bpr(b2, BASE, 30., peak=299.)
ok('insufficient_budget_no_birth', op2 is None and d2['reason'] == 'BIRTH_BELOW_MINIMUM' and not b2.works, d2)

# ---------------------------------------------------------------- 3 same-side pending / same-plan NEW
own = {'key': 'DOWN_1', 'side': 'DOWN', 'qty': op['qty'], 'remaining': op['qty'], 'limit': .30, 'state': 'SUBMITTED'}
S3 = state_of(INV, COST, [own])
op3, d3, _, _ = step_bpr(b, S3, 30.3)
ok('own_pending_reserved_no_resend', op3 is None and d3['reason'] == 'REMAINDER_RESERVED_OR_BELOW_MINIMUM' and abs(d3['reserved'] - op['qty']) < EPS, d3)
b3 = B.BPR('ON')
plan_new = {'kind': 'NEW', 'key': 'DOWN_77', 'side': 'DOWN', 'qty': 15., 'price': .29, 'route': 'PASSIVE'}
op3b, d3b, row3b, _ = step_bpr(b3, BASE, 30., ops=[plan_new])
ok('same_plan_new_reduces_room', op3b is not None and op3b['qty'] < op['qty'] - 14. and
   row3b['worst_G'] - op3b['qty'] * .30 >= row3b['retained_G_floor'] - EPS, (op3b, op['qty']))
b3c = B.BPR('ON')
act = {'kind': 'NEW', 'key': 'UP_78', 'side': 'UP', 'qty': 15., 'price': .70, 'route': 'ACTIVE'}
op3c, d3c, _, _ = step_bpr(b3c, BASE, 30., ops=[act])
ok('existing_active_plan_priority', op3c is None and d3c['reason'] == 'EXISTING_ACTIVE_PLAN_PRIORITY', d3c)
b3d = B.BPR('ON')
op3d, d3d, _, _ = step_bpr(b3d, BASE, 30., count=5)
ok('shares_original_count_cap', op3d is None and d3d['reason'] == 'ORIGINAL_COMBINED_ACTIVE_CAP', d3d)

# ---------------------------------------------------------------- 4 opposite cancel-pending cross, no refund
b4 = B.BPR('ON')
cp = {'key': 'UP_5', 'side': 'UP', 'qty': 15., 'remaining': 15., 'limit': .72, 'state': 'CANCEL_PENDING'}
S4 = state_of(INV, COST, [cp])
op4, d4, row4, pl4 = step_bpr(b4, S4, 30.)
ok('cancel_pending_cross_blocks', op4 is None and d4['reason'] == 'PENDING_OR_PLAN_OWN_CROSS' and d4['conflicts'] == ['UP_5'], d4)
ok('cancel_pending_not_released', pl4['pending_qty']['UP'] == 15. and E.with_plan(S4, [{'kind': 'CANCEL', 'key': 'UP_5'}]) == S4)

# ---------------------------------------------------------------- 5 partial fill, dup receipt, terminal remainder
b5 = B.BPR('ON')
o5, _, _, _ = step_bpr(b5, BASE, 30., key='DOWN_1')
Q = o5['qty']
ok('receipt_credit', b5.on_receipt('r1', 'DOWN_1', 50.) and b5.works[0]['filled'] == 50.)
ok('duplicate_receipt_ignored', not b5.on_receipt('r1', 'DOWN_1', 50.) and b5.works[0]['filled'] == 50.)
ok('foreign_receipt_ignored', not b5.on_receipt('r9', 'DOWN_999', 30.) and b5.works[0]['filled'] == 50.)
inv5 = {'UP': 600., 'DOWN': 350.}; cost5 = COST + 50 * .30
live = dict(own, remaining=Q - 50., filled=50.)
S5 = state_of(inv5, cost5, [live])
o5b, d5b, _, _ = step_bpr(b5, S5, 30.5)
ok('partial_fill_remaining_reserved', o5b is None and abs(d5b['open_need']) < 1e-6, d5b)
S5t = state_of(inv5, cost5, [])       # remainder confirmed terminal (cancelled/expired), no more receipts
o5c, d5c, row5c, _ = step_bpr(b5, S5t, 31., key='DOWN_2')
ok('terminal_remainder_reusable_within_target', o5c is not None and o5c['qty'] <= Q - 50. + EPS and
   b5.works[0]['filled'] + o5c['qty'] <= b5.works[0]['target'] + EPS, (o5c, d5c))

# ---------------------------------------------------------------- 6 zero-fill terminal
b6 = B.BPR('ON')
o6, _, _, _ = step_bpr(b6, BASE, 30., key='DOWN_1')
o6b, d6b, _, _ = step_bpr(b6, BASE, 31., key='DOWN_2')   # owner gone, no receipt
ok('zero_fill_not_credited', b6.works[0]['filled'] == 0. and o6b is not None and abs(o6b['qty'] - o6['qty']) < EPS, d6b)

# ---------------------------------------------------------------- 7 price moves
b7 = B.BPR('ON'); step_bpr(b7, BASE, 30., key='DOWN_1')
o7, d7, _, _ = step_bpr(b7, BASE, 31., ask=.35)
ok('price_above_birth_waits', o7 is None and d7['reason'] == 'PRICE_ABOVE_BIRTH_LIMIT' and b7.works[0]['status'] == 'ACTIVE', d7)
b7b = B.BPR('ON'); o7b0, _, _, _ = step_bpr(b7b, BASE, 30., key='DOWN_1')
o7b, d7b, row7b, _ = step_bpr(b7b, BASE, 31., peak=290.)      # owner gone; higher peak shrinks cash room
ok('reserve_binds_later_order', o7b is not None and o7b['qty'] <= (row7b['worst_G'] - row7b['retained_G_floor']) / .30 + EPS
   and o7b['qty'] < o7b0['qty'], (o7b, d7b))

# ---------------------------------------------------------------- 8 actual FLIP keeps in-flight owners
b8 = B.BPR('ON'); o8, _, _, _ = step_bpr(b8, BASE, 30., key='DOWN_1')
S8 = state_of(INV, COST, [dict(own, qty=o8['qty'], remaining=o8['qty'])])
o8b, d8b, _, _ = step_bpr(b8, S8, 40., flips=1)
w8 = b8.works[0]
ok('flip_withdraws_no_new', w8['status'] == 'WITHDRAWN_DIRECTION_CHANGED' and o8b is None, d8b)
ok('flip_keeps_owner_tracked', 'DOWN_1' in w8['keys'] and b8.reserved(w8, S8) == o8['qty'] and b8.on_receipt('r8', 'DOWN_1', 10.))

# ---------------------------------------------------------------- 9 STOP290 and arrival
b9 = B.BPR('ON'); step_bpr(b9, BASE, 30., key='DOWN_1')
o9, d9, _, _ = step_bpr(b9, BASE, 290.)
ok('stop290_no_new_and_withdraw', o9 is None and d9['reason'] == 'STOP290' and b9.works[0]['status'] == 'WITHDRAWN_STOP290', d9)
b9b = B.BPR('ON')
o9b, d9b, _, _ = step_bpr(b9b, BASE, 290.001)
ok('stop290_no_birth', o9b is None and d9b['reason'] == 'STOP290' and not b9b.works, d9b)
o9c, d9c, _, _ = step_bpr(b9b, BASE, 289.999)
ok('before_290_allowed', o9c is not None, d9c)
b9d = B.BPR('ON', cutoff_ms=299_950)
o9d, d9d, _, _ = step_bpr(b9d, BASE, 299.8)
ok('arrival_after_end_blocked', o9d is None and d9d['reason'] == 'ARRIVAL_AFTER_END', d9d)

# ---------------------------------------------------------------- 10/11 renewal only from new confirmed expansion
b10 = B.BPR('ON'); o10, _, _, _ = step_bpr(b10, BASE, 30., key='DOWN_1')
q10 = o10['qty']; b10.on_receipt('a', 'DOWN_1', q10)
inv10 = {'UP': 600., 'DOWN': 300. + q10}; cost10 = COST + q10 * .30
S10 = state_of(inv10, cost10, [])
o10b, d10b, row10b, _ = step_bpr(b10, S10, 31.)
ok('target_reached_closes_work', b10.works[0]['status'] == 'CONFIRMED_TARGET_REACHED')
ok('still_unaffordable_but_no_expansion_no_rebirth', o10b is None and d10b['reason'] == 'NO_NEW_CONFIRMED_EXPANSION'
   and row10b['reason'] == 'FULL_RESTORATION_NOT_AFFORDABLE' and len(b10.works) == 1, (d10b, row10b['reason']))
for sec in (32., 40., 60.):
    ob, db, _, _ = step_bpr(b10, S10, sec)
    assert ob is None and len(b10.works) == 1
ok('no_per_frame_budget_regeneration', len(b10.works) == 1 and len(b10.orders) == 1)
inv11 = {'UP': 700., 'DOWN': 300. + q10}; cost11 = cost10 + 100 * .70         # confirmed strong expansion
S11 = state_of(inv11, cost11, [])
peak11 = 200.
o11, d11, row11, pl11 = step_bpr(b10, S11, 70., peak=peak11, key='DOWN_2')
new_debt = B.debt(pl11, 'UP') - b10.works[0]['stop_debt']
cap11 = B.floor_step(min(row11['needed_qty'], row11['total_cap'], 1000., new_debt / row11['slope']), .01)
ok('new_responsibility_birth', d11.get('birth_rule') == 'NEW_RESPONSIBILITY' and o11 is not None and len(b10.works) == 2, d11)
ok('new_responsibility_capped_by_new_debt', abs(b10.works[1]['target'] - cap11) < EPS and b10.works[1]['target'] <= new_debt / row11['slope'] + EPS,
   (b10.works[1]['target'], cap11, new_debt))
cum_q = sum(w['target'] for w in b10.works)
ok('cumulative_bounded_by_first_plus_new_debt', cum_q <= q10 + new_debt / row11['slope'] + EPS)

# ---------------------------------------------------------------- 12 new direction episode after FLIP
b12 = B.BPR('ON'); step_bpr(b12, BASE, 30., key='DOWN_1')
FL = state_of({'UP': 300., 'DOWN': 600.}, COST, [])     # now DOWN strong, UP weak
o12, d12, row12, _ = step_bpr(b12, FL, 100., strong='DOWN', flips=1, key='UP_1')
ok('new_direction_first_work', b12.works[0]['status'] == 'WITHDRAWN_DIRECTION_CHANGED' and d12.get('birth_rule') == 'FIRST_IN_DIRECTION'
   and o12 is not None and o12['side'] == 'UP' and b12.works[1]['birth_flips'] == 1, d12)

# ---------------------------------------------------------------- 13 original priority / not triggered
b13 = B.BPR('ON')
cheap = state_of({'UP': 600., 'DOWN': 520.}, 450., [])   # G=150, H=70 -> no negative branch
o13, d13, row13, _ = step_bpr(b13, cheap, 30.)
ok('not_triggered_without_negative_branch', o13 is None and d13['reason'] == 'NOT_TRIGGERED' and row13['reason'] == 'NO_RESTORABLE_NEGATIVE_BRANCH', d13)
small = state_of({'UP': 600., 'DOWN': 460.}, 500., [])   # G=100, H=-40: full affordable at 0.10
o13b, d13b, row13b, _ = step_bpr(b13, small, 30., ask=.10, peak=100.)
ok('original_full_repair_priority', row13b['eligible'] and o13b is None and d13b['reason'] == 'ORIGINAL_REPAIR_PRIORITY', (row13b['reason'], d13b))

# ---------------------------------------------------------------- 14 legacy context isolation + input immutability
class Ctx:  # attribute set used by run_variant Context
    def __init__(self):
        self.peak = 200.; self.first = None; self.first_active = None; self.prepare = None; self.events = []
        self.admissions = []; self.cancel_requests = []; self.scope_blocks = 0
ctx = Ctx(); before = deepcopy(vars(ctx))
b14 = B.BPR('ON')
st14 = deepcopy(BASE); planned14 = E.with_plan(st14, []); row14 = E.proposal(planned14, 'UP', .30, 1000., 200., .01)
pl_copy, row_copy = deepcopy(planned14), deepcopy(row14)
op14, _ = b14.propose(frame(30.), planned14, row14, 'UP', 'DOWN', .30, 200., .01, [], crossing, 0, 0)
ok('inputs_not_mutated', planned14 == pl_copy and row14 == row_copy and op14 is not None)
ok('legacy_ctx_untouched', vars(ctx) == before)
src = (P / 'bpr.py').read_text(encoding='utf-8')
import ast
idents = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
idents |= {a.arg for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) for a in n.args.args}
bad = {i for i in idents if any(tok in i.lower() for tok in ('ctx', 'winner', 'target_', 'settle', 'first_active', 'prepare'))}
ok('no_ctx_or_target_identifier_in_code', not bad, bad)

# ---------------------------------------------------------------- 15 OFF inert
b15 = B.BPR('OFF')
for sec in (5., 30., 100., 289., 291.):
    o15, d15, _, _ = step_bpr(b15, BASE, sec)
    assert o15 is None and d15['reason'] == 'INERT'
ok('off_inert', not b15.works and not b15.orders and not b15.receipts)

# ---------------------------------------------------------------- 16 randomized invariants
rng = random.Random(59)
for trial in range(400):
    b = B.BPR('ON'); inv = {'UP': 600., 'DOWN': 300.}; cost = 450.; owners = []; flips = 0; strong = 'UP'; peak = 200.
    expansions = 0; rid = 0; direction_episodes = 1
    for i in range(120):
        sec = 1 + i * 2.5 + rng.random()
        weak = 'DOWN' if strong == 'UP' else 'UP'
        ask = round(rng.uniform(.05, .6), 2); depth = rng.choice([0.5, 5., 50., 500.])
        # random fills / terminals of own owners
        nxt = []
        for o in owners:
            u = rng.random()
            if u < .3:
                q = round(min(o['remaining'], rng.choice([o['remaining'], o['remaining'] / 2])), 2)
                if q > 0:
                    rid += 1; b.on_receipt(f'r{rid}', o['key'], q); inv[o['side']] += q; cost += q * o['limit']; o = dict(o, remaining=round(o['remaining'] - q, 8))
                if o['remaining'] > 1e-9: nxt.append(o)
            elif u < .45: pass                                   # terminal zero-fill of remainder
            elif u < .55: nxt.append(dict(o, state='CANCEL_PENDING'))
            else: nxt.append(o)
        owners = nxt
        if rng.random() < .15:                                   # confirmed strong expansion
            q = rng.choice([15., 30.]); inv[strong] += q; cost += q * rng.uniform(.5, .8); expansions += 1
        if rng.random() < .02:                                   # strategy flip
            flips += 1; strong = weak; direction_episodes += 1
            weak = 'DOWN' if strong == 'UP' else 'UP'
        st = state_of(inv, cost, owners)
        peak = max(peak, st['payoff'][strong])
        planned = E.with_plan(st, [])
        row = E.proposal(planned, strong, ask, depth, peak, .01)
        f = frame(sec, i)
        op, d = b.propose(f, planned, row, strong, weak, ask, peak, .01, [], crossing, flips, 0)
        assert sum(w['status'] == 'ACTIVE' for w in b.works) <= 1
        if op is not None:
            assert f['t'] - START < 290_000 and f['t'] + 250 < END
            assert row['worst_G'] - op['qty'] * op['price'] >= row['retained_G_floor'] - 1e-6
            assert op['qty'] * op['price'] >= 1 - 1e-8 and op['qty'] <= depth + 1e-9
            op['key'] = f"{op['side']}_{trial}_{i}"; b.accepted(f, op)
            owners.append({'key': op['key'], 'side': op['side'], 'qty': op['qty'], 'remaining': op['qty'], 'limit': op['price'], 'state': 'SUBMITTED'})
        for w in b.works:
            live = sum(o['remaining'] for o in owners if o['key'] in w['keys'])
            assert w['filled'] + live <= w['target'] + 1e-6, (w['filled'], live, w['target'])
    assert len(b.works) <= direction_episodes + expansions, (len(b.works), direction_episodes, expansions)
ok('randomized_invariants_400x120', True)

# ---------------------------------------------------------------- 16b runtime hook seam (fakes for ledger/governor/risk/validate)
class Carrier:
    def __init__(self, filled): self.filled = filled
WP = {'quantity_step': .01, 'tick': .01, 'asset': 'BTC', 'max_live_owners': 4096}
def hframe(sec, n=40):
    return dict(frame(sec), world_profile=WP, own_view={'n': n}, quotes={'DOWN': {'ask': .30}, 'UP': {'ask': .71}})
def run_hook(b, st, sec, ops=(), carriers=None, gq=None, rq=None, val=None, bids={.70: 1000.}, decided=True, flips=1):
    return B.hook(b, hframe(sec), st, list(ops), decided, 'UP', 'DOWN', bids, 200., flips, 0, crossing, carriers or {},
                  gq or (lambda s, sd, p, q: q), rq or (lambda s, sd, p, q: q), val or (lambda *a, **k: None), lambda sd: 2 if sd == 'DOWN' else 1)
boff = B.BPR('OFF'); ops_in = [{'kind': 'CANCEL', 'key': 'UP_3'}]
out, dg = run_hook(boff, BASE, 30., ops=ops_in)
ok('hook_off_returns_same_ops', out is not None and out == ops_in and dg is None and not boff.rows)
bh = B.BPR('ON'); pas = {'kind': 'NEW', 'key': 'UP_40', 'side': 'UP', 'qty': 15., 'price': .60, 'route': 'PASSIVE', 'role': 'X'}
out, dg = run_hook(bh, BASE, 30., ops=[pas])
new = out[-1]
ok('hook_on_appends_one_active', len(out) == 2 and new['key'] == 'DOWN_41' and new['parent_id'] == 2 and new['route'] == 'ACTIVE'
   and new['role'] == 'ACTIVE_BOUNDED_PARTIAL_REPAIR' and set(new) == {'kind', 'key', 'parent_id', 'side', 'route', 'price', 'qty', 'role'}, out)
ok('hook_sees_same_plan_new', bh.works[0]['birth_state']['pending_qty']['UP'] == 15.)
bh2 = B.BPR('ON'); out2, dg2 = run_hook(bh2, BASE, 30., rq=lambda s, sd, p, q: 3.)
ok('hook_risk_floor_reduces_below_minimum', len(out2) == 0 and dg2['reason'] == 'GOVERNOR_OR_RISK_FLOOR' and not bh2.orders, dg2)
bh3 = B.BPR('ON'); out3, dg3 = run_hook(bh3, BASE, 30., gq=lambda s, sd, p, q: 20.)
ok('hook_governor_reduces_quantity', out3[-1]['qty'] == 20. and bh3.orders[-1]['qty'] == 20.)
def bad(*a, **k): raise ValueError('min size')
bh4 = B.BPR('ON'); out4, dg4 = run_hook(bh4, BASE, 30., val=bad)
ok('hook_validate_reject', len(out4) == 0 and dg4['reason'] == 'VALIDATE_REJECT' and not bh4.orders, dg4)
bh5 = B.BPR('ON'); o5_, _ = run_hook(bh5, BASE, 30.); k5 = o5_[-1]['key']; q5 = o5_[-1]['qty']
own5 = {'key': k5, 'side': 'DOWN', 'qty': q5 - 40., 'limit': .30, 'state': 'SUBMITTED'}   # snapshot qty = remaining
st5 = state_of({'UP': 600., 'DOWN': 340.}, COST + 12., [own5])
out5, dg5 = run_hook(bh5, st5, 30.5, carriers={k5: Carrier(40.), 'UP_1': Carrier(99.)})
ok('hook_ledger_fill_sync', bh5.works[0]['filled'] == 40. and len(out5) == 0 and abs(dg5['open_need']) < 1e-6, dg5)
run_hook(bh5, st5, 30.6, carriers={k5: Carrier(40.)})
ok('hook_fill_sync_idempotent', bh5.works[0]['filled'] == 40.)
try:
    run_hook(bh5, st5, 30.7, carriers={k5: Carrier(10.)}); regress = False
except AssertionError: regress = True
ok('hook_fill_regression_detected', regress)
bh6 = B.BPR('ON'); out6, dg6 = run_hook(bh6, BASE, 30., bids={})
ok('hook_no_depth_no_birth', len(out6) == 0 and not bh6.works, dg6)

bh7 = B.BPR('ON'); out7, dg7 = run_hook(bh7, BASE, 30., decided=False)
ok('hook_no_bpr_before_decide', len(out7) == 0 and dg7['reason'] == 'BEFORE_DECIDE' and not bh7.works and not bh7.orders, dg7)
out7b, _ = run_hook(bh7, BASE, 31., decided=True)
ok('hook_bpr_after_decide', len(out7b) == 1 and len(bh7.works) == 1)

bh9 = B.BPR('ON'); out9, dg9 = run_hook(bh9, BASE, 30., flips=0)
ok('hook_no_bpr_before_first_flip', len(out9) == 0 and dg9['reason'] == 'BEFORE_FIRST_FLIP' and not bh9.works and not bh9.orders, dg9)
out9b, _ = run_hook(bh9, BASE, 31., flips=1)
ok('hook_bpr_after_first_flip', len(out9b) == 1 and len(bh9.works) == 1 and bh9.works[0]['birth_flips'] == 1)

# ---------------------------------------------------------------- 17 V51 first-local replay (arithmetic parity)
GP = R / 'v12g_affordability_decomposition_20260927_v51' / 'GEOMETRY.json'
G = json.loads(GP.read_text(encoding='utf-8')) if GP.exists() else {'rows': []}
replayed = skipped = 0; details = []
for r in G['rows']:
    p = r['points'].get('first_local')
    if not p or not p.get('local_candidate'):
        skipped += 1; continue
    F, W = p['F'], p['W']; K = E.K
    # Rebuild a state with identical G, H, peak, pending and ratio debt (owners synthesised to the same penalty).
    margin = p['G'] + K * p['H']; target_pen = -p['total_ratio_debt'] - margin
    qW = p['pending_qty_W']; lW = p['pending_cash_W'] / qW if qW > 0 else 0.
    wpen = min(0., qW * (K - (K + 1) * lW)) if qW > 0 else 0.
    rest = target_pen - wpen
    owners = []
    if qW > 0: owners.append({'key': W + '_w', 'side': W, 'qty': qW, 'remaining': qW, 'limit': lW, 'state': 'SUBMITTED'})
    cF = p['pending_cash_F']
    if cF > 1e-9 or rest < -1e-9:
        x = rest + (K + 1) * cF              # x*(1-(K+1)*l) with x*l = cF
        if x <= 1e-9 or cF / x >= 1:
            skipped += 1; details.append(dict(market=r['market'], status='UNREPRESENTABLE')); continue
        owners.append({'key': F + '_f', 'side': F, 'qty': x, 'remaining': x, 'limit': cF / x, 'state': 'SUBMITTED'})
    inv_w = 0.; cost = inv_w - p['H']; inv_f = p['G'] + cost
    st = state_of({F: inv_f, W: inv_w}, cost, owners)
    assert abs((inv_f - inv_w - qW) - p['net_capacity']) < 1e-6, (r['market'], inv_f - inv_w - qW, p['net_capacity'])
    row = E.proposal(st, F, p['price'], p['visible_depth'], p['peak_G'], .01)
    bb = B.BPR('ON')
    t_ms = p['t'] - (p['t'] - p['seconds'] * 1000)
    fr = {'t': int(p['seconds'] * 1000), 'index': p['index'], 'start': 0, 'end': 300_000}
    op, d = bb.propose(fr, st, row, F, W, p['price'], p['peak_G'], .01, [], lambda *a: [], 0, 0)
    good = (row['reason'] == 'FULL_RESTORATION_NOT_AFFORDABLE' and abs(row['needed_qty'] - p['needed_qty']) < 1e-5
            and abs(row['cash_capacity'] - p['cash_capacity']) < 1e-5 and op is not None and abs(op['qty'] - p['partial_qty']) < 1e-6)
    details.append(dict(market=r['market'], seconds=p['seconds'], v51_partial_qty=p['partial_qty'], bpr_qty=op and op['qty'],
                        v51_partial_cost=p['partial_cost'], status='MATCH' if good else 'MISMATCH'))
    assert good, details[-1]
    replayed += 1
if GP.exists(): ok('v51_first_local_replay_match', replayed == 28, (replayed, skipped))

out = dict(status='PASS', checks=len(checks), names=checks, v51_replayed=replayed, v51_skipped=skipped, v51_details=details,
           constants=dict(K=E.K, RETAIN=E.RETAIN, cutoff_ms=B.CUTOFF_MS, latency_ms=B.LATENCY_MS, count_cap=B.COUNT_CAP),
           sha256={f: sha(P / f) for f in ('bpr.py', 'overlay/economics.py', 'test_bpr.py')}, v51_geometry_present=GP.exists(), legality_sha256=sha(LEG),
           native_paths=0, model_fits=0, target_loaded=False, winner_loaded=False)
if '--write' in sys.argv: (P / 'BPR_LOCAL_TESTS.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
print(json.dumps({k: out[k] for k in ('status', 'checks', 'v51_replayed', 'v51_skipped')}))
