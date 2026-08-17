from __future__ import annotations

from pathlib import Path

from predict_bot import target_taker_public_side_test_v1 as module


def _close(test: module.TargetTakerPublicSideTest) -> None:
    test.client.close()
    test.predict_api_client.close()
    test.db.close()


def test_service_boundary_does_not_open_or_write_official_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(module, "MICRO_BOOTSTRAP_DB", tmp_path / "micro-bootstrap.db")
    test = module.TargetTakerPublicSideTest(tmp_path / "strategy.db")
    try:
        health = test.health_snapshot()
        assert health["strategy"] == "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
        assert health["paperOnly"] is True
        assert health["liveOrdersAffected"] is False
        assert health["writesTo8776"] is False
        assert health["directOfficialDbAccess"] is False
        assert health["targetEventsUsedForDecision"] is False
        assert health["officialSourceUrl"] == "http://127.0.0.1:8776/state"
    finally:
        _close(test)


def test_first_observed_market_is_forward_deployment_boundary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(module, "MICRO_BOOTSTRAP_DB", tmp_path / "micro-bootstrap.db")
    test = module.TargetTakerPublicSideTest(tmp_path / "strategy.db")
    try:
        assert test.excluded_market_id is None
        assert test._register_market(1001, "deployment") is False
        assert test.excluded_market_id == 1001
        assert test._register_market(1002, "next full market") is True
        stored = test.db.execute("SELECT market_id FROM strategy_test_markets ORDER BY market_id").fetchall()
        assert [int(row[0]) for row in stored] == [1002]
    finally:
        _close(test)


def test_official_http_settlement_scores_paper_trade(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(module, "MICRO_BOOTSTRAP_DB", tmp_path / "micro-bootstrap.db")
    test = module.TargetTakerPublicSideTest(tmp_path / "strategy.db")
    try:
        with test.db:
            test.db.execute(
                "INSERT INTO strategy_test_markets(market_id,title,started_at_ms) VALUES(?,?,?)",
                (2002, "BTC test", 1),
            )
            test.db.execute(
                """INSERT INTO strategy_test_trades(
                     market_id,decision_at_ms,snapshot_timestamp_ns,side,observed_ask,effective_unit_cost,
                     stake_usdt,shares,selected_probability,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (2002, 2, 3, "UP", 0.5, 0.51, 1.0, 1.9607843137, 0.71, "{}"),
            )
        test._settle_from_official(
            {
                "targetRecentMarkets": [
                    {
                        "market_id": 2002,
                        "title": "BTC test",
                        "winner": "UP",
                        "resolved_at_ms": 4,
                    }
                ]
            }
        )
        row = test.db.execute(
            "SELECT status,winner,net_pnl_usdt FROM strategy_test_results WHERE market_id=2002"
        ).fetchone()
        assert row is not None
        assert row["status"] == "WIN"
        assert row["winner"] == "UP"
        assert float(row["net_pnl_usdt"]) > 0
    finally:
        _close(test)


def test_frozen_feature_contract_is_exact_side_ebm() -> None:
    assert module.STRATEGY == "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
    assert len(module.public_side.SIDE_EBM_EXPECTED_FEATURES) == 16
    assert "target_side" not in module.public_side.SIDE_EBM_EXPECTED_FEATURES
    assert "target_role" not in module.public_side.SIDE_EBM_EXPECTED_FEATURES
