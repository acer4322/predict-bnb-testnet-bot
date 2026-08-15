from __future__ import annotations


PATCH_VERSION = "LIVE_RULES_ASYNC_PREFLIGHT_DISABLED"


def install_live_rules_async_preflight_patch() -> None:
    """Emergency-disable the update_live_rules monkey patch.

    Live-rule persistence must stay on the engine's native, tested path.  The
    previous implementation temporarily replaced ``self._preflight`` on the
    live engine instance and started an additional background worker after each
    save.  That made engine lifecycle and failure handling harder to reason
    about during real-money operation.  A future non-blocking preflight must be
    implemented inside ``LiveM0WEngine`` with explicit state and shutdown
    ownership rather than by runtime monkey patching.
    """

    return None
