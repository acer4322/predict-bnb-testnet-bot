from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .core import BinancePredictionTradingClient, select_binary_market
from .xpair_btc_eth_canary import (
    DEFAULT_DB_PATH,
    LIVE_CONFIRM_VALUE,
    MAX_PAIR_BUDGET_USDT,
    CanaryPlan,
    CanaryStore,
    _plan_details,
    _quote_details,
    build_canary_plan,
    choose_trial,
    place_pair,
    quote_equal_share_pair,
    reconcile_orders,
)
from .xpair_btc_eth_canary_cedefi import preflight_wallet
from .xpair_btc_eth_paper import (
    MarketRef,
    build_trials,
    discover_active_pair,
    fetch_books,
    utc_iso,
)

API_HOST = os.environ.get("XPAIR_CANARY_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("XPAIR_CANARY_API_PORT", "8767"))
DB_PATH = Path(os.environ.get("XPAIR_CANARY_DB", DEFAULT_DB_PATH))
MAX_LOG_LINES = 300
TERMINAL_ORDER_STATUSES = {
    "FILLED",
    "REJECTED",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
    "FAILED",
}


@dataclass(frozen=True)
class MonitorConfig:
    selection: str = "BTC_DOWN_ETH_UP"
    pair_budget_usdt: Decimal = Decimal("2.00")
    balance_buffer_usdt: Decimal = Decimal("0.10")
    max_total_cost: Decimal = Decimal("0.98")
    max_leg_reprice: Decimal = Decimal("0.01")
    entry_seconds_left: float = 180.0
    entry_window_seconds: float = 10.0
    max_book_age_ms: float = 2000.0
    max_book_skew_ms: float = 500.0
    max_cross_book_skew_ms: float = 1000.0
    market_time_tolerance_ms: int = 2000
    slippage_bps: int = 100
    account_type: str = "CeDeFi"
    reconcile_seconds: float = 15.0
    interval_seconds: float = 1.0
    discovery_interval_seconds: float = 5.0
    quote_interval_seconds: float = 1.0

    @property
    def required_balance(self) -> Decimal:
        return self.pair_budget_usdt + self.balance_buffer_usdt

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], current: "MonitorConfig"
    ) -> "MonitorConfig":
        def decimal_value(name: str, fallback: Decimal) -> Decimal:
            return Decimal(str(payload.get(name, fallback)))

        candidate = replace(
            current,
            selection=str(payload.get("selection", current.selection)).strip().upper(),
            pair_budget_usdt=decimal_value(
                "pairBudgetUsdt", current.pair_budget_usdt
            ),
            balance_buffer_usdt=decimal_value(
                "balanceBufferUsdt", current.balance_buffer_usdt
            ),
            max_total_cost=decimal_value("maxTotalCost", current.max_total_cost),
            max_leg_reprice=decimal_value(
                "maxLegReprice", current.max_leg_reprice
            ),
            entry_seconds_left=float(
                payload.get("entrySecondsLeft", current.entry_seconds_left)
            ),
            entry_window_seconds=float(
                payload.get("entryWindowSeconds", current.entry_window_seconds)
            ),
            slippage_bps=int(payload.get("slippageBps", current.slippage_bps)),
            account_type="CeDeFi",
        )
        candidate.validate()
        return candidate

    def validate(self) -> None:
        if self.selection not in {
            "BTC_DOWN_ETH_UP",
            "BTC_UP_ETH_DOWN",
            "CHEAPEST_ELIGIBLE",
        }:
            raise ValueError("unsupported XPAIR selection")
        if not Decimal("0.02") <= self.pair_budget_usdt <= MAX_PAIR_BUDGET_USDT:
            raise ValueError("pair budget must be between 0.02 and 3.00 USDT")
        if self.balance_buffer_usdt < 0:
            raise ValueError("balance buffer cannot be negative")
        if not Decimal("0") < self.max_total_cost < Decimal("2"):
            raise ValueError("max total cost must be between 0 and 2")
        if not Decimal("0") <= self.max_leg_reprice <= Decimal("0.05"):
            raise ValueError("max leg reprice must be between 0 and 0.05")
        if not 0 < self.entry_window_seconds < self.entry_seconds_left:
            raise ValueError("entry window must be positive and below entry time")
        if not 0 <= self.slippage_bps <= 500:
            raise ValueError("slippage bps must be between 0 and 500")
        if not 0.25 <= self.quote_interval_seconds <= 10:
            raise ValueError("quote interval must be between 0.25 and 10 seconds")


