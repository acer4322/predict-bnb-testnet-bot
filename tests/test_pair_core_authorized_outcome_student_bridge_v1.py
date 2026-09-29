"""Synthetic caller authority / canonical receipt contract tests, never HFT."""
from collections import deque
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from tools.pair_core_economic_grant_ledger_v1 import Grant, EconomicGrantLedger
from tools.minimal_student_open_funding_v1 import OpenFundingLedger, OpenFundingProfile
from tools.minimal_student_system_plan_v1 import PlanAction, PlanRejected
from tools.minimal_student_training_world_v2 import envelope, validate_envelope
from tools.pair_core_authorized_outcome_preview_v1 import InitialPortfolio, EndpointAuthority
from tools.pair_core_authorized_outcome_student_bridge_v1 import (
    AuthorizedOutcomeStudentGateway, make_authorized_training_class,
)


POLICY = SimpleNamespace(policy_id='EXPLICIT_TEST_GOALS', continuation_id='SAME_TWO_GOALS',
                         provenance='SYNTHETIC_NOT_TARGET_OR_NATIVE_EXECUTION')


def fixture(asset='BTC', capacity=32, bounds=None):
    ledger = OpenFundingLedger(OpenFundingProfile(asset=asset, max_live_owners=capacity))
    ledger.issue(Grant(1, 'repair-goal', 'DOWN', 24., 0., 0., 'CALLER_REPAIR'))
    ledger.issue(Grant(2, 'add-goal', 'UP', 0., 0., 0., 'CALLER_ADD'))
    return AuthorizedOutcomeStudentGateway(ledger, policy_id=POLICY.policy_id,
        initial=InitialPortfolio(120., 80., 85.),
        bounds=bounds or EndpointAuthority(0., -6., 'CALLER_FIXTURE'))


def repair(key='DOWN_1', qty=24., price=.19, fee=0.):
    return PlanAction('NEW', key, 1, 'PASSIVE', price, qty, fee)


def add(key='UP_2', qty=18., price=.62):
    return PlanAction('NEW', key, 2, 'PASSIVE', price, qty)


def plan(g, *actions, decision='test'):
    maintained = {a.key for a in actions}
    keep = tuple(PlanAction('KEEP', k) for k, c in g.ledger.carriers.items()
                 if c.state != 'TERMINAL' and k not in maintained)
    return g.propose(decision, (*keep, *actions), POLICY.continuation_id)


def commit(g, p, now=200000):
    return g.commit(p, now_ms=now, market_end_ms=300000)


def sent_repair(g, status='SENT'):
    commit(g, plan(g, repair(), decision='repair'))
    g.record_send('DOWN_1', status, evidence='INJECTED_TRANSPORT_TEST')


def receipt(g, qty, terminal=False):
    return g.receipt('DOWN_1', filled=qty, payment=qty*.19, terminal=terminal,
                     evidence='INJECTED_CUMULATIVE_RECEIPT_NOT_NATIVE_RUN')


