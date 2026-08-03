from __future__ import annotations

import threading
import time
from http.server import ThreadingHTTPServer

from . import xpair_canary_autopilot_server as base
from .core import BinancePredictionTradingClient, select_binary_market
from .xpair_btc_eth_canary import (
    CanaryStore,
    _plan_details,
    _quote_details,
    build_canary_plan,
    choose_trial,
    quote_equal_share_pair,
)
from .xpair_btc_eth_paper import (
    MarketRef,
    build_trials,
    discover_active_pair,
    fetch_books,
    utc_iso,
)


def monitor_loop() -> None:
    api_key = base.os.environ.get("BINANCE_API_KEY")
    api_secret = base.os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        base.STATE.set_phase("ERROR")
        base.STATE.log("BINANCE_API_KEY and BINANCE_API_SECRET are required", "ERROR")
        return

    client = BinancePredictionTradingClient(api_key, api_secret)
    store = CanaryStore(base.DB_PATH)
    pair: tuple[MarketRef, MarketRef] | None = None
    next_discovery = 0.0
    next_quote_at = 0.0
    last_status = ""
    wallet_address = wallet_id = ""
    try:
        config = base.STATE.config
        wallet_address, wallet_id, available = base.monitoring_preflight(client, config)
        with base.STATE.lock:
            base.STATE.running = True
        base.STATE.set_phase("MONITORING")
        base.STATE.log(
            f"MONITORING_STARTED available={available:.8f} "
            f"required={config.required_balance:.8f} account={config.account_type}"
        )

        while True:
            loop_started = time.monotonic()
            config = base.STATE.config
            try:
                now_ms = client.server_timestamp_ms()
                if pair is not None and now_ms >= max(pair[0].end_ms, pair[1].end_ms):
                    pair = None
                if pair is None and loop_started >= next_discovery:
                    next_discovery = loop_started + config.discovery_interval_seconds
                    pair = discover_active_pair(
                        client,
                        select_binary_market,
                        now_ms=now_ms,
                        tolerance_ms=config.market_time_tolerance_ms,
                    )

                if pair is None:
                    status = "WAITING_FOR_ALIGNED_BTC_ETH_MARKETS"
                else:
                    btc, eth = pair
                    seconds_left = max(
                        0.0, (min(btc.end_ms, eth.end_ms) - now_ms) / 1000.0
                    )
                    key = base.market_key(btc, eth)
                    with base.STATE.lock:
                        base.STATE.latest_market = {
                            "marketKey": key,
                            "btcMarketId": btc.market_id,
                            "ethMarketId": eth.market_id,
                            "secondsLeft": seconds_left,
                            "startMs": min(btc.start_ms, eth.start_ms),
                            "endMs": min(btc.end_ms, eth.end_ms),
                        }
                        attempted_current_market = (
                            base.STATE.last_attempt_market_key == key
                        )

                    inside_window = (
                        config.entry_seconds_left - config.entry_window_seconds
                        <= seconds_left
                        <= config.entry_seconds_left
                    )
                    if attempted_current_market:
                        status = "LIVE_ATTEMPT_RECORDED_WAIT_NEXT_MARKET"
                    elif not inside_window:
                        status = f"WAITING_ENTRY_WINDOW left={seconds_left:.1f}s"
                    elif loop_started < next_quote_at:
                        status = "QUOTE_COOLDOWN"
                    else:
                        next_quote_at = loop_started + config.quote_interval_seconds
                        books = fetch_books(client, btc, eth)
                        capture_ms = client.server_timestamp_ms()
                        trials, _ = build_trials(
                            btc=btc,
                            eth=eth,
                            books=books,
                            total_stake=float(config.pair_budget_usdt),
                            max_total_cost_per_share=float(config.max_total_cost),
                            minimum_filled_shares=1e-8,
                            max_book_age_ms=config.max_book_age_ms,
                            max_book_skew_ms=config.max_book_skew_ms,
                            max_cross_book_skew_ms=config.max_cross_book_skew_ms,
                            now_ms=capture_ms,
                        )
                        chosen = choose_trial(trials, config.selection)
                        if chosen is None:
                            reasons = ", ".join(
                                f"{item['variant']}={item['entry_status']}"
                                for item in trials
                            )
                            status = f"NO_ELIGIBLE_VARIANT {reasons}"
                            with base.STATE.lock:
                                base.STATE.latest_plan = None
                                base.STATE.latest_quote = None
                        else:
                            plan = build_canary_plan(
                                chosen=chosen,
                                btc=btc,
                                eth=eth,
                                max_leg_reprice=config.max_leg_reprice,
                            )
                            with base.STATE.lock:
                                base.STATE.latest_plan = _plan_details(plan)
                                base.STATE.quote_attempts += 1

                            run_id = store.begin(
                                mode="AUTO_MONITOR",
                                status="PLANNED",
                                btc_market_id=btc.market_id,
                                eth_market_id=eth.market_id,
                                pair_budget=config.pair_budget_usdt,
                                plan=plan,
                                details={"plan": _plan_details(plan), "trials": trials},
                            )

                            live_ready = False
                            live_wallet = wallet_address
                            live_wallet_id = wallet_id
                            live_block_reason: str | None = None
                            if base.STATE.is_armed():
                                # Complete the slow safety checks before requesting
                                # short-lived signed quotes. Once both final quotes
                                # pass, placement follows immediately.
                                try:
                                    live_wallet, live_wallet_id, _ = (
                                        base.strict_live_preflight(client, config)
                                    )
                                except Exception as exc:
                                    live_block_reason = str(exc)[:500]
                                else:
                                    live_ready = True

                            try:
                                quotes, quoted_cost = quote_equal_share_pair(
                                    client,
                                    wallet_address=wallet_address,
                                    plan=plan,
                                    pair_budget=config.pair_budget_usdt,
                                    max_total_cost_per_share=config.max_total_cost,
                                    slippage_bps=config.slippage_bps,
                                )
                            except Exception as exc:
                                status = f"QUOTE_REJECTED {str(exc)[:240]}"
                                store.update(
                                    run_id,
                                    status="QUOTE_REJECTED",
                                    message=str(exc),
                                )
                                with base.STATE.lock:
                                    base.STATE.latest_quote = {
                                        "accepted": False,
                                        "marketKey": key,
                                        "error": str(exc)[:500],
                                        "updatedAt": utc_iso(),
                                    }
                            else:
                                details = _quote_details(plan, quotes)
                                store.update(
                                    run_id,
                                    status="AUTO_QUOTE_READY",
                                    quoted_cost=quoted_cost,
                                    details=details,
                                )
                                with base.STATE.lock:
                                    base.STATE.quote_accepts += 1
                                    base.STATE.latest_quote = {
                                        "accepted": True,
                                        "marketKey": key,
                                        "variant": plan.variant,
                                        "costPerShare": float(quoted_cost),
                                        "targetShares": float(plan.target_shares),
                                        "quotes": details["quotes"],
                                        "updatedAt": utc_iso(),
                                        "armed": base.STATE.armed,
                                    }

                                if base.STATE.is_armed() and live_ready:
                                    base.execute_live_attempt(
                                        client=client,
                                        store=store,
                                        config=config,
                                        wallet_address=live_wallet,
                                        wallet_id=live_wallet_id,
                                        btc=btc,
                                        eth=eth,
                                        plan=plan,
                                        quotes=quotes,
                                        quoted_cost=quoted_cost,
                                        run_id=run_id,
                                    )
                                    status = base.STATE.phase
                                elif base.STATE.is_armed() and live_block_reason:
                                    status = (
                                        "ARMED_WAITING_SAFE_WALLET "
                                        f"{live_block_reason[:240]}"
                                    )
                                else:
                                    status = (
                                        f"AUTO_QUOTE_READY cost/share={quoted_cost:.6f}"
                                    )

                base.STATE.set_phase(status.split(" ", 1)[0])
                if status != last_status:
                    level = (
                        "WARN"
                        if status.startswith(("ARMED", "LIVE", "SUBMITTED", "PLACE"))
                        else "INFO"
                    )
                    base.STATE.log(status, level)
                    last_status = status
            except Exception as exc:
                base.STATE.set_phase("MONITOR_ERROR")
                base.STATE.log(
                    f"MONITOR_ERROR {type(exc).__name__}: {str(exc)[:500]}",
                    "ERROR",
                )
                time.sleep(2.0)

            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.0, config.interval_seconds - elapsed))
    finally:
        with base.STATE.lock:
            base.STATE.running = False
        client.close()
        store.close()


def main() -> None:
    base.MonitorConfig().validate()
    threading.Thread(
        target=monitor_loop,
        name="xpair-autopilot-monitor-v2",
        daemon=True,
    ).start()
    server = ThreadingHTTPServer((base.API_HOST, base.API_PORT), base.Handler)
    print(
        f"XPAIR autopilot canary API listening on "
        f"http://{base.API_HOST}:{base.API_PORT}; signed quotes are monitored "
        "continuously and live placement is disarmed"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        base.STATE.disarm("server_shutdown")
        server.server_close()


if __name__ == "__main__":
    main()
