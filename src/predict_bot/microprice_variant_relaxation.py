from __future__ import annotations


MICROPRICE_RELAXED_VERSION = "MICROPRICE_VARIANTS_V2_RELAXED"


def install_microprice_variant_relaxation() -> None:
    """Relax confirmation cadence without weakening direct-book safety gates.

    Keep the two most important protections unchanged:
    - independently fetched dual-token REST books only;
    - book age <= 500ms and UP/DOWN skew <= 150ms.

    The original V1 combined a five-second entry window, three distinct
    events, 300ms persistence, and a midpoint move. In practice, the source
    Microprice signal often existed only briefly around the 180-second mark,
    so the paired experiment produced almost no samples. V2 widens only the
    timing/confirmation layer and records a new strategy version.
    """
    from . import microprice_variants as variants

    variants.MICROPRICE_VARIANT_VERSION = MICROPRICE_RELAXED_VERSION
    variants.MICROPRICE_MIN_CONFIRMATIONS = 2
    variants.MICROPRICE_MIN_CONFIRMATION_MS = 150.0
    variants.MICROPRICE_MIN_MIDPOINT_MOVE = 0.0005
    variants.MICROPRICE_MIN_RETAINED_STRENGTH = 0.65
    variants.MICROPRICE_WINDOW_MIN_SECONDS_LEFT = 170.0
    variants.MICROPRICE_WINDOW_MAX_SECONDS_LEFT = 181.0

    # Deliberately unchanged after the three suspicious source losses used
    # books aged 570ms, 810ms, and 571ms.
    variants.MICROPRICE_MAX_BOOK_AGE_MS = 500.0
    variants.MICROPRICE_MAX_BOOK_SKEW_MS = 150.0
