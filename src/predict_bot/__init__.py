"""Predict.fun research bot with explicit, process-scoped startup profiles.

Default: complete legacy bootstrap, including all existing protections.
Audited collectors alone may opt into PREDICT_BOT_BOOTSTRAP_PROFILE=collector.
"""
from ._bootstrap_profiles import initialize as _initialize

_initialize()
del _initialize