class AuthorizedStudentBridgeTests(unittest.TestCase):
    def test_joint_all_fill_good_but_add_first_bad_atomic_rejection(self):
        g = fixture(); original_ledger = g.ledger
        before = (g.snapshot_id(), deepcopy(g.events), set(g.committed_ids))
        p = plan(g, repair(), add())
        r = g.preview_plan(p, now_ms=200000, market_end_ms=300000)
        self.assertFalse(r['accepted'])
        self.assertAlmostEqual(r['outcome']['projection']['conditional_all_filled_at_limits']['DOWN'], 3.28)
        self.assertAlmostEqual(r['outcome']['projection']['coordinate_worst']['DOWN'], -16.16)
        with self.assertRaisesRegex(PlanRejected, 'ENDPOINT_AUTHORITY'): commit(g, p)
        self.assertEqual((g.snapshot_id(), g.events, g.committed_ids), before)
        self.assertIs(g.ledger, original_ledger)

    def test_partial_repair_unlocks_add_without_finishing_goal(self):
        g = fixture(); sent_repair(g); receipt(g, 15.)
        commit(g, plan(g, add(), decision='add'))
        f = g.outcome_feedback()
        self.assertAlmostEqual(f['projection']['coordinate_worst']['DOWN'], -4.01)
        self.assertEqual(g.ledger.account(1)['repair_remaining'], 9.)
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 9.)
        self.assertEqual(set(g.ledger.grants), {1, 2})
        self.assertEqual(g.active_continuation, POLICY.continuation_id)

    def test_same_progress_new_price_can_reject(self):
        g = fixture(); sent_repair(g); receipt(g, 15.)
        r = g.preview_plan(plan(g, add(price=.8)), now_ms=200000, market_end_ms=300000)
        self.assertEqual(r['status'], 'DECLARED_ENDPOINT_AUTHORITY_EXCEEDED')
        self.assertAlmostEqual(r['outcome']['projection']['coordinate_worst']['DOWN'], -7.25)

    def test_zero_and_duplicate_receipts_add_no_protection(self):
        g = fixture(); sent_repair(g); receipt(g, 0.)
        self.assertFalse(g.preview_plan(plan(g, add()), now_ms=200000, market_end_ms=300000)['accepted'])
        receipt(g, 15.); before = g.own_state(); receipt(g, 15.)
        self.assertEqual(g.own_state(), before)

    def test_cancel_pending_keeps_risk_and_partial_late_fill(self):
        g = fixture(); sent_repair(g)
        commit(g, plan(g, PlanAction('CANCEL', 'DOWN_1'), decision='cancel'))
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 24.)
        receipt(g, 15.)
        self.assertEqual(g.ledger.carriers['DOWN_1'].state, 'CANCEL_PENDING')
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 9.)
        receipt(g, 15., terminal=True)
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 0.)
        self.assertEqual(g.ledger.account(1)['repair_remaining'], 9.)

    def test_zero_terminal_releases_claim_not_repair_responsibility(self):
        g = fixture(); sent_repair(g); receipt(g, 0., terminal=True)
        self.assertEqual(g.ledger.account(1)['reserved_cash'], 0.)
        self.assertEqual(g.ledger.account(1)['repair_remaining'], 24.)

    def test_after_terminal_new_execution_is_ordering_fault(self):
        g = fixture(); sent_repair(g); receipt(g, 15., terminal=True)
        before = g.snapshot_id()
        with self.assertRaisesRegex(ValueError, 'after reconciled terminal'): receipt(g, 16.)
        self.assertEqual(g.snapshot_id(), before)

    def test_partial_below_new_minimum_is_accounted(self):
        g = fixture(); sent_repair(g); receipt(g, .5)
        self.assertEqual(g.own_state()['inventory']['DOWN'], 80.5)

    def test_unknown_send_keeps_full_claim_until_canonical_terminal(self):
        g = fixture(); sent_repair(g, 'UNKNOWN')
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 24.)
        receipt(g, 0., terminal=True)
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 0.)

    def test_definite_not_sent_releases_no_inventory_credit(self):
        g = fixture(); commit(g, plan(g, repair()))
        g.record_send('DOWN_1', 'NOT_SENT', evidence='DEFINITE_PRE_SEND_TEST_FAILURE')
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 0.)
        self.assertEqual(g.own_state()['inventory'], {'UP': 120., 'DOWN': 80.})

    def test_receipt_invalidates_prepared_plan(self):
        g = fixture(); sent_repair(g); old = plan(g, decision='old')
        receipt(g, 1.)
        with self.assertRaisesRegex(PlanRejected, 'STALE_OWN'): commit(g, old)

    def test_external_authority_invalidates_prepared_plan(self):
        g = fixture(); old = plan(g, repair())
        g.replace_endpoint_authority(EndpointAuthority(0., -20., 'CALLER_REVISION'))
        with self.assertRaisesRegex(PlanRejected, 'STALE_OWN'): commit(g, old)
        commit(g, plan(g, repair(), add()))

    def test_cancel_and_receipts_remain_available_outside_tightened_bounds(self):
        g = fixture(); sent_repair(g)
        g.replace_endpoint_authority(EndpointAuthority(100., 100., 'TIGHTENED_TEST'))
        e = commit(g, plan(g, PlanAction('CANCEL', 'DOWN_1'), decision='cancel'))
        self.assertEqual(e['outcome_admission_status'], 'MAINTENANCE_ONLY_OUTSIDE_AUTHORITY')
        receipt(g, 15., terminal=True)
        self.assertFalse(g.outcome_feedback()['within_authority'])
        self.assertEqual(g.ledger.carriers['DOWN_1'].state, 'TERMINAL')

    def test_rejected_cancel_plus_new_does_not_partly_cancel(self):
        g = fixture(); sent_repair(g); before = g.snapshot_id()
        with self.assertRaises(PlanRejected):
            commit(g, plan(g, PlanAction('CANCEL', 'DOWN_1'), add(), decision='rejected'))
        self.assertEqual(g.snapshot_id(), before)
        self.assertEqual(g.ledger.carriers['DOWN_1'].state, 'SUBMITTED')
        self.assertNotIn('rejected', g.committed_ids)

    def test_nonbinding_funding_over_100_and_110_is_not_capped(self):
        g = fixture(bounds=EndpointAuthority(-1000., -1000., 'EXPLICIT_LOOSE_TEST'))
        commit(g, plan(g, add(qty=400., price=.62)))
        f = g.ledger.funding_demand()
        self.assertIsNone(f['capital_cap'])
        self.assertAlmostEqual(f['current_cash_requirement'], 248.)
        self.assertEqual(g.ledger.carriers['UP_2'].qty, 400.)
        json.dumps(g.own_state(), allow_nan=False)

    def test_passive_five_allowed_but_declared_owner_limit_is_censor(self):
        for cap in (4, 32):
            g = fixture(capacity=cap, bounds=EndpointAuthority(-1000., -1000., 'LOOSE'))
            actions = [add(key=f'UP_{i}', price=.1) for i in range(1, 6)]
            if cap == 4:
                with self.assertRaisesRegex(PlanRejected, 'RESOURCE_CENSOR'): commit(g, plan(g, *actions))
                self.assertFalse(g.ledger.carriers)
            else:
                commit(g, plan(g, *actions)); self.assertEqual(len(g.ledger.carriers), 5)

    def test_native_active_cannot_be_declared_supported_by_ledger(self):
        g = fixture()
        with self.assertRaisesRegex(PlanRejected, 'NATIVE_ACTIVE_UNSUPPORTED_NOT_HOLD'):
            commit(g, plan(g, replace(repair(), route='ACTIVE')))
        self.assertFalse(g.ledger.carriers)

    def test_old_finite_ledger_cannot_replace_effective_profile(self):
        with self.assertRaisesRegex(ValueError, 'NONBINDING'):
            AuthorizedOutcomeStudentGateway(EconomicGrantLedger(45.), policy_id='X',
                initial=InitialPortfolio(0., 0., 0.), bounds=EndpointAuthority(-10., -10., 'X'))

    def test_native_numeric_profile_drift_is_not_silent(self):
        g = fixture(); g.ledger.profile = replace(g.ledger.profile, tick=.001)
        with self.assertRaisesRegex(PlanRejected, 'NUMERICAL_PROFILE'): commit(g, plan(g, repair()))

    def test_btc_eth_corrected_minima_and_actual_market_end(self):
        for asset, minimum in [('BTC', 18.), ('ETH', 12.)]:
            g = fixture(asset)
            with self.assertRaises(PlanRejected): commit(g, plan(g, repair(qty=minimum-1)))
            commit(g, plan(g, repair(qty=minimum)), now=299999)
            fresh = fixture(asset)
            with self.assertRaises(PlanRejected): commit(fresh, plan(fresh, repair()), now=300000)

    def test_fee_cap_in_unresolved_risk(self):
        g = fixture(); commit(g, plan(g, repair(fee=.5)))
        f = g.outcome_feedback()
        self.assertAlmostEqual(f['projection']['coordinate_worst']['DOWN'], -5.5)
        self.assertAlmostEqual(f['projection']['coordinate_worst']['UP'], 29.94)

    def test_missing_maintenance_and_unknown_receipts_reject(self):
        g = fixture(); sent_repair(g)
        p = g.propose('missing', (), POLICY.continuation_id)
        with self.assertRaisesRegex(PlanRejected, 'WHOLE_LIVE'): commit(g, p)
        with self.assertRaises(ValueError):
            g.receipt('ghost', filled=1., payment=.1, evidence='UNKNOWN_TEST')

    def test_envelope_binds_quote_clock_and_complete_operation(self):
        g = fixture()
        f = dict(t=200000, start=0, end=300000, index=1,
            world_profile=asdict(g.ledger.profile), book={'bids': {.62: 100.}, 'asks': {.64: 100.}},
            previous_book={'bids': {}, 'asks': {}}, quotes={},
            own_view=dict(inv={'UP':120., 'DOWN':80.}, cost=85., un={'UP':deque(), 'DOWN':deque()},
                orders={}, slot_key={}, key_role={}, n=1, max_slots=32),
            snapshots={}, cancellable={}, ledger=deepcopy(g.ledger), gateway_state_id=g.snapshot_id())
        env = envelope(f, POLICY, [dict(kind='NEW', key='DOWN_1', parent_id=1,
            side='DOWN', price=.19, qty=24., role='EXPLICIT_REPAIR')])
        self.assertTrue(validate_envelope(f, env, POLICY.policy_id, POLICY.continuation_id))
        for field in ('quote', 'clock'):
            changed = deepcopy(f)
            if field == 'quote': changed['book']['bids'][.62] = 99.
            else: changed['t'] += 1
            with self.assertRaisesRegex(PlanRejected, 'STALE_WORLD'):
                validate_envelope(changed, env, POLICY.policy_id, POLICY.continuation_id)
        commit(g, env.plan)

    def test_native_factory_is_opt_in_flat_and_preserves_receipt_method(self):
        class BoundaryStub:
            def __init__(self):
                self.inv = {'UP':0., 'DOWN':0.}; self.cost = 0.
                self.payload = {'market': {'window_end_ms':300000}}
        cls = make_authorized_training_class(BoundaryStub, object)
        ledger = fixture().ledger
        student = cls(producer=POLICY, ledger=ledger, trace=None,
            verified_window=(0,300000), window_source_sha256='a'*64,
            endpoint_authority=EndpointAuthority(-10.,-10.,'EXPLICIT_FLAT_TEST'))
        self.assertIsInstance(student.gateway, AuthorizedOutcomeStudentGateway)
        self.assertEqual(student.gateway.initial_portfolio(), InitialPortfolio(0.,0.,0.))
        self.assertNotIn('process', cls.__dict__)
        self.assertNotIn('submit', cls.__dict__)
        self.assertNotIn('consume', cls.__dict__)
        with patch.object(cls.__mro__[1], 'current_frame', return_value={'own_view':{}}):
            frame = student.current_frame()
        self.assertEqual(frame['own_view']['authorized_outcome'], frame['authorized_outcome'])
        self.assertIsNot(frame['own_view']['authorized_outcome'], frame['authorized_outcome'])
        self.assertEqual(frame['own_view']['authorized_outcome']['authority']['reference'], 'EXPLICIT_FLAT_TEST')

    def test_inherited_process_reconciles_injected_partial_cancel_terminal(self):
        # Deterministic boundary double, not a matching engine or native fill run.
        class ReceiptBoundary:
            def __init__(self):
                self.inv = {'UP':0., 'DOWN':0.}; self.cost = 0.
                self.payload = {'market': {'window_end_ms':300000}}
                self.orders = {}; self.injected = []; self.status = 'NEW'; self.cum = 0.
            def process(self, t):
                self._receipt_delta_rows = self.injected; self.injected = []
                for r in self._receipt_delta_rows:
                    self.inv['DOWN'] += r['qty']
                    self.cost += r['qty'] * r['contractPrice'] + r['fee']
                    self.cum = r['cumulative_qty']
            def snap(self, order):
                return {'status':self.status, 'cumExecQty':self.cum}
        captured = []
        trace = SimpleNamespace(now=0, process=lambda s,t: captured.append(s.gateway.own_state()))
        cls = make_authorized_training_class(ReceiptBoundary, object)
        student = cls(producer=POLICY, ledger=fixture().ledger, trace=trace,
            verified_window=(0,300000), window_source_sha256='a'*64,
            endpoint_authority=EndpointAuthority(-10.,-10.,'FLAT_CALLER_TEST'))
        g = student.gateway
        self.assertFalse(g.preview_plan(plan(g, repair(), add()), now_ms=1, market_end_ms=300000)['accepted'])
        sent_repair(g); student.orders['DOWN_1'] = {'n':1}
        student.process(1)  # no injected receipt, no acquired protection
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 24.)
        commit(g, plan(g, PlanAction('CANCEL','DOWN_1'), decision='cancel'))
        student.injected = [dict(key='DOWN_1', qty=15., contractPrice=.19,
                                  fee=0., cumulative_qty=15., sequence=1)]
        student.process(2)  # authoritative partial after cancel request
        self.assertEqual(g.ledger.carriers['DOWN_1'].state, 'CANCEL_PENDING')
        self.assertTrue(g.preview_plan(plan(g, add()), now_ms=3, market_end_ms=300000)['accepted'])
        student.process(3)  # no second credit on an unchanged native snapshot
        self.assertEqual(g.own_state()['inventory']['DOWN'], 15.)
        student.status = 'CANCELED'; student.process(4)
        self.assertEqual(g.ledger.account(1)['reserved_qty'], 0.)
        self.assertEqual(g.ledger.account(1)['repair_remaining'], 9.)
        self.assertEqual(captured[-1]['inventory'], student.inv)
        self.assertAlmostEqual(captured[-1]['cost'], student.cost)


if __name__ == '__main__':
    unittest.main()
