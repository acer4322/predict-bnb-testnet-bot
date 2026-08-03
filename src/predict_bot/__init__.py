"""Predict.fun BNB Testnet research bot."""

from .decision_snapshot_diagnostics import (
    install_decision_snapshot_diagnostics as _install_decision_snapshot_diagnostics,
)
from .decision_snapshot_state import (
    install_decision_snapshot_state as _install_decision_snapshot_state,
)
from .direct_outcome_guard import (
    install_direct_outcome_guard as _install_direct_outcome_guard,
)

_install_decision_snapshot_diagnostics()
_install_decision_snapshot_state()
_install_direct_outcome_guard()
del _install_decision_snapshot_diagnostics
del _install_decision_snapshot_state
del _install_direct_outcome_guard
