from __future__ import annotations

from . import live_trading as _live
from . import m_realtime as _realtime
from .decision_strategy_context_cache_patch import (
    install_decision_strategy_context_cache_patch,
)
from .decision_strategy_dashboard_detach_patch import (
    install_decision_strategy_dashboard_detach_patch,
)
from .decision_strategy_frozen_rules_patch import (
    install_decision_strategy_frozen_rules_patch,
)
from .decision_strategy_runtime_corrections_patch import (
    install_decision_strategy_runtime_corrections_patch,
)
from .decision_strategy_safe_integration_patch import (
    install_decision_strategy_safe_integration_patch,
)


DECISION_STRATEGIES = ("R_DECISION_RANK1", "R_DECISION_RANK2")


def _deduplicated(*values: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value).strip().upper()
            for value in values
            if str(value).strip()
        )
    )


def install_decision_strategy_live_forward_patch() -> None:
    """Install Rank 1/2 without changing legacy dashboard or event cadence.

    The strategies remain native event-driven Paper candidates and live
    whitelist options. Evaluation is triggered only after an included source
    family opens a new trade; exceptions return the original candidates intact.
    """

    install_decision_strategy_frozen_rules_patch()
    install_decision_strategy_context_cache_patch()
    install_decision_strategy_runtime_corrections_patch()
    install_decision_strategy_dashboard_detach_patch()
    install_decision_strategy_safe_integration_patch()

    research = _deduplicated(
        *_live.LIVE_RESEARCH_STRATEGIES,
        *DECISION_STRATEGIES,
    )
    supported = _deduplicated(
        *_live.LIVE_SUPPORTED_STRATEGIES,
        *DECISION_STRATEGIES,
    )

    _live.LIVE_RESEARCH_STRATEGIES = research
    _live.LIVE_SUPPORTED_STRATEGIES = supported

    _realtime.LIVE_RESEARCH_STRATEGIES = research
    _realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES = (
        set(_realtime.LIVE_FORWARDABLE_OBSERVER_STRATEGIES) | set(research)
    )
