from __future__ import annotations

from predict_bot import predict_wallet_shadow_observer as base
from predict_bot.predict_wallet_shadow_observer_v3 import (
    WalletShadowObserver,
    paper_market_result,
    resolved_winner,
)


def test_resolved_winner_prefers_explicit_winning_outcome() -> None:
    market = {
        "outcomes": [
            {"name": "UP", "status": "LOST"},
            {"name": "DOWN", "status": "WON"},
        ],
        "variantData": {"startPrice": "100", "endPrice": "110"},
    }
    assert resolved_winner(market) == "DOWN"


def test_resolved_winner_falls_back_to_final_price_move() -> None:
    assert resolved_winner({"variantData": {"startPrice": "100", "endPrice": "101"}}) == "UP"
    assert resolved_winner({"variantData": {"startPrice": "100", "endPrice": "99"}}) == "DOWN"
    assert resolved_winner({"variantData": {"startPrice": "100", "endPrice": "100"}}) is None


def test_paper_market_result_uses_shadow_fill_prices_and_binary_payout() -> None:
    result = paper_market_result(
        [
            {
                "event_type": "MAKER_FILL_PROXY",
                "role": "MAKER",
                "side": "UP",
                "price": 0.41,
                "shares": 18,
            },
            {
                "event_type": "TAKER_INTENT",
                "role": "TAKER",
                "side": "DOWN",
                "price": 0.70,
                "shares": 36,
            },
            {
                "event_type": "MAKER_QUOTE",
                "role": "MAKER",
                "side": "DOWN",
                "price": 0.55,
                "shares": 18,
            },
        ],
        "DOWN",
    )
    assert result["fillCount"] == 2
    assert result["traded"] is True
    assert result["status"] == "WIN"
    assert abs(result["costUsdt"] - 32.58) < 1e-9
    assert abs(result["payoutUsdt"] - 36.0) < 1e-9
    assert abs(result["grossPnlUsdt"] - 3.42) < 1e-9
    assert abs(result["maker"]["grossPnlUsdt"] + 7.38) < 1e-9
    assert abs(result["taker"]["grossPnlUsdt"] - 10.8) < 1e-9


def test_paper_market_result_excludes_no_trade_from_win_rate_domain() -> None:
    result = paper_market_result([], "UP")
    assert result["traded"] is False
    assert result["status"] == "NO_TRADE"
    assert result["grossPnlUsdt"] == 0


def test_retention_cleanup_deletes_old_target_shadow_and_results(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db")
    now = base._now_ms()
    observer.retention_ms = 1_000
    old = now - 10_000
    fresh = now
    with observer.db_lock:
        observer.db.execute(
            """INSERT INTO wallet_shadow_target_events(
                leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("old", observer.wallet, 1, "MAKER", "UP", "BID", "a", old, 0.4, 18, "{}"),
        )
        observer.db.execute(
            """INSERT INTO wallet_shadow_target_events(
                leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("new", observer.wallet, 2, "MAKER", "UP", "BID", "b", fresh, 0.4, 18, "{}"),
        )
        observer.db.execute(
            """INSERT INTO wallet_shadow_events(
                id,wallet,market_id,at_ms,event_type,role,side,price,shares,core_side,core_source,reason,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("old-shadow", observer.wallet, 1, old, "MAKER_FILL_PROXY", "MAKER", "UP", 0.4, 18, "UP", "test", "test", "{}"),
        )
        observer.db.execute(
            """INSERT INTO wallet_shadow_events(
                id,wallet,market_id,at_ms,event_type,role,side,price,shares,core_side,core_source,reason,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("new-shadow", observer.wallet, 2, fresh, "MAKER_FILL_PROXY", "MAKER", "UP", 0.4, 18, "UP", "test", "test", "{}"),
        )
        for market_id, resolved_at in ((1, old), (2, fresh)):
            observer.db.execute(
                """INSERT INTO wallet_shadow_market_results(
                    wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                    cost_usdt,payout_usdt,gross_pnl_usdt,gross_roi,
                    maker_cost_usdt,maker_pnl_usdt,taker_cost_usdt,taker_pnl_usdt
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (observer.wallet, market_id, "BTC", "UP", resolved_at, 1, "WIN", 1, 7.2, 18, 10.8, 1.5, 7.2, 10.8, 0, 0),
            )
        observer.db.commit()

    observer._cleanup_retention(force=True)

    with observer.db_lock:
        target_ids = {row[0] for row in observer.db.execute("SELECT leg_id FROM wallet_shadow_target_events")}
        shadow_ids = {row[0] for row in observer.db.execute("SELECT id FROM wallet_shadow_events")}
        result_ids = {row[0] for row in observer.db.execute("SELECT market_id FROM wallet_shadow_market_results")}
    assert target_ids == {"new"}
    assert shadow_ids == {"new-shadow"}
    assert result_ids == {2}
    observer.stop()


def test_performance_snapshot_counts_profit_markets_not_target_similarity(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "performance.db")
    now = base._now_ms()
    rows = [
        (10, "WIN", 10.0, 2.0),
        (11, "LOSS", 20.0, -3.0),
        (12, "FLAT", 5.0, 0.0),
    ]
    with observer.db_lock:
        for market_id, status, cost, pnl in rows:
            observer.db.execute(
                """INSERT INTO wallet_shadow_market_results(
                    wallet,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                    cost_usdt,payout_usdt,gross_pnl_usdt,gross_roi,
                    maker_cost_usdt,maker_pnl_usdt,taker_cost_usdt,taker_pnl_usdt
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    observer.wallet, market_id, "BTC", "UP", now, 1, status, 1,
                    cost, cost + pnl, pnl, pnl / cost,
                    cost / 2, pnl / 2, cost / 2, pnl / 2,
                ),
            )
        observer.db.commit()

    performance = observer._performance_snapshot()
    assert performance["tradedMarkets"] == 3
    assert performance["wins"] == 1
    assert performance["losses"] == 1
    assert performance["flats"] == 1
    assert performance["winRate"] == 1 / 3
    assert abs(performance["grossPnlUsdt"] + 1.0) < 1e-9
    observer.stop()
