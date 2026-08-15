from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from .. import live_loss_attribution_report as v1


def _trajectory_snapshot_with_tolerance(
    rows: list[dict[str, Any]],
    *,
    at_ms: int | None,
    side: str,
    tolerance_ms: int = v1.SAMPLE_NEAREST_TOLERANCE_MS,
) -> dict[str, Any] | None:
    """V1-compatible trajectory snapshot with a caller-selected time tolerance."""
    sample = v1._nearest_sample(rows, at_ms, tolerance_ms=tolerance_ms)
    if sample is None:
        return None
    poly_up = v1._finite(sample.get("poly_up_mid"))
    binance_up = v1._finite(sample.get("binance_up_mid"))
    return {
        "observedAtMs": v1._int(sample.get("observed_at_ms")),
        "nearestDeltaMs": v1._int(sample.get("nearestDeltaMs")),
        "polyReceivedAtMs": v1._int(sample.get("poly_received_at_ms")),
        "binanceObservedAtMs": v1._int(sample.get("binance_observed_at_ms")),
        "polyUpMid": poly_up,
        "binanceUpMid": binance_up,
        "polySelectedMid": v1._selected(poly_up, side),
        "binanceSelectedMid": v1._selected(binance_up, side),
        "secondsLeftSkew": v1._finite(sample.get("seconds_left_skew")),
        "binanceBookAgeMs": v1._finite(sample.get("binance_book_age_ms")),
        "binanceBookSkewMs": v1._finite(sample.get("binance_book_skew_ms")),
    }


# The V2 implementation intentionally asks for a 1.5s nearest-sample tolerance
# for sparse 10/30/60/120s diagnostics. V1 originally exposed a fixed 1s helper.
# Install the backward-compatible superset before loading the implementation.
v1._trajectory_snapshot = _trajectory_snapshot_with_tolerance

_IMPL_PATH = Path(__file__).resolve().parent.parent / "live_loss_attribution_report_v2.py"
_SPEC = importlib.util.spec_from_file_location(
    "predict_bot._live_loss_attribution_report_v2_impl",
    _IMPL_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"unable to load V2 implementation from {_IMPL_PATH}")
_IMPL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_IMPL)

# Preserve the original module API for tests/importers while the package shim
# makes `python -m predict_bot.live_loss_attribution_report_v2` work unchanged.
for _name in dir(_IMPL):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_IMPL, _name)

