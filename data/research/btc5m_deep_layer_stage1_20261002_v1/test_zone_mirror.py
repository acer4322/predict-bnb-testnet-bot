"""V65 host-only tests for zone_mirror (no native)."""
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
P = Path(__file__).resolve().parent; sys.path.insert(0, str(P)); sys.path.insert(1, str(P / 'overlay'))
os.environ['V12G_ZONE_MIRROR'] = 'ON'; os.environ.pop('BTC5M_LAN_RESULT_DIR', None)
import zone_mirror as Z
from economics import with_plan
checks = []
def ok(n, c, i=None):
    assert c, (n, i); checks.append(n)
ok('plan_closes_imbalance', Z.plan(300., 100., .45, 1000., .01) == 200.)
ok('plan_depth_limited', Z.plan(300., 100., .45, 50., .01) == 50.)
ok('plan_balanced_none', Z.plan(100., 100., .45, 1000., .01) is None and Z.plan(100., 101., .45, 1000., .01) is None)
ok('plan_min_notional', Z.plan(101., 100., .45, 1000., .01) is None)
ok('plan_alpha_half', Z.plan(300., 100., .45, 1000., .01, alpha=.5) == 50. and Z.plan(300., 160., .45, 1000., .01, alpha=.5) is None)
class Car:
    def __init__(s, st): s.state = st
class Led:
    def __init__(s): s.carriers = {}
def fr(t, inv, mid=.52, n=10):
    return dict(t=t, start=0, end=300000, book=dict(bids={round(mid - .01, 2): 500.}, asks={round(mid + .01, 2): 400.}),
                quotes=dict(UP=dict(ask=round(mid + .01, 2)), DOWN=dict(ask=round(1 - mid + .01, 2))), own_view=dict(inv=inv, n=n),
                world_profile=dict(quantity_step=.01, tick=.01, asset='BTC'), ledger=Led())
