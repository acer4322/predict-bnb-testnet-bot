from __future__ import annotations

from pathlib import Path

from predict_bot.poly_feed_latency_probe import PolyFeedLatencyProbe


def test_rest_minus_ws_positive_means_ws_first(tmp_path: Path) -> None:
    probe = PolyFeedLatencyProbe(250, 10.0, tmp_path / "summary.json")
    try:
        probe.market_slug = "btc-updown-5m-test"
        probe.token_outcome = {"token-up": "UP"}
        probe._record_state(
            "WS",
            {
                "source": "WS",
                "market": probe.market_slug,
                "token": "token-up",
                "outcome": "UP",
                "hash": "same-hash",
                "bestBid": 0.79,
                "bestAsk": 0.81,
                "sourceTimestampMs": 1_000,
                "receivedAtMs": 1_100,
            },
        )
        probe._record_state(
            "REST",
            {
                "source": "REST",
                "market": probe.market_slug,
                "token": "token-up",
                "outcome": "UP",
                "hash": "same-hash",
                "bestBid": 0.79,
                "bestAsk": 0.81,
                "sourceTimestampMs": 1_000,
                "receivedAtMs": 1_225,
            },
        )
        summary = probe.summary()
        assert summary["matchedBookStates"] == 1
        assert summary["wsFirst"] == 1
        assert summary["restFirst"] == 0
        assert summary["restMinusWsMs"]["median"] == 125.0
    finally:
        probe.close()


def test_same_hash_is_counted_only_once(tmp_path: Path) -> None:
    probe = PolyFeedLatencyProbe(250, 10.0, tmp_path / "summary.json")
    try:
        common = {
            "market": "btc-updown-5m-test",
            "token": "token-down",
            "outcome": "DOWN",
            "hash": "dedupe-hash",
            "bestBid": 0.19,
            "bestAsk": 0.21,
            "sourceTimestampMs": 2_000,
        }
        probe._record_state("REST", {**common, "source": "REST", "receivedAtMs": 2_100})
        probe._record_state("WS", {**common, "source": "WS", "receivedAtMs": 2_140})
        probe._record_state("WS", {**common, "source": "WS", "receivedAtMs": 2_150})
        probe._record_state("REST", {**common, "source": "REST", "receivedAtMs": 2_160})
        summary = probe.summary()
        assert summary["matchedBookStates"] == 1
        assert summary["restFirst"] == 1
        assert summary["restMinusWsMs"]["median"] == -40.0
    finally:
        probe.close()