class AutopilotState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.config = MonitorConfig()
        self.running = False
        self.phase = "STARTING"
        self.started_at = utc_iso()
        self.updated_at = self.started_at
        self.last_error: str | None = None
        self.last_warning: str | None = None
        self.armed = False
        self.armed_at: str | None = None
        self.armed_generation = 0
        self.last_attempt_market_key: str | None = None
        self.latest_market: dict[str, Any] | None = None
        self.latest_plan: dict[str, Any] | None = None
        self.latest_quote: dict[str, Any] | None = None
        self.quote_attempts = 0
        self.quote_accepts = 0
        self.live_attempts = 0
        self.logs: deque[dict[str, str]] = deque(maxlen=MAX_LOG_LINES)

    def log(self, message: str, level: str = "INFO") -> None:
        with self.lock:
            self.logs.append(
                {"timestamp": utc_iso(), "level": level, "message": message[:1000]}
            )
            self.updated_at = utc_iso()
            if level == "ERROR":
                self.last_error = message[:1000]
            elif level == "WARN":
                self.last_warning = message[:1000]

    def set_phase(self, phase: str) -> None:
        with self.lock:
            self.phase = phase
            self.updated_at = utc_iso()

    def update_config(self, payload: dict[str, Any]) -> MonitorConfig:
        with self.lock:
            self.config = MonitorConfig.from_payload(payload, self.config)
            config = self.config
            self.updated_at = utc_iso()
        self.log(
            "CONFIG_UPDATED "
            f"selection={config.selection} budget={config.pair_budget_usdt} "
            f"max_cost={config.max_total_cost}"
        )
        return config

    def arm(self, payload: dict[str, Any]) -> MonitorConfig:
        config = self.update_config(payload)
        with self.lock:
            if self.armed:
                raise RuntimeError("XPAIR is already armed for one live attempt")
            self.armed = True
            self.armed_at = utc_iso()
            self.armed_generation += 1
            self.updated_at = utc_iso()
        self.log(
            "LIVE_ARMED next eligible accepted quote will attempt one non-atomic pair",
            "WARN",
        )
        return config

    def disarm(self, reason: str) -> None:
        with self.lock:
            was_armed = self.armed
            self.armed = False
            self.armed_at = None
            self.updated_at = utc_iso()
        if was_armed:
            self.log(f"LIVE_DISARMED reason={reason}", "WARN")

    def is_armed(self) -> bool:
        with self.lock:
            return self.armed

    def consume_arm(self, market_key: str) -> bool:
        with self.lock:
            if not self.armed:
                return False
            self.armed = False
            self.armed_at = None
            self.last_attempt_market_key = market_key
            self.live_attempts += 1
            self.updated_at = utc_iso()
            return True

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            config = self.config
            return {
                "running": self.running,
                "phase": self.phase,
                "startedAt": self.started_at,
                "updatedAt": self.updated_at,
                "lastError": self.last_error,
                "lastWarning": self.last_warning,
                "armed": self.armed,
                "armedAt": self.armed_at,
                "armedGeneration": self.armed_generation,
                "lastAttemptMarketKey": self.last_attempt_market_key,
                "latestMarket": self.latest_market,
                "latestPlan": self.latest_plan,
                "latestQuote": self.latest_quote,
                "quoteAttempts": self.quote_attempts,
                "quoteAccepts": self.quote_accepts,
                "liveAttempts": self.live_attempts,
                "config": config_payload(config),
                "logs": list(self.logs),
            }


STATE = AutopilotState()


def config_payload(config: MonitorConfig) -> dict[str, Any]:
    return {
        "selection": config.selection,
        "pairBudgetUsdt": float(config.pair_budget_usdt),
        "balanceBufferUsdt": float(config.balance_buffer_usdt),
        "requiredBalanceUsdt": float(config.required_balance),
        "maxTotalCost": float(config.max_total_cost),
        "maxLegReprice": float(config.max_leg_reprice),
        "entrySecondsLeft": config.entry_seconds_left,
        "entryWindowSeconds": config.entry_window_seconds,
        "slippageBps": config.slippage_bps,
        "accountType": config.account_type,
        "quoteIntervalSeconds": config.quote_interval_seconds,
    }


