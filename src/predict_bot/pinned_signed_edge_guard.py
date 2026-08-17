from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .pinned_binance_poly_strategy import PINNED_STRATEGY_NAME


class _PinnedSignedEdgeBlocked(RuntimeError):
    pass


class PinnedSignedEdgeGuardMixin:
    """Prevent place when the special divergence vanished during BUY quoting."""

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        if str(row.get("entry_strategy") or "") != PINNED_STRATEGY_NAME:
            return super()._open_round(row, poly)

        with self.lock:
            client = self.client
        if client is None:
            return super()._open_round(row, poly)

        original_place = client.place_market_order
        blocked: dict[str, Any] = {}

        def guarded_place(*args: Any, **kwargs: Any) -> dict[str, Any]:
            edge_after = base._finite((self.last_entry_latency or {}).get("edgeAfterQuote"))
            minimum = float(self._settings()["pinnedMinimumGap"])
            if edge_after is None or edge_after + 1e-12 < minimum:
                blocked.update(edgeAfterQuote=edge_after, minimum=minimum)
                shown = "unavailable" if edge_after is None else f"{edge_after:+.6f}"
                raise _PinnedSignedEdgeBlocked(
                    f"pinned-divergence signed edge {shown} must remain >= {minimum:.6f}"
                )
            return original_place(*args, **kwargs)

        client.place_market_order = guarded_place  # type: ignore[method-assign]
        try:
            super()._open_round(row, poly)
        finally:
            client.place_market_order = original_place  # type: ignore[method-assign]

        if not blocked:
            return
        edge_after = base._finite(blocked.get("edgeAfterQuote"))
        minimum = float(blocked["minimum"])
        shown = "unavailable" if edge_after is None else f"{edge_after:+.6f}"
        message = (
            f"{PINNED_STRATEGY_NAME} BUY not submitted: signed executable edge {shown} "
            f"must remain >= {minimum:.6f}"
        )
        self._update_round(
            int(row["id"]),
            state="REJECTED",
            close_reason="PINNED_SIGNED_EDGE_BELOW_MIN",
            error_kind="PINNED_SIGNED_EDGE_BELOW_MIN",
            error_message=message,
        )
        self.status = "PINNED_SIGNED_EDGE_BLOCKED"
        self.last_error = message
        self._event(
            "INFO",
            "PINNED_SIGNED_EDGE_BLOCKED",
            int(row.get("market_id") or 0) or None,
            int(row["id"]),
            message,
        )
