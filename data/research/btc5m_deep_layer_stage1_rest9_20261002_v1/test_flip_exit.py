"""V60 host-only tests for flip_exit (no native)."""
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
P = Path(__file__).resolve().parent; sys.path.insert(0, str(P))
os.environ.pop('BTC5M_LAN_RESULT_DIR', None)
import importlib.util
leg = P.parent / 'v12g_fresh30_generalization_20260927_v45' / 'base' / 'hft244_pair_route_legality_v1.py'
spec = importlib.util.spec_from_file_location('hft244_pair_route_legality_v1', leg); L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
sys.modules['hft244_pair_route_legality_v1'] = L
import flip_exit as F
checks = []
def ok(n, c, i=None):
    assert c, (n, i); checks.append(n)
roles = SimpleNamespace(v12g_events=[dict(kind='DECIDE', t=1030000), dict(kind='FLIP', t=1100000)])
fr = lambda t: dict(t=t, start=1000000, end=1300000, index=0)
ok('off_never_active', not F.active(fr(1200000), 'OFF', roles))
ok('inactive_before_flip', not F.active(fr(1099999), 'ON', roles) and F.active(fr(1100000), 'ON', roles))
ok('no_flip_never_active', not F.active(fr(1200000), 'ON', SimpleNamespace(v12g_events=[dict(kind='DECIDE', t=1)])))
base = dict(quotes={'DOWN': {'ask': .70}, 'UP': {'ask': .31}}, book={'bids': {.30: 500.}, 'asks': {.31: 200.}})
f = dict(fr(1150000), **base)
op, row = F.plan_exit(f, {'UP': 0., 'DOWN': 0.}, {'UP': 900., 'DOWN': 600.}, False, lambda *a: [], [], .01, .01)
ok('short_side_bought_to_equalize', op == dict(side='DOWN', price=.70, qty=300.) and row['need'] == 300., (op, row))
op, row = F.plan_exit(f, {'UP': 0., 'DOWN': 0.}, {'UP': 1500., 'DOWN': 600.}, False, lambda *a: [], [], .01, .01)
ok('depth_limited', op['qty'] == 500.)
op, row = F.plan_exit(f, {'UP': 0., 'DOWN': 200.}, {'UP': 900., 'DOWN': 600.}, False, lambda *a: [], [], .01, .01)
ok('pending_short_counted', op['qty'] == 100.)
op, row = F.plan_exit(f, {'UP': 0., 'DOWN': 0.}, {'UP': 400., 'DOWN': 700.}, False, lambda *a: [], [], .01, .01)
ok('up_short_uses_up_ask', op == dict(side='UP', price=.31, qty=200.))
op, row = F.plan_exit(f, {'UP': 0., 'DOWN': 0.}, {'UP': 600.5, 'DOWN': 600.}, False, lambda *a: [], [], .01, .01)
ok('flat_below_minimum', op is None and row['reason'] == 'FLAT_OR_BELOW_MINIMUM')
op, row = F.plan_exit(f, {'UP': 0., 'DOWN': 0.}, {'UP': 900., 'DOWN': 600.}, True, lambda *a: [], [], .01, .01)
ok('one_exit_order_at_a_time', op is None and row['reason'] == 'EXIT_ORDER_IN_FLIGHT')
op, row = F.plan_exit(f, {'UP': 0., 'DOWN': 0.}, {'UP': 900., 'DOWN': 600.}, False, L.crossing_owners, [dict(key='UP_3', side='UP', price=.31)], .01, .01)
ok('wait_for_own_cross', op is None and row['reason'] == 'WAIT_OWN_CROSS')
op, row = F.plan_exit(dict(fr(1290000), **base), {'UP': 0., 'DOWN': 0.}, {'UP': 900., 'DOWN': 600.}, False, lambda *a: [], [], .01, .01)
ok('stop290_no_exit_order', op is None and row['reason'] == 'STOP290_OR_END')
import hard_stop as H
ok('gate_includes_exit_only_when_on', H.gate(fr(1200000)) == (F.MODE == 'ON' and F.active(fr(1200000))))
from test_hard_stop import run as hs
ok('hard_stop_tests_still_pass', hs()['status'] == 'PASS')
print(json.dumps(dict(status='PASS', checks=len(checks), names=checks)))
