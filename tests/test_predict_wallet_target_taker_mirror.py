from predict_bot import predict_wallet_target_taker_mirror as mirror


def book():
    return {
        "data": {
            "asks": [[0.60, 10], [0.61, 20]],
            "bids": [[0.40, 5], [0.39, 20]],
        }
    }


def test_down_asks_are_complements_of_yes_bids() -> None:
    assert mirror.executable_levels(book(), "UP") == [(0.60, 10.0), (0.61, 20.0)]
    assert mirror.executable_levels(book(), "DOWN") == [(0.60, 5.0), (0.61, 20.0)]


def test_share_vwap_walks_depth_and_enforces_minimum_notional() -> None:
    fill = mirror.fill_by_shares([(0.60, 10), (0.61, 20)], 18)
    assert fill["filledShares"] == 18
    assert fill["principalUsdt"] == 10 * 0.60 + 8 * 0.61
    assert fill["levelsConsumed"] == 2
    assert fill["fullyExecutable"] is True

    too_small = mirror.fill_by_shares([(0.10, 10)], 1)
    assert too_small["minimumNotionalMet"] is False
    assert too_small["fullyExecutable"] is False


def test_budget_profile_spends_exact_budget_when_depth_exists() -> None:
    fill = mirror.fill_by_budget([(0.60, 10), (0.61, 20)], 5)
    assert fill["principalUsdt"] == 5
    assert fill["filledShares"] == 5 / 0.60
    assert fill["fullyExecutable"] is True


def test_winner_and_stress_economics_include_200_bps_fee() -> None:
    fill = mirror.fill_by_shares([(0.50, 100)], 10)
    base = mirror.settled_economics(fill, side="UP", winner="UP")
    stress = mirror.settled_economics(fill, side="UP", winner="UP", stress_ticks=1)
    assert base["costUsdt"] == 5.1
    assert base["pnlUsdt"] == 4.9
    assert stress["pnlUsdt"] < base["pnlUsdt"]
