from __future__ import annotations

from predict_bot import hft_forward_paper_collector_v1 as m


def test_r2_summary_uses_confirmed_inventory_only():
    report = {
        "studentRollout": {
            "decisions": 10,
            "makerPlacements": 3,
            "makerFillEvents": 2,
            "makerFilledShares": 27.0,
            "takerFills": 1,
            "takerFilledShares": 5.0,
            "makerCostUsdt": 11.0,
            "takerCostUsdt": 3.0,
            "takerFeesUsdt": 0.06,
            "finalPortfolio": {
                "maker_gross": 27.0,
                "maker_net": 9.0,
                "taker_gross": 5.0,
                "taker_net": -5.0,
                "combined_abs_net": 4.0,
            },
            "runMetrics": {},
            "submitRejects": [],
        }
    }
    s = m._summary("R2", report)
    assert s["makerUpShares"] == 18.0
    assert s["makerDownShares"] == 9.0
    assert s["takerUpShares"] == 0.0
    assert s["takerDownShares"] == 5.0
    assert s["totalCostUsdt"] == 14.06
    assert s["finalAbsNet"] == 4.0


def test_cap100_summary_uses_hft_closed_loop_ledger():
    report = {
        "closedLoop": {
            "decisions": 7,
            "makerPlacements": 4,
            "makerFillEvents": 2,
            "makerFilledShares": 20.0,
            "takerFills": 1,
            "runMetrics": {},
            "submitRejects": [],
            "finalPortfolio": {"maker_net": -2.0, "combined_abs_net": 3.0},
            "ledger": {
                "makerUpShares": 9.0,
                "makerDownShares": 11.0,
                "takerUpShares": 5.0,
                "takerDownShares": 0.0,
                "makerCostUsdt": 8.0,
                "takerCostUsdt": 2.5,
                "takerFeesUsdt": 0.05,
            },
        },
        "takerEvents": [{"shares": 5.0}],
    }
    s = m._summary("CAP100", report)
    assert s["totalCostUsdt"] == 10.55
    assert s["takerFilledShares"] == 5.0
    assert s["finalMakerNet"] == -2.0


def test_fill_rows_preserve_partial_fill_quantities():
    report = {
        "makerFillEvents": [
            {"atMs": 100, "side": "UP", "price": 0.4, "deltaShares": 6.5, "orderId": "a", "status": "PARTIALLY_FILLED"},
            {"atMs": 200, "side": "UP", "price": 0.4, "deltaShares": 11.5, "orderId": "a", "status": "FILLED"},
        ],
        "takerEvents": [{"atMs": 300, "decisionMs": 250, "side": "DOWN", "price": 0.7, "shares": 4.0, "hftStatus": "PARTIALLY_FILLED"}],
    }
    rows = m._fill_rows("R2", report)
    assert [r["shares"] for r in rows] == [6.5, 11.5, 4.0]
    assert [r["channel"] for r in rows] == ["MAKER", "MAKER", "TAKER"]


def test_official_collector_never_labels_queueclear_as_execution():
    assert m.EXECUTION_LABEL == "HFTBACKTEST_PREDICT_TAPE_CLOSED_LOOP_FORWARD_PAPER"
    assert m.DB_PATH.name == "hft_forward_paper_v1.db"


def test_archive_candidate_assessment_is_cached_until_file_changes(tmp_path, monkeypatch):
    from src.predict_bot import execution_tape_quality_v1 as quality

    monkeypatch.setattr(m, "ARCHIVE_DIR", tmp_path)
    paths = [tmp_path / "101.json.xz", tmp_path / "102.json.xz"]
    for path in paths:
        path.write_bytes(b"tape")

    assessed: list[str] = []

    def fake_assess(path):
        assessed.append(path.name)
        market_id = int(path.name.split(".", 1)[0])
        return {
            "marketId": market_id,
            "windowEndMs": 1_000 + market_id,
            "qualityStatus": "COMPLETE_FORWARD_V1",
            "eligibleExecutionTraining": True,
        }

    monkeypatch.setattr(quality, "assess_archive", fake_assess)
    cache = {}
    stats = {}

    first = m._archive_candidates(0, 0, assessment_cache=cache, scan_stats=stats)
    assert [row["market_id"] for row in first] == [101, 102]
    assert len(assessed) == 2
    assert stats["assessedArchives"] == 2

    second_stats = {}
    second = m._archive_candidates(0, 0, assessment_cache=cache, scan_stats=second_stats)
    assert second == first
    assert len(assessed) == 2
    assert second_stats["assessedArchives"] == 0
    assert second_stats["assessmentCacheHits"] == 2

    paths[1].write_bytes(b"changed-tape")
    third_stats = {}
    m._archive_candidates(0, 0, assessment_cache=cache, scan_stats=third_stats)
    assert len(assessed) == 3
    assert third_stats["assessedArchives"] == 1
    assert third_stats["assessmentCacheHits"] == 1


def test_archive_candidate_scan_skips_already_completed_markets(tmp_path, monkeypatch):
    from src.predict_bot import execution_tape_quality_v1 as quality

    monkeypatch.setattr(m, "ARCHIVE_DIR", tmp_path)
    for market_id in (201, 202):
        (tmp_path / f"{market_id}.json.xz").write_bytes(b"tape")

    assessed: list[int] = []

    def fake_assess(path):
        market_id = int(path.name.split(".", 1)[0])
        assessed.append(market_id)
        return {
            "marketId": market_id,
            "windowEndMs": 2_000 + market_id,
            "qualityStatus": "COMPLETE_FORWARD_V1",
            "eligibleExecutionTraining": True,
        }

    monkeypatch.setattr(quality, "assess_archive", fake_assess)
    stats = {}
    rows = m._archive_candidates(0, 0, skip_market_ids={201}, scan_stats=stats)
    assert [row["market_id"] for row in rows] == [202]
    assert assessed == [202]
    assert stats["skippedCompletedMarkets"] == 1
