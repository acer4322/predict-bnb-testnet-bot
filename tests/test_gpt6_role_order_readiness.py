"""Operator-run mock contracts. No HFT imports, tape, models or strategy PnL."""
from types import SimpleNamespace
import unittest

from tools.eth_repair_modular.role_order_readiness import CancelObservation, ReanchorIntentLedger
from tools.eth_repair_modular.persistent_execution_roles import CORE, SATELLITE, PersistentExecutionRoles
from tools.eth_repair_modular.readiness_aware_role_adapter import observe_cancel_readiness, fresh_satellite_handoff


def observation(*, ready=False, terminal=False):
    return CancelObservation(True, True, 'CANCELED' if terminal else 'NEW', True, ready, terminal)


class IntentContracts(unittest.TestCase):
    def setUp(self):
        self.ledger = ReanchorIntentLedger()
        self.ledger.request('s', 1, 'UP', 'root-s', 10)

    def test_elapsed_time_never_confers_readiness(self):
        for t in (11, 99999999):
            self.ledger.request('s', 1, 'UP', 'root-s', t)
            self.ledger.observe('s', observation())
            self.assertFalse(self.ledger.can_dispatch('s'))
        self.assertEqual(len(self.ledger.entries), 1)
        self.assertIsNone(self.ledger.entries['s'].accepted_at)

    def test_state_change_allows_exactly_one_dispatch(self):
        self.ledger.observe('s', observation())
        self.ledger.observe('s', observation(ready=True))
        self.assertTrue(self.ledger.can_dispatch('s'))
        self.ledger.record_dispatch('s', accepted=True, t=12)
        self.ledger.observe('s', observation(ready=True))
        self.assertFalse(self.ledger.can_dispatch('s'))
        with self.assertRaises(ValueError):
            self.ledger.record_dispatch('s', accepted=True, t=13)

    def test_unknown_outcome_is_not_optimistically_retried(self):
        self.ledger.observe('s', observation(ready=True))
        self.ledger.record_dispatch('s', accepted=False, t=12)
        self.ledger.observe('s', observation(ready=True))
        self.ledger.abandon_unsubmitted('s')
        self.assertEqual(self.ledger.entries['s'].state, 'CANCEL_OUTCOME_UNKNOWN')
        self.assertFalse(self.ledger.can_dispatch('s'))
        self.ledger.observe('s', observation(terminal=True))
        self.assertEqual(self.ledger.entries['s'].state, 'TERMINAL')

    def test_identity_cannot_migrate_and_terminal_cannot_resurrect(self):
        with self.assertRaises(ValueError):
            self.ledger.request('s', 2, 'UP', 'root-s', 12)
        self.ledger.observe('s', observation(terminal=True))
        self.ledger.request('s', 1, 'UP', 'root-s', 13)
        self.ledger.observe('s', observation(ready=True))
        self.assertFalse(self.ledger.can_dispatch('s'))

    def test_unsubmitted_intent_can_expire_without_physical_action(self):
        self.ledger.abandon_unsubmitted('s')
        self.assertNotIn('s', self.ledger.entries)


class SnapshotContracts(unittest.TestCase):
    def test_observer_has_no_cancel_side_effect(self):
        current = SimpleNamespace(cancellable=False)
        sim = SimpleNamespace(orders={'s': {'n': 1}}, carrierLedger={'s': {}},
            snap=lambda o: {'status': 'NEW'}, bt=SimpleNamespace(orders=lambda n: {1: current}))
        self.assertFalse(observe_cancel_readiness(sim, 's').ready)
        self.assertNotIn('cancelRequested', sim.carrierLedger['s'])
        current.cancellable = True
        self.assertTrue(observe_cancel_readiness(sim, 's').ready)
        sim.carrierLedger['s']['terminalConfirmed'] = True
        self.assertFalse(observe_cancel_readiness(sim, 's').ready)

    def test_snapshot_error_is_explicit_wait(self):
        def broken(order):
            raise RuntimeError('fixture')
        sim = SimpleNamespace(orders={'s': {'n': 1}}, carrierLedger={'s': {}}, snap=broken)
        seen = observe_cancel_readiness(sim, 's')
        self.assertFalse(seen.ready)
        self.assertEqual(seen.reason, 'SNAPSHOT_UNAVAILABLE')


