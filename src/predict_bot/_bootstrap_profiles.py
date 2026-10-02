"""Conservative startup boundary; not a security sandbox or trading policy."""
from __future__ import annotations
import importlib
import os
import sys

PROFILE_ENV = "PREDICT_BOT_BOOTSTRAP_PROFILE"
COLLECTOR_ENTRYPOINTS = frozenset({
    "predict_bot.target_wallet_official_v1",
    "predict_bot.target_wallet_official_v2",
    "predict_bot.predict_wallet_shadow_observer_v4_23",
})
_COLLECTOR_IMPORTS = COLLECTOR_ENTRYPOINTS | {
    "predict_bot._bootstrap_profiles",
    # Audited zero-work 8777 compatibility module; not a supported launcher target.
    "predict_bot.predict_wallet_taker_signal_collector",
}
_active_profile: str | None = None
_guard = None


class _CollectorImportBoundary:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("predict_bot.") and fullname not in _COLLECTOR_IMPORTS:
            raise ModuleNotFoundError(
                f"Collector bootstrap refuses unaudited module {fullname!r}. "
                "Use a fresh process with the default legacy profile for other workloads.",
                name=fullname,
            )
        return None


def initialize() -> None:
    """Select once; reject unknown profiles, mixed workloads and mode switching."""
    global _active_profile, _guard
    profile = os.environ.get(PROFILE_ENV, "legacy")
    if profile not in {"legacy", "collector"}:
        raise RuntimeError(f"Invalid {PROFILE_ENV}={profile!r}; expected legacy or collector")
    if _active_profile is not None:
        if _active_profile != profile:
            raise RuntimeError("Bootstrap profile cannot change inside an existing process")
        return
    if profile == "collector":
        unexpected = sorted(name for name in sys.modules
                            if name.startswith("predict_bot.") and name not in _COLLECTOR_IMPORTS)
        if unexpected:
            raise RuntimeError(f"Collector bootstrap requires a fresh process: {unexpected}")
        _guard = _CollectorImportBoundary()
        sys.meta_path.insert(0, _guard)
        _active_profile = profile
        return
    # The original initializer is retained byte-for-byte, including call order.
    # Failed partial installation must not be reused as a healthy bootstrap.
    _active_profile = "initializing"
    try:
        importlib.import_module("predict_bot._legacy_bootstrap")
    except BaseException:
        _active_profile = "failed"
        raise
    _active_profile = profile
