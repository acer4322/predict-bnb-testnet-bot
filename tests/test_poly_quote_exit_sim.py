from __future__ import annotations

import sqlite3
from pathlib import Path

import predict_bot.poly_quote_canary_exit_sim as exit_sim_module
from predict_bot.poly_quote_canary_exit_sim import ExitSimulatedPolyQuoteCanary


def _event(*, phase: str, signal_ms: int = 1_000_000) -> dict[str, object]:
    return {
        "trade_id": 7,
        "strategy": "R_POLY_LEAD_EXIT",
        "phase": phase,
        "market_id": 12345,
        "side": "UP",
        "signal_at_ms": signal_ms,
        "queued_at_ms": signal_ms,
        "paper_price": 0.50 if phase == "ENTRY" else 0.42,
        "stake_usdt": 10.0,
        "shares": 20.0,
        "poly_up_mid": 0.60,
        "signal_binance_up_mid": 0.50,
        "poly_selected_mid": 0.60,
    }


def _canary(tmp_path: Path) -> ExitSimulatedPolyQuoteCanary:
    return ExitSimulatedPolyQuoteCanary(tmp_path / "cross_oracle.db")


def _seed_passing_entry(canary: ExitSimulatedPolyQuoteCanary) -> None:
    entry = _event(phase="ENTRY")
    assert canary._record_initial(entry)
    canary._update_attempt(
        7,
        "ENTRY",
        fee_rate_bps=200,
        quote_average_price=0.50,
        quote_amount_in_wei=str(10 * 10**18),
        quote_amount_out_wei=str(20 * 10**18),
        quote_rtt_ms=120.0,
        signal_to_quote_response_ms=180.0,
        quote_coverage_ratio=1.0,
        would_submit=1,
        status="PASS_SIMULATED_PLACE",
        quote_id_present=1,
    )


def test_exit_uses_visible_first_level_only_and_never_needs_sell_quote(tmp_path: Path) -> None:
    canary = _canary(tmp_path)
    _seed_passing_entry(canary)
    exit_event = _event(phase="EXIT")
    assert canary._record_initial(exit_event)
    canary._current_binance_state = lambda: {
        "market_id": 12345,
        "up_bid": 0.40,
        "up_bid_size": 6.0,
        "up_ask": 0.42,
        "down_bid": 0.58,
        "down_ask": 0.60,
    }

    canary._process_exit_simulation(exit_event)

    row = canary.db.execute(
        "SELECT * FROM poly_quote_canary_attempts WHERE trade_id=7 AND phase='EXIT'"
    ).fetchone()
    assert row is not None
    assert row["execution_mode"] == "SIMULATED_EXIT_NO_SELL_QUOTE"
    assert row["status"] == "EXIT_SIM_WAITING_SETTLEMENT"
    assert row["quote_id_present"] == 0
    assert row["quote_rtt_ms"] is None
    assert abs(float(row["sim_entry_shares"]) - 20.0) < 1e-9
    assert abs(float(row["sim_best_fill_shares"]) - 6.0) < 1e-9
    assert abs(float(row["sim_best_fill_ratio"]) - 0.30) < 1e-9
    assert abs(float(row["sim_best_unfilled_shares"]) - 14.0) < 1e-9
    canary.db.close()


def test_official_settlement_finalizes_best_visible_and_no_sell_scenarios(tmp_path: Path) -> None:
    sim_db = tmp_path / "simulation.db"
    sim = sqlite3.connect(sim_db)
    sim.execute(
        """CREATE TABLE market_settlements(
               market_id INTEGER, status TEXT, official_winner TEXT
           )"""
    )
    sim.execute(
        "INSERT INTO market_settlements VALUES (12345, 'OFFICIAL', 'DOWN')"
    )
    sim.commit()
    sim.close()
    exit_sim_module.SIM_DB_PATH = sim_db

    canary = _canary(tmp_path)
    _seed_passing_entry(canary)
    exit_event = _event(phase="EXIT")
    assert canary._record_initial(exit_event)
    canary._current_binance_state = lambda: {
        "market_id": 12345,
        "up_bid": 0.40,
        "up_bid_size": 6.0,
        "up_ask": 0.42,
        "down_bid": 0.58,
        "down_ask": 0.60,
    }
    canary._process_exit_simulation(exit_event)
    canary._finalize_exit_settlements()

    row = canary.db.execute(
        "SELECT * FROM poly_quote_canary_attempts WHERE trade_id=7 AND phase='EXIT'"
    ).fetchone()
    assert row is not None
    assert row["sim_settlement_status"] == "FINALIZED"
    assert row["sim_official_winner"] == "DOWN"
    # Best visible: 6 shares * 0.40 = 2.40, minus 2% estimated exit fee = 2.352.
    # Remaining 14 UP shares lose at settlement. Cost basis was 10 USDT.
    assert abs(float(row["sim_best_final_pnl_usdt"]) - (-7.648)) < 1e-9
    # No sell: all 20 UP shares lose, so the full 10 USDT input is lost.
    assert abs(float(row["sim_no_sell_final_pnl_usdt"]) - (-10.0)) < 1e-9
    canary.db.close()


def test_summary_excludes_legacy_sell_quote_reject_from_entry_quality(tmp_path: Path) -> None:
    canary = _canary(tmp_path)
    _seed_passing_entry(canary)
    exit_event = _event(phase="EXIT")
    assert canary._record_initial(exit_event)
    canary._update_attempt(
        7,
        "EXIT",
        status="QUOTE_REJECTED",
        reason="You have exceeded your available shares",
    )

    summary = canary._summary_for("R_POLY_LEAD_EXIT")
    assert summary["attempts"] == 1
    assert summary["completed"] == 1
    assert summary["wouldSubmit"] == 1
    assert summary["wouldSubmitRate"] == 1.0
    assert summary["quoteRejected"] == 0
    assert summary["legacySellQuoteRejectedExcluded"] == 1
    canary.db.close()