class TerminalRetargetContracts(unittest.TestCase):
    def setUp(self):
        roles = PersistentExecutionRoles()
        roles.bind('core', 1, 'UP', CORE, lease_price=.4)
        roles.bind('old', 1, 'UP', SATELLITE)
        self.available, self.target, self.v16 = 2.0, .5, True
        self.floor_safe = True
        self.economic_calls = []
        def economic(t, side, qty, target):
            self.economic_calls.append((qty, target))
            return {'allow': self.v16}
        self.sim = SimpleNamespace(carrierLedger={'old': {'terminalConfirmed': True, 'submittedQty': 2.0, 'actualFilled': 0.0}},
            executionRoles=roles, _priority_target=lambda side: (self.target, self.target-.01, self.target+.01, SimpleNamespace(improved=True)),
            _parent_debt_now=lambda pid: 5.0, _sync_parent_occupancy=lambda: None,
            parentExecutionOccupancy=SimpleNamespace(available=lambda pid, debt: self.available),
            _current_payoffs=lambda: {'floor': -2.0},
            _floor_after_buys=lambda side, plans: (-1.0, [-1.0]) if self.floor_safe else (-3.0, [-3.0]), _econ=economic)
        self.meta = {'key': 'old', 'parentId': 1, 'side': 'UP', 'hybridFanoutApprovedPrice': .6,
                     'hybridFanoutApprovedQty': 2.0}
        self.rows = [{'key': 'core', 'parentId': 1, 'side': 'UP', 'price': .4, 'remaining': 2.0}]

    def propose(self):
        return fresh_satellite_handoff(self.sim, None, 20, self.meta, self.rows)

    def test_terminal_is_mandatory(self):
        self.sim.carrierLedger['old']['terminalConfirmed'] = False
        with self.assertRaises(ValueError):
            self.propose()

    def test_current_cheaper_quote_coalesces_without_qty_uplift(self):
        proposal, event = self.propose()
        self.assertEqual(event['decision'], 'FRESH_SATELLITE_PROPOSAL')
        self.assertEqual(proposal['hybridFanoutApprovedPrice'], .5)
        self.assertLessEqual(proposal['hybridFanoutApprovedQty'], self.meta['hybridFanoutApprovedQty'])
        self.assertEqual(self.meta['hybridFanoutApprovedPrice'], .6)

    def test_higher_quote_requires_fresh_v16(self):
        self.target, self.v16 = .7, False
        proposal, event = self.propose()
        self.assertEqual(event['decision'], 'RETAIN_ORIGINAL_UPWARD_V16_BLOCK')
        self.assertEqual(proposal, self.meta)
        self.assertEqual(len(self.economic_calls), 1)
        self.v16 = True
        proposal, event = self.propose()
        self.assertEqual(event['decision'], 'FRESH_SATELLITE_PROPOSAL')
        self.assertAlmostEqual(proposal['hybridFanoutApprovedQty'], 1/.7)

    def test_partial_fill_or_quota_shortage_cannot_create_new_tranche(self):
        self.sim.carrierLedger['old']['actualFilled'] = 1.0
        self.assertEqual(self.propose()[1]['decision'], 'RETAIN_ORIGINAL_LEGAL_QUOTA_OR_FLOOR_BLOCK')
        self.sim.carrierLedger['old']['actualFilled'] = 0.0
        self.available = 1.0
        self.assertEqual(self.propose()[1]['decision'], 'RETAIN_ORIGINAL_LEGAL_QUOTA_OR_FLOOR_BLOCK')

    def test_floor_or_core_loss_blocks_retarget(self):
        self.floor_safe = False
        self.assertEqual(self.propose()[0], self.meta)
        self.rows.clear()
        self.assertEqual(self.propose()[1]['decision'], 'RETAIN_ORIGINAL_NO_LIVE_CORE')


if __name__ == '__main__':
    unittest.main()
