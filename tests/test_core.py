from datetime import datetime, timezone

import httpx
import pytest

from predict_bot.core import (
    ApiHttpError,
    ApiTransportError,
    BinancePredictionClient,
    BinancePredictionTradingClient,
    ProbabilityModel,
    TopOfBook,
    binance_top_of_book,
    decide,
    effective_taker_cost,
    enforce_one_trade,
    parse_time,
    select_binary_market,
    taker_fee,
)


def prediction_http_client(handler):
    pooled = httpx.Client(
        base_url="https://api.binance.com",
        transport=httpx.MockTransport(handler),
    )
    client = BinancePredictionTradingClient(
        "api-key", "api-secret", http_client=pooled
    )
    client._time_offset_ms = 0
    return client, pooled


def test_parse_time_is_utc_aware():
    parsed = parse_time("2026-07-15T22:35:00.000Z")
    assert parsed == datetime(2026, 7, 15, 22, 35, tzinfo=timezone.utc)


def test_binance_token_books_are_read_independently():
    book = binance_top_of_book(
        {
            "updateTimestampMs": 1000,
            "asks": [{"price": "0.62", "size": "100"}],
            "bids": [{"price": "0.61", "size": "80"}],
        },
        {
            "updateTimestampMs": 1004,
            "asks": [{"price": "0.40", "size": "50"}],
            "bids": [{"price": "0.38", "size": "40"}],
        },
        current_timestamp_ms=2000,
    )
    assert book == TopOfBook(
        up_ask=0.62, up_bid=0.61, down_ask=0.40, down_bid=0.38,
        up_ask_size=100, up_bid_size=80, down_ask_size=50, down_bid_size=40,
        book_skew_ms=4,
        up_book_timestamp_ms=1000, down_book_timestamp_ms=1004, book_age_ms=1000,
    )


def test_equal_timestamp_books_still_report_absolute_age():
    levels = {
        "updateTimestampMs": 1000,
        "asks": [{"price": "0.60", "size": "100"}],
        "bids": [{"price": "0.59", "size": "100"}],
    }
    book = binance_top_of_book(levels, levels, current_timestamp_ms=3500)
    assert book.book_skew_ms == 0
    assert book.book_age_ms == 2500


def test_empty_book_never_claims_a_trade():
    result = decide(0.9, TopOfBook(None, None, None, None), 200, 0.03)
    assert result.action == "NO_TRADE"
    assert result.reason == "NO_LIQUIDITY"


def test_edge_includes_fee_buffer():
    book = TopOfBook(up_ask=0.70, up_bid=0.69, down_ask=0.31, down_bid=0.30)
    result = decide(0.80, book, fee_rate_bps=200, min_edge=0.05)
    assert result.action == "PAPER_BUY_UP"
    assert round(result.edge, 3) == 0.094


def test_predict_taker_fee_is_price_sensitive():
    assert taker_fee(shares=10, price=0.20, fee_rate_bps=200) == pytest.approx(0.04)
    assert taker_fee(shares=10, price=0.90, fee_rate_bps=200) == pytest.approx(0.02)
    assert effective_taker_cost(0.90, 200) == pytest.approx(0.902)


def test_probability_moves_with_distance():
    model = ProbabilityModel(fallback_sigma_per_sqrt_second=0.001)
    assert model.up_probability(101, 100, 60) > 0.5
    assert model.up_probability(99, 100, 60) < 0.5


def test_hmac_query_signature_is_deterministic():
    client = BinancePredictionClient("key", "secret")
    signed = client.sign_query({"limit": 5, "timestamp": 123})
    assert signed == (
        "limit=5&timestamp=123&signature="
        "2ef89367a9fcbcd8fcad6afc789290f1d3c6ddf87149c318f028b2109e0b1aaf"
    )


def test_hmac_query_signature_preserves_repeated_redeem_token_ids():
    client = BinancePredictionTradingClient("key", "secret")
    signed = client.sign_query(
        {"tokenIds": ["token-a", "token-b"], "timestamp": 123}
    )

    canonical, signature = signed.rsplit("&signature=", 1)
    assert canonical == "tokenIds=token-a&tokenIds=token-b&timestamp=123"
    assert len(signature) == 64


def test_signed_get_quote_and_place_share_one_persistent_http_client():
    paths = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"success": True})

    client, pooled = prediction_http_client(handler)

    client.signed_get("/read", {"value": "zero"})
    client.signed_post("/quote", {"value": "one"})
    client.signed_post("/place", {"value": "two"})

    assert client.http_client is pooled
    assert paths == ["/read", "/quote", "/place"]


def test_signed_post_transport_failure_is_not_retried_or_leaked():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("contains-sensitive-url", request=request)

    client, _ = prediction_http_client(handler)

    with pytest.raises(ApiTransportError) as raised:
        client.signed_post("/place-order-bundle", {"walletId": "secret-wallet"})

    assert calls == 1
    message = str(raised.value)
    assert "signature" not in message
    assert "secret-wallet" not in message
    assert "api-key" not in message


