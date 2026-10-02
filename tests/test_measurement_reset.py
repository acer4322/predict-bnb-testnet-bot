import json
from pathlib import Path
from types import SimpleNamespace
import threading
import urllib.error
import urllib.request

import pytest

import predict_bot.server as server_module
from predict_bot.server import Handler, Store, SUPPORTED_STRATEGIES


def collector_stub():
    return SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )


def open_trade(store: Store, strategy: str, market_id: int) -> None:
    store.open_trade(
        strategy=strategy,
        topic_id=10_000 + market_id,
        market_id=market_id,
        side="UP",
        entry=0.90,
        target=None,
        stake=9.0,
        fee_rate_bps=200,
        note="measurement reset test",
    )


def post_json(url: str, payload: object):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.load(response)


def test_reset_is_strategy_local_and_never_modifies_trade_history(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    open_trade(store, "A", 701)
    store.settle_market(701, winner="UP", official=True)
    open_trade(store, "B", 702)
    store.settle_market(702, winner="UP", official=True)
    open_trade(store, "A", 703)  # Still open when A's measurement is reset.

    trades_before = [
        dict(row) for row in store.db.execute("SELECT * FROM trades ORDER BY id").fetchall()
    ]
    reset = store.reset_strategy_measurement("A")
    assert store.has_trade("A", 703)  # Resetting metrics must not permit re-entry.
    trades_after = [
        dict(row) for row in store.db.execute("SELECT * FROM trades ORDER BY id").fetchall()
    ]
    assert trades_after == trades_before

    summaries = store.dashboard(collector_stub())["summaries"]
    assert summaries["A"] == {
        "trades": 0,
        "open": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl": 0,
        "resetAt": reset["resetAt"],
        "cutoffTradeId": reset["cutoffTradeId"],
        "carriedOpen": 1,
        "totalOpen": 1,
    }
    assert summaries["B"]["trades"] == 1
    assert summaries["B"]["wins"] == 1
    assert summaries["B"]["resetAt"] is None


def test_post_reset_trade_is_counted_but_pre_reset_open_result_stays_excluded(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    open_trade(store, "A", 711)
    store.settle_market(711, winner="UP", official=True)  # Historical closed result.
    open_trade(store, "A", 712)  # Pre-reset open position.
    store.reset_strategy_measurement("A")
    open_trade(store, "A", 713)  # Post-reset open position.

    summary = store.dashboard(collector_stub())["summaries"]["A"]
    assert summary["trades"] == 1
    assert summary["open"] == 1
    assert summary["wins"] == 0
    assert summary["carriedOpen"] == 1
    assert summary["totalOpen"] == 2

    # The cutoff is an immutable trade-ID cohort: closing a carried position
    # removes its exposure but never leaks its result into the new measurement.
    store.settle_market(712, winner="UP", official=True)
    summary = store.dashboard(collector_stub())["summaries"]["A"]
    assert summary["trades"] == 1
    assert summary["open"] == 1
    assert summary["wins"] == 0
    assert summary["losses"] == 0
    assert summary["realized_pnl"] == 0
    assert summary["carriedOpen"] == 0
    assert summary["totalOpen"] == 1

    store.settle_market(713, winner="UP", official=True)
    summary = store.dashboard(collector_stub())["summaries"]["A"]
    measured = store.db.execute(
        "SELECT pnl FROM trades WHERE strategy='A' AND market_id=713"
    ).fetchone()
    assert summary["trades"] == 1
    assert summary["open"] == 0
    assert summary["wins"] == 1
    assert summary["realized_pnl"] == pytest.approx(measured["pnl"])
    assert summary["totalOpen"] == 0


def test_reset_baseline_persists_when_store_reopens(tmp_path: Path):
    path = tmp_path / "sim.db"
    store = Store(path)
    reset = store.reset_strategy_measurement("E2")

    reopened = Store(path)
    summary = reopened.dashboard(collector_stub())["summaries"]["E2"]
    row = reopened.db.execute(
        """SELECT cutoff_trade_id, reset_at FROM strategy_measurement_resets
           WHERE strategy='E2' ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    assert row["reset_at"] == reset["resetAt"]
    assert row["cutoff_trade_id"] == reset["cutoffTradeId"]
    assert summary["resetAt"] == reset["resetAt"]
    assert summary["cutoffTradeId"] == reset["cutoffTradeId"]


def test_reset_accepts_all_supported_strategies(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    assert len(SUPPORTED_STRATEGIES) == len(set(SUPPORTED_STRATEGIES))
    assert {
        "R_FUTURES_LEAD_SIGNAL_100",
        "R_FUTURES_LEAD_MIN_ENTRY_020",
    } <= set(SUPPORTED_STRATEGIES)
    for strategy in SUPPORTED_STRATEGIES:
        assert store.reset_strategy_measurement(strategy)
    assert store.db.execute(
        "SELECT COUNT(*) FROM strategy_measurement_resets"
    ).fetchone()[0] == len(SUPPORTED_STRATEGIES)

    second_a_reset = store.reset_strategy_measurement("A")
    assert store.db.execute(
        "SELECT COUNT(*) FROM strategy_measurement_resets"
    ).fetchone()[0] == len(SUPPORTED_STRATEGIES) + 1
    assert store.dashboard(collector_stub())["summaries"]["A"]["resetAt"] == (
        second_a_reset["resetAt"]
    )

    with pytest.raises(ValueError, match="unsupported strategy"):
        store.reset_strategy_measurement("UNKNOWN")
    assert store.db.execute(
        "SELECT COUNT(*) FROM strategy_measurement_resets"
    ).fetchone()[0] == len(SUPPORTED_STRATEGIES) + 1


def test_m0_summary_reports_current_and_average_win_loss_streaks(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    for market_id, pnl in enumerate((1.0, 2.0, -1.0, 0.0, -2.0, 3.0), 1):
        store.open_trade(
            strategy="M0",
            topic_id=market_id,
            market_id=market_id,
            side="UP",
            entry=0.5,
            target=None,
            stake=10.0,
            fee_rate_bps=200,
            note="streak fixture",
        )
        store.db.execute(
            """UPDATE trades SET status='SETTLED_WIN', pnl=?, closed_at=?
               WHERE strategy='M0' AND market_id=?""",
            (pnl, "2026-07-18T00:00:00+00:00", market_id),
        )
    store.db.commit()

    summary = store.dashboard(collector_stub())["summaries"]["M0"]
    assert summary["currentWinStreak"] == 1
    assert summary["averageWinStreak"] == pytest.approx(1.5)
    assert summary["currentLossStreak"] == 0
    assert summary["averageLossStreak"] == pytest.approx(3.0)
    assert summary["averageWinLossCycleStreak"] == pytest.approx(1.0)

    store.reset_strategy_measurement("M0")
    reset_summary = store.dashboard(collector_stub())["summaries"]["M0"]
    assert reset_summary["currentWinStreak"] == 0
    assert reset_summary["averageWinStreak"] == 0
    assert reset_summary["currentLossStreak"] == 0
    assert reset_summary["averageLossStreak"] == 0
    assert reset_summary["averageWinLossCycleStreak"] == 0


def test_m0_win_loss_cycle_average_matches_projected_m0w_loss_runs(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    # W,L,W,L,W,L creates three consecutive projected M0W losses. W,W then
    # breaks that run with a projected win, and the final L creates a run of 1.
    pnls = (1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0)
    for market_id, pnl in enumerate(pnls, 1):
        store.open_trade(
            strategy="M0",
            topic_id=market_id,
            market_id=market_id,
            side="UP",
            entry=0.5,
            target=None,
            stake=10.0,
            fee_rate_bps=200,
            note="win-loss cycle fixture",
        )
        store.db.execute(
            """UPDATE trades SET status=?, pnl=?, closed_at=?
               WHERE strategy='M0' AND market_id=?""",
            (
                "SETTLED_WIN" if pnl > 0 else "SETTLED_LOSS",
                pnl,
                "2026-07-18T00:00:00+00:00",
                market_id,
            ),
        )
    store.db.commit()

    summary = store.dashboard(collector_stub())["summaries"]["M0"]
    assert summary["averageWinLossCycleStreak"] == pytest.approx(2.0)


def test_m0_hourly_win_rate_uses_taipei_time_and_settled_cohort(tmp_path: Path):
    store = Store(tmp_path / "sim.db")

    def add_result(
        market_id: int,
        opened_at: str,
        *,
        winner: bool | None,
    ) -> None:
        open_trade(store, "M0", market_id)
        if winner is None:
            status = "OPEN"
            pnl = None
            closed_at = None
        else:
            status = "SETTLED_WIN" if winner else "SETTLED_LOSS"
            pnl = 1.0 if winner else -1.0
            closed_at = opened_at
        store.db.execute(
            """UPDATE trades
                  SET opened_at=?, status=?, pnl=?, closed_at=?
                WHERE strategy='M0' AND market_id=?""",
            (opened_at, status, pnl, closed_at, market_id),
        )
        store.db.commit()

    # UTC 15:xx => Taipei 23:xx; UTC 16:xx => Taipei 00:xx next day.
    add_result(801, "2026-07-18T15:05:00+00:00", winner=True)
    add_result(802, "2026-07-18T16:05:00+00:00", winner=False)
    add_result(803, "2026-07-18T16:10:00+00:00", winner=True)
    add_result(804, "2026-07-18T17:05:00+00:00", winner=True)
    add_result(805, "2026-07-18T17:10:00+00:00", winner=None)

    payload = store.dashboard(collector_stub())["m0HourlyPerformance"]
    hours = {row["hour"]: row for row in payload["hours"]}

    assert payload["timezone"] == "Asia/Taipei"
    assert payload["utcOffset"] == "+08:00"
    assert payload["settledTrades"] == 4
    assert len(payload["hours"]) == 24
    assert hours[23] == {
        "hour": 23,
        "label": "23:00–23:59",
        "settledTrades": 1,
        "wins": 1,
        "losses": 0,
        "winRatePct": 100.0,
        "averageWinStreak": 1.0,
        "averageLossStreak": None,
        "winThenLossCount": 0,
        "winThenLossOpportunities": 0,
        "winThenLossRatePct": None,
    }
    assert hours[0]["settledTrades"] == 2
    assert hours[0]["wins"] == 1
    assert hours[0]["losses"] == 1
    assert hours[0]["winRatePct"] == pytest.approx(50.0)
    assert hours[0]["averageWinStreak"] == pytest.approx(1.0)
    assert hours[0]["averageLossStreak"] == pytest.approx(1.0)
    assert hours[0]["winThenLossRatePct"] is None
    assert hours[1]["winRatePct"] == pytest.approx(100.0)
    assert hours[2]["settledTrades"] == 0
    assert hours[2]["winRatePct"] is None
    assert hours[2]["averageWinStreak"] is None
    assert hours[2]["averageLossStreak"] is None
    assert hours[2]["winThenLossRatePct"] is None

    store.reset_strategy_measurement("M0")
    add_result(806, "2026-07-18T18:05:00+00:00", winner=False)
    reset_payload = store.dashboard(collector_stub())["m0HourlyPerformance"]
    reset_hours = {row["hour"]: row for row in reset_payload["hours"]}
    assert reset_payload["settledTrades"] == 1
    assert reset_payload["resetAt"] is not None
    assert reset_hours[2]["settledTrades"] == 1
    assert reset_hours[2]["winRatePct"] == pytest.approx(0.0)
    assert reset_hours[2]["averageWinStreak"] is None
    assert reset_hours[2]["averageLossStreak"] == pytest.approx(1.0)
    assert reset_hours[2]["winThenLossRatePct"] is None
    assert reset_hours[0]["settledTrades"] == 0


def test_m0_hourly_streaks_and_win_then_loss_rate_are_time_segmented(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")

    def add_result(market_id: int, opened_at: str, winner: bool) -> None:
        open_trade(store, "M0", market_id)
        store.db.execute(
            """UPDATE trades
                  SET opened_at=?, status=?, pnl=?, closed_at=?
                WHERE strategy='M0' AND market_id=?""",
            (
                opened_at,
                "SETTLED_WIN" if winner else "SETTLED_LOSS",
                1.0 if winner else -1.0,
                opened_at,
                market_id,
            ),
        )

    # Taipei 05:00 hour, day one: W,W,L,L,W,L.
    first_day = (True, True, False, False, True, False)
    for offset, winner in enumerate(first_day):
        add_result(
            901 + offset,
            f"2026-07-18T21:{offset * 5:02d}:00+00:00",
            winner,
        )
    # Same local hour on another date must begin fresh, not extend day one's
    # final loss run. This adds a separate L,L run of length two.
    add_result(907, "2026-07-19T21:00:00+00:00", False)
    add_result(908, "2026-07-19T21:05:00+00:00", False)
    # A W at Taipei 06:55 followed by L at 07:00 is an M0W-eligible W->L.
    # The rate is bucketed to the current/entry hour even across the hour edge.
    add_result(909, "2026-07-18T22:55:00+00:00", True)
    add_result(910, "2026-07-18T23:00:00+00:00", False)
    store.db.commit()

    hours = {
        row["hour"]: row
        for row in store.dashboard(collector_stub())["m0HourlyPerformance"]["hours"]
    }
    hour_five = hours[5]
    assert hour_five["averageWinStreak"] == pytest.approx(1.5)
    assert hour_five["averageLossStreak"] == pytest.approx(5 / 3)
    assert hour_five["winThenLossCount"] == 2
    assert hour_five["winThenLossOpportunities"] == 3
    assert hour_five["winThenLossRatePct"] == pytest.approx(200 / 3)

    hour_seven = hours[7]
    assert hour_seven["averageLossStreak"] == pytest.approx(1.0)
    assert hour_seven["winThenLossCount"] == 1
    assert hour_seven["winThenLossOpportunities"] == 1
    assert hour_seven["winThenLossRatePct"] == pytest.approx(100.0)


def test_strategy_reset_http_endpoint_and_invalid_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = Store(tmp_path / "sim.db")
    monkeypatch.setattr(server_module, "STORE", store)
    httpd = server_module.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, payload = post_json(
            f"{base_url}/api/strategy-reset", {"strategy": "b2"}
        )
        assert status == 200
        assert payload["strategy"] == "B2"
        assert payload["resetAt"] == store.dashboard(collector_stub())["summaries"]["B2"][
            "resetAt"
        ]
        assert payload["cutoffTradeId"] == store.dashboard(collector_stub())["summaries"][
            "B2"
        ]["cutoffTradeId"]

        with pytest.raises(urllib.error.HTTPError) as caught:
            post_json(f"{base_url}/api/strategy-reset", {"strategy": "NOPE"})
        assert caught.value.code == 400
        error_payload = json.loads(caught.value.read())
        assert "unsupported strategy" in error_payload["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_live_rules_http_endpoint_updates_executor_from_loopback(
    monkeypatch: pytest.MonkeyPatch,
):
    class LiveStub:
        def __init__(self):
            self.received = None

        def update_live_rules(self, payload):
            self.received = dict(payload)
            return {"rules": dict(payload), "runtimeEnabled": True}

        def state(self, *, include_ledger=True):
            return {
                "rules": {"strategies": ["M3"]},
                "runtimeEnabled": True,
                "includeLedger": include_ledger,
            }

    live = LiveStub()
    monkeypatch.setattr(server_module, "LIVE_M0W", live)
    httpd = server_module.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    rules = {
        "strategy": "M3",
        "maxStakeUsdt": 2.25,
        "minHourlyWinRatePct": 55,
        "maxHourlyWinThenLossRatePct": 45,
    }
    try:
        with urllib.request.urlopen(f"{base_url}/api/live-rules", timeout=5) as response:
            assert response.status == 200
            rules_payload = json.load(response)
        assert rules_payload["rules"]["strategies"] == ["M3"]
        assert rules_payload["includeLedger"] is False

        status, payload = post_json(f"{base_url}/api/live-rules", rules)
        assert status == 200
        assert live.received == rules
        assert payload["liveM0W"]["rules"] == rules
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
