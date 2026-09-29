from __future__ import annotations

from predict_bot import polymarket_btc5m_external_hft_collector_v1 as m


def test_unwrap_current_market_stream_shape():
    typ, payload = m.unwrap_event({
        'topic': 'market',
        'type': 'price_change',
        'payload': {'market': '0xabc', 'priceChanges': [{'tokenId': '1'}]},
    })
    assert typ == 'price_change'
    assert payload['market'] == '0xabc'


def test_unwrap_legacy_market_stream_shape():
    typ, payload = m.unwrap_event({
        'event_type': 'book',
        'asset_id': '1',
        'bids': [{'price': '0.49', 'size': '10'}],
    })
    assert typ == 'book'
    assert payload['asset_id'] == '1'


def test_parse_book_levels_filters_invalid_rows():
    out = m.parse_book_levels([
        {'price': '0.49', 'size': '10'},
        {'price': '0.50', 'size': '0'},
        {'price': '1.0', 'size': '5'},
        {'price': 'bad', 'size': '5'},
    ])
    assert out == {0.49: 10.0}


def test_market_identity_outcome_mapping():
    ident = m.MarketIdentity(
        market_id='1', condition_id='0x1', event_slug='btc-updown-5m-100',
        market_slug=None, question='q', bucket_start_sec=100,
        window_start_ms=100000, window_end_ms=400000,
        up_token_id='UPTOKEN', down_token_id='DOWNTOKEN',
    )
    assert ident.outcome_for('UPTOKEN') == 'UP'
    assert ident.outcome_for('DOWNTOKEN') == 'DOWN'
    assert ident.outcome_for('OTHER') is None


def test_book_summary_reports_depth_and_spread():
    book = {
        'bids': {0.48: 20.0, 0.47: 10.0},
        'asks': {0.51: 15.0, 0.52: 5.0},
        'lastTradePrice': 0.5,
        'tickSize': 0.01,
        'sourceMs': 123,
    }
    s = m.Collector._book_summary(book)
    assert s['bestBid'] == 0.48
    assert s['bestAsk'] == 0.51
    assert abs(s['spread'] - 0.03) < 1e-12
    assert s['bidLevels'] == 2
    assert s['askLevels'] == 2
    assert s['bidDepth'] == 30.0
    assert s['askDepth'] == 20.0
    assert s['topBidDepth'] == 20.0
    assert s['topAskDepth'] == 15.0