@pytest.mark.parametrize("status_code", [400, 500])
def test_signed_post_http_error_preserves_status_without_retry(status_code: int):
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"code": -1, "msg": "rejected"})

    client, _ = prediction_http_client(handler)

    with pytest.raises(ApiHttpError) as raised:
        client.signed_post("/place-order-bundle", {"value": "one"})

    assert calls == 1
    assert raised.value.status_code == status_code


def test_owned_prediction_http_client_closes_cleanly():
    client = BinancePredictionTradingClient("key", "secret")
    pooled = client.http_client

    client.close()

    assert pooled.is_closed is True


def test_prediction_sell_quote_sends_sell_limit_parameters(monkeypatch):
    client = BinancePredictionTradingClient("key", "secret")
    captured = {}

    def fake_post(path, params):
        captured.update({"path": path, "params": params})
        return {"quoteId": "sell-quote"}

    monkeypatch.setattr(client, "signed_post", fake_post)
    client.get_quote(
        wallet_address="0xwallet",
        token_id="down-token",
        amount_in_wei="8400000000000000000",
        price_limit="0.15",
        slippage_bps=100,
        fee_rate_bps=200,
        side="SELL",
        order_type="LIMIT",
    )

    assert captured["path"].endswith("/trade/get-quote")
    assert captured["params"]["side"] == "SELL"
    assert captured["params"]["orderType"] == "LIMIT"
    assert captured["params"]["priceLimit"] == "0.15"


def test_prediction_market_order_uses_fok_without_price_limit(monkeypatch):
    client = BinancePredictionTradingClient("key", "secret")
    captured = {}

    def fake_post(path, params):
        captured.update({"path": path, "params": params})
        return {"orderId": "market-sell"}

    monkeypatch.setattr(client, "signed_post", fake_post)
    client.place_market_order(
        wallet_address="0xwallet",
        wallet_id="wallet-1",
        quote_id="sell-quote",
        slippage_bps=100,
        account_type="SPOT",
    )

    assert captured["path"].endswith("/trade/place-order-bundle")
    assert captured["params"]["orderType"] == "MARKET"
    assert captured["params"]["timeInForce"] == "FOK"
    assert "priceLimit" not in captured["params"]


def test_server_timestamp_applies_cached_offset(monkeypatch):
    client = BinancePredictionClient("key", "secret")
    client._time_offset_ms = -1000
    monkeypatch.setattr("predict_bot.core.time.time", lambda: 10.0)
    assert client.server_timestamp_ms() == 9000


def test_selects_yes_as_up_for_binary_crypto_question():
    selected = select_binary_market({"markets": [{
        "marketId": 10,
        "tradingStatus": "OPEN",
        "outcomes": [
            {"name": "YES", "tokenId": "up"},
            {"name": "NO", "tokenId": "down"},
        ],
    }]})
    assert selected["up"]["tokenId"] == "up"
    assert selected["down"]["tokenId"] == "down"


def test_only_one_paper_trade_per_market():
    traded = set()
    signal = decide(0.8, TopOfBook(0.6, 0.59, 0.42, 0.4), 0, 0.05)
    first = enforce_one_trade(signal, 123, traded)
    second = enforce_one_trade(signal, 123, traded)
    assert first.action == "PAPER_BUY_UP"
    assert second.action == "NO_TRADE"
    assert second.reason == "ALREADY_TRADED"


def test_find_market_summary_uses_list_payload_without_detail(monkeypatch):
    client = BinancePredictionClient("key", "secret")
    now = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    topic = {
        "marketTopicId": 123,
        "title": "BTC Up or Down 5m",
        "chartType": "CRYPTO_UP_DOWN",
        "symbol": "BTCUSDT",
        "startDate": now_ms - 1_000,
        "endDate": now_ms + 299_000,
        "variantData": {"startPrice": "65000"},
        "markets": [{
            "marketId": 456,
            "tradingStatus": "OPEN",
            "outcomes": [
                {"name": "UP", "tokenId": "up-token"},
                {"name": "DOWN", "tokenId": "down-token"},
            ],
        }],
    }
    monkeypatch.setattr(
        client,
        "list_markets",
        lambda offset=0, limit=100: {
            "marketTopics": [topic], "hasMore": False, "limit": limit
        },
    )
    monkeypatch.setattr(
        client,
        "market_detail",
        lambda _topic_id: pytest.fail("summary lookup must not call market detail"),
    )

    summary = client.find_market_summary("BTCUSDT", now, max_pages=1)

    assert summary is not None
    assert summary["marketTopicId"] == 123
    assert summary["_selectedMarket"]["market"]["marketId"] == 456


def test_find_market_summary_max_pages_one_never_fetches_second_page(monkeypatch):
    client = BinancePredictionClient("key", "secret")
    calls: list[int] = []

    def empty_page(offset=0, limit=100):
        calls.append(offset)
        return {"marketTopics": [], "hasMore": True, "limit": limit}

    monkeypatch.setattr(client, "list_markets", empty_page)

    assert client.find_market_summary("BTCUSDT", max_pages=1) is None
    assert calls == [0]
