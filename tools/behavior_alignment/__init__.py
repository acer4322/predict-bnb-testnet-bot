"""Behavioral-semantic alignment utilities for the BTC 5M research lab.

This package is research-only.  It normalizes already-recorded OUR and Target
evidence; it has no order or runtime authority.
"""

from .schema import (
    EVIDENCE_CLASSES,
    EVENT_TYPES,
    TRACE_SCHEMA_VERSION,
    canonical_event,
    trace_document,
    trace_json_schema,
    validate_trace_document,
)

__all__ = [
    "EVIDENCE_CLASSES",
    "EVENT_TYPES",
    "TRACE_SCHEMA_VERSION",
    "canonical_event",
    "trace_document",
    "trace_json_schema",
    "validate_trace_document",
]
