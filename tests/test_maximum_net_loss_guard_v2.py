from __future__ import annotations

import time
from pathlib import Path

from predict_bot import live_trading


def _engine(path: Path) -> live_trading.LiveM0WEngine:
    return live_trading.LiveM0WEngine(
        api_key=None,
        api_secret=None,
        configured_enabled=False,
        credential_source="test",
        current_market=lambda: None,
        db_path=path,
        auto_redeem_enabled=False,
    )


def _insert_loss(ledger: live_trading.LiveLedger, pnl_usdt: float) -> None:
    time.sleep(0.002)
    now = live_trading.utc_iso()
    with ledger.lock:
        ledger.db.execute(
            """INSERT INTO live_strategy_settlements(
                   order_local_id, market_id, position_status, result,
                   cost_usdt, payout_usdt, pnl_usdt, roi_pct,
                   settled_at, updated_at
               ) VALUES (?, ?, 'SETTLED', 'LOSS', ?, ?, ?, ?, ?, ?)""",
            (
                999001,
                999001,
                10.0,
                max(0.0, 10.0 + pnl_usdt),
                pnl_usdt,
                pnl_usdt / 10.0 * 100.0,
                now,
                now,
            ),
        )
        ledger.db.commit()


def test_numeric_reduction_threshold_and_multiplier_scale_new_stake(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "live.db")
    try:
        state = engine.update_live_rules(
            {
                "strategies": ["R_MICROPRICE"],
                "strategyStakesUsdt": [2.0],
                "maximumNetLossReduceEnabled": True,
                "maximumNetLossReduceUsdt": 3.0,
                "maximumNetLossReduceMultiplierPct": 50.0,
                "maximumNetLossGuardEnabled": True,
                "maximumNetLossUsdt": 10.0,
            }
        )
        guard = state["maximumNetLossGuard"]
        assert guard["reductionEnabled"] is True
        assert guard["reductionThresholdUsdt"] == 3.0
        assert guard["reductionMultiplierPct"] == 50.0
        assert guard["phase"] == "NORMAL"

        _insert_loss(engine.ledger, -3.5)
        reduced = engine.evaluate_maximum_net_loss_guard("test loss")
        assert reduced["reductionTripped"] is True
        assert reduced["tripped"] is False
        assert reduced["phase"] == "REDUCED"
        assert reduced["effectiveStakeMultiplier"] == 0.5

        plan = live_trading.live_strategy_execution_plan(
            engine.live_rules,
            "R_MICROPRICE",
        )
        assert float(plan["initialStakeUsdt"]) == 1.0
        assert float(plan["totalCapUsdt"]) == 1.0
        assert plan["lossGuardReductionMultiplier"] == 0.5
    finally:
        live_trading._maximum_net_loss_reduction_multiplier = 1.0


def test_dashboard_keeps_all_three_tiered_values_numeric_and_editable() -> None:
    dashboard = (
        Path(__file__).resolve().parents[1]
        / "dashboard"
        / "app"
        / "maximum-net-loss-guard-dashboard.tsx"
    ).read_text(encoding="utf-8")

    assert "減額門檻（USDT）" in dashboard
    assert "減額後比例（%）" in dashboard
    assert "停止門檻（USDT）" in dashboard
    assert 'type="number" min="0.01" max="1000000" step="0.01" value={reduceThresholdDraft}' in dashboard
    assert 'type="number" min="1" max="100" step="1" value={reduceMultiplierDraft}' in dashboard
    assert 'type="number" min="0.01" max="1000000" step="0.01" value={stopThresholdDraft}' in dashboard
    assert 'disabled={saving || resetting}' in dashboard
    assert "disabled={saving || resetting || !reduceEnabledDraft}" not in dashboard