snap = lambda f, l: dict(inv=dict(f['own_view']['inv']), cost=0., payoff=dict(UP=0., DOWN=0.), pending_qty=dict(UP=0., DOWN=0.), pending_cash=dict(UP=0., DOWN=0.), owners=[])
roles = SimpleNamespace(side='UP', v12g_events=[dict(kind='DECIDE', t=1)], pid=lambda s: 1 if s == 'UP' else 2)
ident = lambda st, sd, p, q, r='ACTIVE': q
args = lambda: (snap, with_plan, ident, ident, lambda *a, **k: None, lambda *a: [])
Z.STATE.update(last_inv=None, zoneF=0., zoneW=0., keys=[], rows=[], orders=[], ended=None)
ops = Z.extend(fr(30000, dict(UP=300., DOWN=300.)), [], roles, *args())
ok('first_call_sets_baseline_no_order', ops == [] and Z.STATE['zoneF'] == 0)
ops = Z.extend(fr(31000, dict(UP=500., DOWN=300.)), [], roles, *args())
ok('mirror_other_side_active', len(ops) == 1 and ops[0]['side'] == 'DOWN' and ops[0]['route'] == 'ACTIVE' and ops[0]['qty'] == 200. and ops[0]['price'] == .49, ops)
f2 = fr(32000, dict(UP=500., DOWN=300.)); f2['ledger'].carriers[ops[0]['key']] = Car('SUBMITTED')
ok('one_in_flight', Z.extend(f2, [], roles, *args()) == [])
ops = Z.extend(fr(33000, dict(UP=500., DOWN=500.)), [], roles, *args())
ok('balanced_after_fill', ops == [] and Z.STATE['zoneW'] == 200.)
ops = Z.extend(fr(34000, dict(UP=800., DOWN=500.), mid=.75), [], roles, *args())
ok('out_of_zone_not_counted_no_order', ops == [] and Z.STATE['zoneF'] == 200.)
ops = Z.extend(fr(35000, dict(UP=900., DOWN=500.), mid=.60), [dict(kind='NEW', key='x', side='UP', route='ACTIVE')], roles, *args())
ok('existing_active_priority', len(ops) == 1)
ops = Z.extend(fr(291000, dict(UP=1000., DOWN=500.)), [], roles, *args())
ok('no_order_after_290', ops == [])
roles.v12g_events.append(dict(kind='FLIP', t=36000))
ok('no_order_after_first_flip', Z.extend(fr(37000, dict(UP=1200., DOWN=500.)), [], roles, *args()) == [] and Z.STATE['ended']['reason'] == 'FIRST_FLIP')
roles2 = SimpleNamespace(side='DOWN', v12g_events=[dict(kind='DECIDE', t=1)], pid=lambda s: 1 if s == 'UP' else 2)
Z.STATE.update(last_inv=None, zoneF=0., zoneW=0., keys=[], rows=[], orders=[], ended=None)
Z.extend(fr(30000, dict(UP=300., DOWN=300.), mid=.48), [], roles2, *args())
ops = Z.extend(fr(31000, dict(UP=300., DOWN=500.), mid=.48), [], roles2, *args())
ok('down_decided_zone_uses_1_minus_mid_and_buys_up', len(ops) == 1 and ops[0]['side'] == 'UP' and ops[0]['price'] == .49, ops)
Z.ENABLED = True; Z.ROUTE = 'PASSIVE'; Z.TICKET = 15.
roles3 = SimpleNamespace(side='UP', v12g_events=[dict(kind='DECIDE', t=1)], pid=lambda s: 1 if s == 'UP' else 2)
Z.STATE.update(last_inv=None, zoneF=0., zoneW=0., keys=[], rows=[], orders=[], ended=None)
Z.extend(fr(30000, dict(UP=300., DOWN=300.)), [], roles3, *args())
ops = Z.extend(fr(31000, dict(UP=400., DOWN=300.)), [], roles3, *args())
ok('passive_ticket_at_other_best_bid', len(ops) == 1 and ops[0]['route'] == 'PASSIVE' and ops[0]['qty'] == 15. and ops[0]['side'] == 'DOWN' and ops[0]['price'] == .47, ops)
k = ops[0]['key']
class Car2:
    def __init__(s, st, lim): s.state = st; s.limit = lim
f5 = fr(32000, dict(UP=400., DOWN=300.), mid=.50); f5['ledger'].carriers[k] = Car2('SUBMITTED', .47); f5['cancellable'] = {k: True}
ops = Z.extend(f5, [], roles3, *args())
ok('passive_requote_when_left_behind', ops == [dict(kind='CANCEL', key=k, origin='WHOLE_POLICY', reason='V67_ZONE_MIRROR_REQUOTE')], ops)
f6 = fr(33000, dict(UP=400., DOWN=300.), mid=.53); f6['ledger'].carriers[k] = Car2('SUBMITTED', .47); f6['cancellable'] = {k: True}
ok('passive_wait_when_not_behind', Z.extend(f6, [], roles3, *args()) == [])
f7 = fr(34000, dict(UP=400., DOWN=300.), mid=.53); f7['ledger'].carriers[k] = Car2('TERMINAL', .47)
ops = Z.extend(f7, [], roles3, *args())
ok('passive_repost_after_terminal', len(ops) == 1 and ops[0]['price'] == .46)
Z.STATE.update(last_inv=None, zoneF=0., zoneW=0., keys=[], rows=[], orders=[], ended=None)
Z.extend(fr(30000, dict(UP=300., DOWN=300.)), [], roles3, *args())
ok('passive_needs_full_ticket', Z.extend(fr(31000, dict(UP=310., DOWN=300.)), [], roles3, *args()) == [])
Z.ROUTE = 'ACTIVE'
Z.ENABLED = False
ok('off_inert', Z.extend(fr(31000, dict(UP=900., DOWN=300.)), ['o'], roles2, *args()) == ['o'])
print(json.dumps(dict(status='PASS', checks=len(checks), names=checks)))