def market_key(btc: MarketRef, eth: MarketRef) -> str:
    return f"{btc.market_id}:{eth.market_id}"


def enrich_placements(
    plan: CanaryPlan, results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {**result, "symbol": leg.symbol, "side": leg.side}
        for leg, result in zip(plan.legs, results)
    ]


def recent_runs() -> list[dict[str, Any]]:
    try:
        store = CanaryStore(DB_PATH)
        try:
            rows = store.recent()
        finally:
            store.close()
        result: list[dict[str, Any]] = []
        for raw in rows:
            item = dict(raw)
            try:
                details = json.loads(str(item.pop("details_json", "{}") or "{}"))
            except json.JSONDecodeError:
                details = {}
            item["details"] = details
            placements = details.get("placements") if isinstance(details, dict) else []
            orders = details.get("orders") if isinstance(details, dict) else {}
            placements = placements if isinstance(placements, list) else []
            orders = orders if isinstance(orders, dict) else {}
            for placement in placements:
                if not isinstance(placement, dict):
                    continue
                symbol = str(placement.get("symbol") or "")
                prefix = "btc" if symbol == "BTC" else "eth" if symbol == "ETH" else ""
                if not prefix:
                    continue
                order_id = str(placement.get("orderId") or "")
                exchange = orders.get(order_id) if order_id else None
                item[f"{prefix}_order_id"] = order_id or None
                item[f"{prefix}_order_status"] = str(
                    (exchange.get("status") if isinstance(exchange, dict) else None)
                    or placement.get("status")
                    or "UNKNOWN"
                ).upper()
            result.append(item)
        return result
    except Exception as exc:
        return [{"id": -1, "status": "LEDGER_ERROR", "message": str(exc)[:500]}]


def strict_live_preflight(
    client: BinancePredictionTradingClient, config: MonitorConfig
) -> tuple[str, str, Decimal]:
    return preflight_wallet(
        client,
        account_type=config.account_type,
        required_balance=config.required_balance,
        allow_existing_exposure=False,
    )


def monitoring_preflight(
    client: BinancePredictionTradingClient, config: MonitorConfig
) -> tuple[str, str, Decimal]:
    return preflight_wallet(
        client,
        account_type=config.account_type,
        required_balance=config.required_balance,
        allow_existing_exposure=True,
    )


def execute_live_attempt(
    *,
    client: BinancePredictionTradingClient,
    store: CanaryStore,
    config: MonitorConfig,
    wallet_address: str,
    wallet_id: str,
    btc: MarketRef,
    eth: MarketRef,
    plan: CanaryPlan,
    quotes: list[dict[str, Any]],
    quoted_cost: Decimal,
    run_id: int,
) -> None:
    key = market_key(btc, eth)
    if not STATE.consume_arm(key):
        return
    STATE.set_phase("PLACE_ATTEMPTED")
    STATE.log(
        f"PLACE_ATTEMPTED market={key} variant={plan.variant} "
        f"cost/share={quoted_cost:.6f}",
        "WARN",
    )
    quote_details = _quote_details(plan, quotes)
    store.update(run_id, status="PLACE_ATTEMPTED", quoted_cost=quoted_cost, details=quote_details)
    results = enrich_placements(
        plan,
        place_pair(
            client,
            wallet_address=wallet_address,
            wallet_id=wallet_id,
            account_type=config.account_type,
            slippage_bps=config.slippage_bps,
            plan=plan,
            quotes=quotes,
        ),
    )
    placement_details = {**quote_details, "placements": results}
    if not all(item.get("ok") for item in results):
        store.update(
            run_id,
            status="PLACEMENT_INCOMPLETE_MANUAL_RECONCILE",
            details=placement_details,
            message="one or both placements were rejected or ambiguous",
        )
        STATE.set_phase("PLACEMENT_INCOMPLETE_MANUAL_RECONCILE")
        STATE.log(
            "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE: do not re-arm before manual reconciliation",
            "ERROR",
        )
        return

    order_ids = {str(item["orderId"]) for item in results}
    store.update(run_id, status="SUBMITTED_BOTH", details=placement_details)
    STATE.set_phase("SUBMITTED_BOTH")
    STATE.log("SUBMITTED_BOTH: " + ", ".join(sorted(order_ids)), "WARN")
    found = reconcile_orders(
        client,
        wallet_address=wallet_address,
        order_ids=order_ids,
        seconds=config.reconcile_seconds,
    )
    statuses = [
        str(found.get(str(item["orderId"]), {}).get("status") or "NOT_FOUND").upper()
        for item in results
    ]
    filled = sum(item == "FILLED" for item in statuses)
    final_status = (
        "FILLED_BOTH"
        if filled == 2
        else "ONE_SIDED_FILL_ALERT"
        if filled == 1
        else "SUBMITTED_NOT_BOTH_FILLED"
    )
    store.update(
        run_id,
        status=final_status,
        details={**placement_details, "orders": found},
        message="/".join(statuses),
    )
    STATE.set_phase(final_status)
    STATE.log(f"{final_status}: BTC={statuses[0]} ETH={statuses[1]}", "WARN" if final_status == "FILLED_BOTH" else "ERROR")


