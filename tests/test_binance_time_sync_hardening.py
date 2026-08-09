from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from predict_bot import binance_time_sync_hardening as sync
from predict_bot.core import ApiHttpError, BinancePredictionClient, BinancePredictionTradingClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = int(status_code)
        self._payload = dict(payload)
        self.text = json.dumps(payload, separators=(",", ":"))
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


class FakeHttp:
    def __init__(
        self,
        *,
        get_responses: list[FakeResponse] | None = None,
        post_responses: list[FakeResponse] | None = None,
    ) -> None:
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = 0
        self.post_calls = 0

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        self.get_calls += 1
        assert self.get_responses
        return self.get_responses.pop(0)

    def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        self.post_calls += 1
        assert self.post_responses
        return self.post_responses.pop(0)

    def close(self) -> None:
        return


def _seed_bad_but_fresh_offset(client: BinancePredictionClient, offset_ms: int = 1500) -> None:
    sync._ensure_time_sync_state(client)
    client._time_offset_ms = int(offset_ms)
    client._time_sync_last_mono = time.monotonic()
    client._time_sync_last_wall_ms = int(time.time() * 1000)
    client._time_sync_last_server_ms = int(time.time() * 1000) + int(offset_ms)


def test_signed_get_1021_resyncs_and_retries_once() -> None:
    http = FakeHttp(
        get_responses=[
            FakeResponse(
                400,
                {
                    "code": -1021,
                    "msg": "Timestamp for this request was 1000ms ahead of the server's time.",
                },
            ),
            FakeResponse(200, {"orders": []}),
        ]
    )
    client = BinancePredictionClient("key", "secret", http_client=http)  # type: ignore[arg-type]
    _seed_bad_but_fresh_offset(client)
    client.get = lambda path, query=None: {  # type: ignore[method-assign]
        "serverTime": int(time.time() * 1000)
    }

    result = client.signed_get("/sapi/v1/w3w/wallet/prediction/order/history")

    assert result == {"orders": []}
    assert http.get_calls == 2
    state = sync.time_sync_snapshot(client)
    assert state["timestampErrorRecoveries"] == 1
    assert state["lastTimestampErrorPath"].endswith("/order/history")
    assert state["offsetMs"] is not None


def test_signed_post_1021_resyncs_clock_but_never_retries_write() -> None:
    http = FakeHttp(
        post_responses=[
            FakeResponse(
                400,
                {
                    "code": -1021,
                    "msg": "Timestamp for this request was 1000ms ahead of the server's time.",
                },
            )
        ]
    )
    client = BinancePredictionTradingClient("key", "secret", http_client=http)  # type: ignore[arg-type]
    _seed_bad_but_fresh_offset(client)
    client.get = lambda path, query=None: {  # type: ignore[method-assign]
        "serverTime": int(time.time() * 1000)
    }

    with pytest.raises(ApiHttpError):
        client.signed_post(
            "/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle",
            {"quoteId": "q1"},
        )

    assert http.post_calls == 1
    state = sync.time_sync_snapshot(client)
    assert state["timestampErrorRecoveries"] == 1
    assert state["signedPost1021AutoRetry"] is False
    assert state["offsetMs"] is not None


def test_periodic_refresh_replaces_stale_cached_offset() -> None:
    http = FakeHttp()
    client = BinancePredictionClient("key", "secret", http_client=http)  # type: ignore[arg-type]
    sync._ensure_time_sync_state(client)
    client._time_offset_ms = 2000
    client._time_sync_last_mono = time.monotonic() - sync.TIME_SYNC_REFRESH_SECONDS - 1.0
    client._time_sync_last_wall_ms = int(time.time() * 1000) - int(
        (sync.TIME_SYNC_REFRESH_SECONDS + 1.0) * 1000
    )
    client.get = lambda path, query=None: {  # type: ignore[method-assign]
        "serverTime": int(time.time() * 1000)
    }

    before = int(time.time() * 1000)
    observed = client.server_timestamp_ms()
    after = int(time.time() * 1000)

    assert before - 50 <= observed <= after + 50
    state = sync.time_sync_snapshot(client)
    assert state["refreshes"] >= 1
    assert abs(int(state["offsetMs"])) < 100


def test_supervisor_loads_time_hardening_in_all_signed_binance_processes() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.server_binance_prefetch_v4" in source
    assert "predict_bot.cross_oracle_strategy_chop_guard_v3" in source
    assert "predict_bot.poly_gap_live_v19" in source
    # V19 still inherits the V16 time-sync layer through V17/V18.
    assert "predict_bot.poly_gap_live_v16" in source
