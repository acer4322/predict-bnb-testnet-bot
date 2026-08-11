from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_multi_asset_live_v1 import MULTI_OBSERVER_URL, MultiAssetPolyGapLiveEngine


REQUIRED_OBSERVER_VERSION = "MULTI_PREDICTION_OBSERVER_V2"


def ws_generation_policy(
    *,
    observer_version: str | None,
    current_ws_session: int | None,
    quote_ws_session: int | None,
) -> str:
    """Fail-closed BUY authorization policy for the 8770 Poly quote generation."""
    if str(observer_version or "") != REQUIRED_OBSERVER_VERSION:
        return "BLOCK_OBSERVER_VERSION"
    try:
        current = int(current_ws_session or 0)
    except (TypeError, ValueError):
        current = 0
    try:
        quote = int(quote_ws_session or 0)
    except (TypeError, ValueError):
        quote = 0
    if current <= 0 or quote <= 0:
        return "BLOCK_SESSION_UNAVAILABLE"
    if current != quote:
        return "BLOCK_STALE_WS_GENERATION"
    return "ALLOW"


class LiveGradeMultiAssetPolyGapLiveEngine(MultiAssetPolyGapLiveEngine):
    """V2: require quote freshness to belong to the current 8770 WS session."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._asset_observer_version: str | None = None
        self._last_asset_observer_state: dict[str, Any] | None = None
        self._ws_generation_blocks = 0
        self._last_ws_generation_policy: str | None = None
        super().__init__(*args, **kwargs)

    def _asset_observer_state(self) -> dict[str, Any] | None:
        try:
            response = self.http.get(MULTI_OBSERVER_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.last_error = f"{self.asset} observer state: {str(exc)[:300]}"
            return None
        if not isinstance(payload, dict):
            return None
        root = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        if not isinstance(root, dict):
            return None
        self._asset_observer_version = str(root.get("version") or "") or None
        assets = root.get("assets")
        row = assets.get(self.asset) if isinstance(assets, dict) else None
        if not isinstance(row, dict):
            return None
        self._last_asset_observer_state = row
        return row

    def _poly_state(self) -> dict[str, Any] | None:
        result = super()._poly_state()
        if not isinstance(result, dict):
            return None

        state = self._last_asset_observer_state or {}
        poly = state.get("poly") if isinstance(state, dict) else None
        up = poly.get("up") if isinstance(poly, dict) else None
        if not isinstance(up, dict):
            self._last_ws_generation_policy = "BLOCK_SESSION_UNAVAILABLE"
            self._ws_generation_blocks += 1
            self.last_poly = None
            return None
        try:
            current_session = int(up.get("wsSession") or 0)
        except (TypeError, ValueError):
            current_session = 0
        try:
            quote_session = int(up.get("quoteWsSession") or 0)
        except (TypeError, ValueError):
            quote_session = 0

        policy = ws_generation_policy(
            observer_version=self._asset_observer_version,
            current_ws_session=current_session,
            quote_ws_session=quote_session,
        )
        self._last_ws_generation_policy = policy
        if policy != "ALLOW":
            self._ws_generation_blocks += 1
            self.last_poly = None
            if policy == "BLOCK_OBSERVER_VERSION":
                self.last_error = (
                    f"{self.asset} Echtgeld requires {REQUIRED_OBSERVER_VERSION}; "
                    f"received {self._asset_observer_version or 'UNKNOWN'}"
                )
            else:
                self.last_error = (
                    f"{self.asset} Poly quote is not from the current WS generation: "
                    f"quoteWsSession={quote_session or None}; wsSession={current_session or None}; "
                    f"policy={policy}; waiting for a fresh book/price_change before BUY"
                )
            return None

        result["collectorCurrentWsSession"] = current_session
        result["collectorQuoteWsSession"] = quote_session
        result["signalFreshnessTimestampSource"] = "MULTI_OBSERVER_V2_CURRENT_WS_QUOTE"
        self.last_poly = result
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_MULTI_ASSET_LIVE_V2"
        payload["multiAssetWsFreshnessV2"] = {
            "enabled": True,
            "requiredObserverVersion": REQUIRED_OBSERVER_VERSION,
            "observedObserverVersion": self._asset_observer_version,
            "currentWsQuoteRequiredForBuy": True,
            "lastPolicy": self._last_ws_generation_policy,
            "wsGenerationBlocks": int(self._ws_generation_blocks),
            "sellPolicyRelaxed": False,
        }
        payload.setdefault("rules", {}).update(
            multiAssetCurrentWsQuoteRequiredV2=True,
            multiAssetObserverVersionRequired=REQUIRED_OBSERVER_VERSION,
        )
        return payload


base.PolyGapLiveEngine = LiveGradeMultiAssetPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