def monitor_loop() -> None:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        STATE.set_phase("ERROR")
        STATE.log("BINANCE_API_KEY and BINANCE_API_SECRET are required", "ERROR")
        return

    client = BinancePredictionTradingClient(api_key, api_secret)
    store = CanaryStore(DB_PATH)
    pair: tuple[MarketRef, MarketRef] | None = None
    next_discovery = 0.0
    next_quote_at = 0.0
    last_status = ""
    wallet_address = wallet_id = ""
    try:
        config = STATE.config
        wallet_address, wallet_id, available = monitoring_preflight(client, config)
        STATE.running = True
        STATE.set_phase("MONITORING")
        STATE.log(
            f"MONITORING_STARTED available={available:.8f} "
            f"required={config.required_balance:.8f} account={config.account_type}"
        )
        while True:
            loop_started = time.monotonic()
            config = STATE.config
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
                    key = market_key(btc, eth)
                    with STATE.lock:
                        STATE.latest_market = {
                            "marketKey": key,
                            "btcMarketId": btc.market_id,
                            "ethMarketId": eth.market_id,
                            "secondsLeft": seconds_left,
                            "startMs": min(btc.start_ms, eth.start_ms),
                            "endMs": min(btc.end_ms, eth.end_ms),
                        }
                    inside_window = (
                        config.entry_seconds_left - config.entry_window_seconds
                        <= seconds_left
                        <= config.entry_seconds_left
                    )
                    if not inside_window:
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
                                f"{item['variant']}={item['entry_status']}" for item in trials
                            )
                            status = f"NO_ELIGIBLE_VARIANT {reasons}"
                            with STATE.lock:
                                STATE.latest_plan = None
                                STATE.latest_quote = None
                        else:
                            plan = build_canary_plan(
                                chosen=chosen,
                                btc=btc,
                                eth=eth,
                                max_leg_reprice=config.max_leg_reprice,
                            )
                            with STATE.lock:
                                STATE.latest_plan = _plan_details(plan)
                                STATE.quote_attempts += 1
                            run_id = store.begin(
                                mode="AUTO_MONITOR",
                                status="PLANNED",
                                btc_market_id=btc.market_id,
                                eth_market_id=eth.market_id,
                                pair_budget=config.pair_budget_usdt,
                                plan=plan,
                                details={"plan": _plan_details(plan), "trials": trials},
                            )
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
                                store.update(run_id, status="QUOTE_REJECTED", message=str(exc))
                                with STATE.lock:
                                    STATE.latest_quote = {
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
                                with STATE.lock:
                                    STATE.quote_accepts += 1
                                    STATE.latest_quote = {
                                        "accepted": True,
                                        "marketKey": key,
                                        "variant": plan.variant,
                                        "costPerShare": float(quoted_cost),
                                        "targetShares": float(plan.target_shares),
                                        "quotes": details["quotes"],
                                        "updatedAt": utc_iso(),
                                        "armed": STATE.armed,
                                    }
                                status = f"AUTO_QUOTE_READY cost/share={quoted_cost:.6f}"
                                if STATE.is_armed():
                                    try:
                                        live_wallet, live_wallet_id, _ = strict_live_preflight(
                                            client, config
                                        )
                                    except Exception as exc:
                                        status = f"ARMED_WAITING_SAFE_WALLET {str(exc)[:240]}"
                                    else:
                                        execute_live_attempt(
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
                                        status = STATE.phase

                STATE.set_phase(status.split(" ", 1)[0])
                if status != last_status:
                    level = "WARN" if status.startswith(("ARMED", "LIVE", "SUBMITTED")) else "INFO"
                    STATE.log(status, level)
                    last_status = status
            except Exception as exc:
                STATE.set_phase("MONITOR_ERROR")
                STATE.log(f"MONITOR_ERROR {type(exc).__name__}: {str(exc)[:500]}", "ERROR")
                time.sleep(2.0)

            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.0, config.interval_seconds - elapsed))
    finally:
        STATE.running = False
        client.close()
        store.close()


