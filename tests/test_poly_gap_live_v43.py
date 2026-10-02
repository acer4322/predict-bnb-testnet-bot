from predict_bot.poly_gap_live_v40 import ImmediateExitCautiousReentryPolyGapLiveEngine
from predict_bot.poly_gap_live_v42 import TakeProfitMarketLockPolyGapLiveEngine
from predict_bot.poly_gap_live_v43 import (
    QuoteTimestampRaceFixedPolyGapLiveEngine,
    select_quote_timestamp_pair,
)


def test_v43_prefers_collector_quote_timestamp_over_generic_last_event() -> None:
    side = {
        "quoteSourceTimestampMs": 1_000,
        "quoteReceivedTimestampMs": 1_120,
        "quoteWsSession": 7,
        "sourceTimestampMs": 2_000,
        "receivedTimestampMs": 2_050,
        "lastEventType": "last_trade_price",
    }
    source, received, origin = select_quote_timestamp_pair(
        side,
        current_ws_session=7,
        fallback_source_ms=900,
        fallback_received_ms=950,
    )
    assert source == 1_000
    assert received == 1_120
    assert origin == "COLLECTOR_QUOTE_TIMESTAMP_V3"


def test_v43_rejects_quote_timestamp_from_old_websocket_session() -> None:
    side = {
        "quoteSourceTimestampMs": 1_000,
        "quoteReceivedTimestampMs": 1_120,
        "quoteWsSession": 6,
    }
    source, received, origin = select_quote_timestamp_pair(
        side,
        current_ws_session=7,
        fallback_source_ms=2_000,
        fallback_received_ms=2_050,
    )
    assert source == 2_000
    assert received == 2_050
    assert origin == "V41_LEGACY_POLL_INFERENCE"


def test_v43_preserves_v42_and_v40_exit_lineage() -> None:
    assert issubclass(
        QuoteTimestampRaceFixedPolyGapLiveEngine,
        TakeProfitMarketLockPolyGapLiveEngine,
    )
    assert (
        QuoteTimestampRaceFixedPolyGapLiveEngine._observe_exit_flip
        is ImmediateExitCautiousReentryPolyGapLiveEngine._observe_exit_flip
    )
    assert (
        QuoteTimestampRaceFixedPolyGapLiveEngine._exit_round
        is ImmediateExitCautiousReentryPolyGapLiveEngine._exit_round
    )
