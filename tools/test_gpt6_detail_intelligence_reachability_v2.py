"""External reviewer fixtures; construction author does not execute these tests.

Standard-library only, no HFT imports. Run before the external market experiment.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eth_repair_modular.detail_intelligence_reachability_v2 import (
    PassiveIntent, bounded_frontier, pending_reachability, synthetic_keep_decision,
)


class ReachabilityFixtures(unittest.TestCase):
    def context(self):
        return PassiveIntent("DOWN", "SATELLITE_REPAIR", .24, 1 / .24,
                             .52, .53, ((2.272727272727273, .44),), (.52, .51), .01)

    def test_fills_empty_depth_hole_without_crossing_or_changing_intent(self):
        c = self.context()
        d = bounded_frontier(c)
        self.assertTrue(d["changed"])
        self.assertAlmostEqual(d["price"], .50)
        self.assertAlmostEqual(d["qty"] * d["price"], 1.0)
        self.assertEqual((d["intent"]["role"], d["intent"]["side"]), (c.role, c.side))

    def test_pair_ceiling_not_relaxed(self):
        c = replace(self.context(), side="UP", baseline_price=.45, baseline_qty=1 / .45,
                    bid=.50, ask=.53, used_prices=(),
                    opposite_lots=((1.7857142857142856, .56), (1.8867924528301885, .53),
                                   (1.923076923076923, .52)))
        d = bounded_frontier(c)
        self.assertAlmostEqual(d["price"], .46)
        self.assertLessEqual(d["price"], d["pairCeiling"])
        # Negative finite FIFO credit is telemetry, not a new hard veto.
        self.assertLess(d["selectedFifo"]["finiteFifoCredit"], 0)

    def test_occupied_prices_remain_reserved(self):
        d = bounded_frontier(replace(self.context(), used_prices=(.52, .51, .50, .49)))
        self.assertAlmostEqual(d["price"], .48)

    def test_probe_and_expand_unchanged(self):
        for role in ("PROBE_CORE", "SATELLITE_EXPAND"):
            d = bounded_frontier(replace(self.context(), role=role))
            self.assertFalse(d["changed"])
            self.assertAlmostEqual(d["price"], .24)

    def test_existing_frontier_unchanged(self):
        d = bounded_frontier(replace(self.context(), baseline_price=.50, baseline_qty=2.0))
        self.assertFalse(d["changed"])

    def test_crossed_book_falls_back_not_new_veto(self):
        d = bounded_frontier(replace(self.context(), ask=.50))
        self.assertFalse(d["changed"])
        self.assertEqual(d["price"], .24)

    def test_nonfinite_falls_back(self):
        d = bounded_frontier(replace(self.context(), bid=float("nan")))
        self.assertFalse(d["changed"])

    def test_side_complement_uses_same_token_price_grid(self):
        a = bounded_frontier(self.context())
        b = bounded_frontier(replace(self.context(), side="UP"))
        self.assertEqual(a["price"], b["price"])

    def test_pending_has_no_service_or_discount_authority(self):
        rows = [{"remainingQty": 2, "status": "NONE", "price": .5, "bid": .52},
                {"remainingQty": 3, "status": "NEW", "price": .24, "bid": .52},
                {"remainingQty": 1, "status": "NEW", "price": .52, "bid": .52,
                 "ttlCancelAttemptAt": 123}]
        r = pending_reachability(rows, 2)
        self.assertEqual(r["reservedQty"], 6)
        self.assertEqual(r["futureServiceQtyBounds"], [0, 6])
        self.assertEqual(r["confirmedPaymentFromPending"], 0)
        self.assertEqual(r["effectiveRedundancyDiscount"], 1)
        self.assertEqual(r["rejectedV1RawQuantityDiscount"], .25)
        self.assertEqual(set(r["qtyByClass"]), {"SUBMIT_IN_FLIGHT", "RESTING_BEHIND_BID", "CANCEL_UNCERTAIN"})

    def keep_args(self):
        return dict(synthetic=True, pair_ok=True, price=.50, frontier_price=.50,
                    age_ms=1000, ttl_ms=5000, cancel_pending=False)

    def test_keep_own_frontier_when_public_level_absent(self):
        self.assertTrue(synthetic_keep_decision(**self.keep_args())["suppressPublicMembershipCancel"])

    def test_no_keep_when_inherited_ttl_due_or_cancel_pending(self):
        for change in ({"age_ms": 5000}, {"cancel_pending": True}):
            self.assertFalse(synthetic_keep_decision(**{**self.keep_args(), **change})["suppressPublicMembershipCancel"])

    def test_no_keep_for_closer_replacement_or_invalid_pair_or_legacy(self):
        for change in ({"frontier_price": .51}, {"pair_ok": False}, {"synthetic": False},
                       {"frontier_price": None}):
            self.assertFalse(synthetic_keep_decision(**{**self.keep_args(), **change})["suppressPublicMembershipCancel"])


if __name__ == "__main__":
    unittest.main()
