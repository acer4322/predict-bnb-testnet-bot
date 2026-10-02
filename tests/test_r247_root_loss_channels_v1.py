import copy
import unittest

from tools.audit_r247_root_loss_channels_v1 import CELL, decompose


def row(fills, winner="UP"):
    qty = {"UP": 0.0, "DOWN": 0.0}
    cost = 0.0
    events = []
    for ordinal, (side, price, amount) in enumerate(fills):
        qty[side] += amount
        cost += price * amount
        events.append(dict(event="ROLE_FILL_SPLIT", t=ordinal, key=str(ordinal),
                           side=side, price=price, fillInc=amount, role="ECONOMIC_CORE"))
    return dict(marketId=1945866, cell=CELL, splitEvents=events, fillEvents=len(fills),
                submits=len(fills), floor=min(qty.values()) - cost, best=max(qty.values()) - cost,
                winnerPostHocOnly=winner, pnlDiagnosticOnly=qty[winner] - cost,
                r247ServiceCorrectnessPass=True, unauthorizedOverflowQty=0,
                repairQuotaExcessMax=0, totalRepairCreditValue=10)


class DecompositionTests(unittest.TestCase):
    def test_profitable_pair_is_not_credit(self):
        result = decompose(row([("UP", .4, 2), ("DOWN", .5, 2)]))
        self.assertAlmostEqual(result["limitPriceDiagnosticOnly"]["fifoPairedGrossPnl"], .2)
        self.assertEqual(result["residualQty"], 0)
        self.assertNotEqual(result["limitPriceDiagnosticOnly"]["fifoPairedGrossPnl"], result["repairCreditValue_NOT_profit"])

    def test_costly_pair_locks_loss(self):
        result = decompose(row([("UP", .7, 2), ("DOWN", .6, 2)]))
        self.assertAlmostEqual(result["limitPriceDiagnosticOnly"]["fifoNegativePairLoss"], -.6)
        self.assertAlmostEqual(result["grossBest"], -.6)

    def test_partial_pair_with_directional_residual(self):
        result = decompose(row([("UP", .4, 4), ("DOWN", .5, 2)]))
        self.assertAlmostEqual(result["limitPriceDiagnosticOnly"]["fifoPairedGrossPnl"], .2)
        self.assertAlmostEqual(result["limitPriceDiagnosticOnly"]["residualAcquisitionCost"], .8)
        self.assertAlmostEqual(result["grossFloor"], -.6)

    def test_crossing_and_late_generation_independent_inventory(self):
        result = decompose(row([("UP", .7, 1), ("DOWN", .2, 3), ("UP", .9, 1)]))
        self.assertEqual(result["weakSideCrossingFills"], 1)
        self.assertAlmostEqual(result["residualQty"], 1)
        self.assertAlmostEqual(result["limitPriceDiagnosticOnly"]["fifoPairedGrossPnl"], 0)

    def test_winner_only_changes_posthoc_residual_pnl(self):
        a = decompose(row([("UP", .4, 4), ("DOWN", .5, 2)], "UP"))
        b = decompose(row([("UP", .4, 4), ("DOWN", .5, 2)], "DOWN"))
        for key in ("grossFloor", "grossBest", "limitPriceDiagnosticOnly", "firstComposite", "conditionalResidualCostLower"):
            self.assertEqual(a[key], b[key])

    def test_missing_fill_fails(self):
        source = row([("UP", .4, 4), ("DOWN", .5, 2)])
        source["splitEvents"].pop()
        with self.assertRaises(ValueError):
            decompose(source)

    def test_locked_market_fails(self):
        source = row([("UP", .4, 4)])
        source["marketId"] = 1946468
        with self.assertRaises(ValueError):
            decompose(source)

    def test_bad_saved_accounting_fails(self):
        source = copy.deepcopy(row([("UP", .4, 4)]))
        source["best"] += .01
        with self.assertRaises(ValueError):
            decompose(source)

    def test_limit_annotation_requires_explicit_contract(self):
        source = row([("UP", .7, 2), ("DOWN", .6, 2)])
        for key in ("floor", "best", "pnlDiagnosticOnly"):
            source[key] += .2
        with self.assertRaises(ValueError):
            decompose(source)
        result = decompose(source, allow_limit_annotations=True)
        self.assertAlmostEqual(result["limitNotionalMinusImpliedNativeNotional"], .2)
        self.assertAlmostEqual(result["conditionalActualPairGainLower"], -.6)
        self.assertAlmostEqual(result["conditionalActualPairGainUpper"], -.4)

    def test_inferred_cost_cannot_hide_quantity_payoff_mismatch(self):
        source = row([("UP", .7, 2), ("DOWN", .6, 2)])
        source["best"] += .2
        with self.assertRaises(ValueError):
            decompose(source, allow_limit_annotations=True)


if __name__ == "__main__":
    unittest.main()
