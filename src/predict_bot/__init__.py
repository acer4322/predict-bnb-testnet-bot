"""Predict.fun BNB Testnet research bot."""

from .confirmation_add_sources_patch import (
    install_confirmation_add_sources_patch as _install_confirmation_add_sources_patch,
)
from .decision_snapshot_diagnostics import (
    install_decision_snapshot_diagnostics as _install_decision_snapshot_diagnostics,
)
from .decision_snapshot_state import (
    install_decision_snapshot_state as _install_decision_snapshot_state,
)
from .direct_outcome_guard import (
    install_direct_outcome_guard as _install_direct_outcome_guard,
)
from .microprice_variant_relaxation import (
    install_microprice_variant_relaxation as _install_microprice_variant_relaxation,
)
from .microprice_variants import (
    install_microprice_variants as _install_microprice_variants,
)
from .microprice_dashboard_patch import (
    install_microprice_dashboard_patch as _install_microprice_dashboard_patch,
)

_install_decision_snapshot_diagnostics()
_install_decision_snapshot_state()
_install_direct_outcome_guard()
_install_microprice_variant_relaxation()
_install_microprice_variants()
_install_microprice_dashboard_patch()
_install_confirmation_add_sources_patch()
del _install_confirmation_add_sources_patch
del _install_decision_snapshot_diagnostics
del _install_decision_snapshot_state
del _install_direct_outcome_guard
del _install_microprice_variant_relaxation
del _install_microprice_variants
del _install_microprice_dashboard_patch
