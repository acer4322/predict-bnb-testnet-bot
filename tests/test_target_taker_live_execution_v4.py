from __future__ import annotations

import pytest

from predict_bot import target_taker_live_execution_v4 as live


class _FakePredictClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def _request(self, method: str, path: str) -> dict:
        self.calls.append((method, path))
        return self.payload


def test_exact_predict_market_lookup_uses_signal_market_id() -> None:
    client = _FakePredictClient({"success": True, "data": {"id": 6827001}})
    market = live.TargetTakerLiveExecutor._predict_market(client, 6827001)
    assert market["id"] == 6827001
    assert client.calls == [("GET", "/v1/markets/6827001")]


def test_exact_predict_market_lookup_rejects_mismatch() -> None:
    client = _FakePredictClient({"success": True, "data": {"id": 6827002}})
    with pytest.raises(live.TargetTakerLiveError, match="expected 6827001, returned 6827002"):
        live.TargetTakerLiveExecutor._predict_market(client, 6827001)