def state_payload() -> dict[str, Any]:
    return {
        "strategy": "XPAIR_BTC_ETH_AUTOPILOT_CANARY",
        "nonAtomic": True,
        "defaults": config_payload(STATE.config),
        "policy": {
            "alwaysOnSignedQuoteMonitor": True,
            "liveRequiresExplicitArm": True,
            "onePlacementAttemptPerArm": True,
            "armSurvivesRejectedQuotes": True,
            "armConsumedBeforePlacement": True,
            "automaticCancel": False,
            "automaticUnwind": False,
            "retryPlacement": False,
            "liveConfirmationPhrase": LIVE_CONFIRM_VALUE,
            "otherLiveExecutorMustBeStopped": True,
        },
        "runtime": STATE.snapshot(),
        "recentRuns": recent_runs(),
        "updatedAt": utc_iso(),
    }


def origin_is_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


class Handler(BaseHTTPRequestHandler):
    server_version = "BTC5MLabXPairAutopilot/1.0"

    def _send_headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin")
        if origin and origin_is_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-BTC-Lab-XPair-Live",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        self._send_headers(status)
        self.wfile.write(
            json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str).encode(
                "utf-8"
            )
        )

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64_000:
            raise ValueError("request body is missing or too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_OPTIONS(self) -> None:
        if not origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        self._send_headers(204)

    def do_GET(self) -> None:
        if not origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self.respond(200, {"status": "ok", "running": STATE.running})
        elif path == "/api/xpair-canary":
            self.respond(200, state_payload())
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        path = urlsplit(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/xpair-canary/config":
                STATE.update_config(payload)
            elif path == "/api/xpair-canary/arm":
                if self.headers.get("X-BTC-Lab-XPair-Live") != "confirmed":
                    raise ValueError("live arm request is missing confirmation header")
                if str(payload.get("confirmation") or "") != LIVE_CONFIRM_VALUE:
                    raise ValueError("live confirmation phrase is incorrect")
                STATE.arm(payload)
            elif path == "/api/xpair-canary/disarm":
                STATE.disarm("operator")
            else:
                self.respond(404, {"error": "not found"})
                return
            self.respond(200, state_payload())
        except RuntimeError as exc:
            self.respond(409, {"error": str(exc), **state_payload()})
        except (ValueError, ArithmeticError) as exc:
            self.respond(400, {"error": str(exc), **state_payload()})
        except Exception as exc:
            self.respond(500, {"error": str(exc)[:500], **state_payload()})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> None:
    MonitorConfig().validate()
    threading.Thread(
        target=monitor_loop,
        name="xpair-autopilot-monitor",
        daemon=True,
    ).start()
    server = ThreadingHTTPServer((API_HOST, API_PORT), Handler)
    print(
        f"XPAIR autopilot canary API listening on http://{API_HOST}:{API_PORT}; "
        "signed quotes are monitored continuously and live placement is disarmed"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STATE.disarm("server_shutdown")
        server.server_close()


if __name__ == "__main__":
    main()
