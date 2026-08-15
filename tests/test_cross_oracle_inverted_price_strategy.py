from predict_bot.cross_oracle_strategy_chop_guard_v5 import inverted_price_signal


def test_inverted_price_signal_accepts_80_20_example():
    signal = inverted_price_signal(
        poly_selected_mid=0.80,
        binance_selected_mid=0.20,
        executable_ask=0.22,
    )
    assert signal["triggered"] is True
    assert abs(signal["mirrorError"]) < 1e-12


def test_inverted_price_signal_accepts_90_10_example():
    signal = inverted_price_signal(
        poly_selected_mid=0.90,
        binance_selected_mid=0.10,
        executable_ask=0.12,
    )
    assert signal["triggered"] is True


def test_inverted_price_signal_rejects_large_gap_that_is_not_mirror_inverted():
    signal = inverted_price_signal(
        poly_selected_mid=0.80,
        binance_selected_mid=0.40,
        executable_ask=0.25,
    )
    assert signal["triggered"] is False
    assert signal["cheapBinance"] is False


def test_inverted_price_signal_rejects_expensive_executable_ask():
    signal = inverted_price_signal(
        poly_selected_mid=0.80,
        binance_selected_mid=0.20,
        executable_ask=0.40,
    )
    assert signal["triggered"] is False
    assert signal["executable"] is False


def test_inverted_price_signal_allows_small_mirror_error():
    signal = inverted_price_signal(
        poly_selected_mid=0.85,
        binance_selected_mid=0.12,
        executable_ask=0.15,
    )
    assert signal["triggered"] is True
    assert abs(signal["mirrorError"] - 0.03) < 1e-12
