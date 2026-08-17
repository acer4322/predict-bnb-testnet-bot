from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Sequence

from .core import (
    ApiHttpError,
    ApiTransportError,
    BinancePredictionClient,
    BinancePredictionTradingClient,
    select_binary_market,
)
from .xpair_btc_eth_paper import (
    MarketRef,
    build_trials,
    discover_active_pair,
    entry_fee,
    fetch_books,
    utc_iso,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "xpair_btc_eth_canary.db"
STRATEGY_NAME = "XPAIR_BTC_ETH_LIVE_CANARY"
LIVE_CONFIRM_ENV = "XPAIR_LIVE_CONFIRM"
LIVE_CONFIRM_VALUE = "I_ACCEPT_NON_ATOMIC_TWO_LEG_RISK"
MIN_LEG_STAKE_USDT = Decimal("0.01")
DEFAULT_PAIR_BUDGET_USDT = Decimal("2.00")
MAX_PAIR_BUDGET_USDT = Decimal("3.00")
DEFAULT_BALANCE_BUFFER_USDT = Decimal("0.10")
QUOTE_MIN_CAPACITY = Decimal("0.95")
QUOTE_MAX_SHARE_MISMATCH = Decimal("0.0025")
QUOTE_MIN_EXPIRY_MS = 1_500
TERMINAL_ORDER_STATUSES = {
    "FILLED", "REJECTED", "CANCELED", "CANCELLED", "EXPIRED", "FAILED"
}


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _float(value: Any) -> float | None:
    result = _decimal(value)
    return float(result) if result is not None else None


def _wei(value: Decimal) -> int:
    if value <= 0:
        raise ValueError("amount must be positive")
    return int((value * Decimal(10**18)).to_integral_value(rounding=ROUND_DOWN))


def _from_wei(value: Any) -> Decimal:
    return Decimal(int(value)) / Decimal(10**18)


def _price_text(value: Decimal) -> str:
    if not Decimal("0") < value < Decimal("1"):
        raise ValueError("price limit must be between 0 and 1")
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class CanaryLeg:
    symbol: str
    side: str
    market_id: int
    token_id: str
    fee_bps: int
    signal_price: Decimal
    price_limit: Decimal
    requested_stake: Decimal


@dataclass(frozen=True)
class CanaryPlan:
    variant: str
    btc_market: MarketRef
    eth_market: MarketRef
    cost_per_share: Decimal
    target_shares: Decimal
    legs: tuple[CanaryLeg, CanaryLeg]


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, MarketRef):
        return asdict(value)
    raise TypeError(type(value).__name__)


class CanaryStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS canary_runs(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   strategy TEXT NOT NULL,
                   mode TEXT NOT NULL,
                   status TEXT NOT NULL,
                   btc_market_id INTEGER NOT NULL,
                   eth_market_id INTEGER NOT NULL,
                   variant TEXT,
                   pair_budget_usdt REAL NOT NULL,
                   modeled_cost_per_share REAL,
                   quoted_cost_per_share REAL,
                   details_json TEXT NOT NULL DEFAULT '{}',
                   message TEXT,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   UNIQUE(btc_market_id, eth_market_id)
               )"""
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def has_round(self, btc_market_id: int, eth_market_id: int) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM canary_runs WHERE btc_market_id=? AND eth_market_id=?",
            (int(btc_market_id), int(eth_market_id)),
        ).fetchone()
        return row is not None

    def begin(
        self,
        *,
        mode: str,
        status: str,
        btc_market_id: int,
        eth_market_id: int,
        pair_budget: Decimal,
        plan: CanaryPlan | None = None,
        details: dict[str, Any] | None = None,
        message: str = "",
    ) -> int:
        now = utc_iso()
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO canary_runs(
                   strategy, mode, status, btc_market_id, eth_market_id,
                   variant, pair_budget_usdt, modeled_cost_per_share,
                   details_json, message, created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                STRATEGY_NAME,
                mode,
                status,
                int(btc_market_id),
                int(eth_market_id),
                plan.variant if plan else None,
                float(pair_budget),
                float(plan.cost_per_share) if plan else None,
                json.dumps(details or {}, default=_json_default, sort_keys=True),
                message[:500],
                now,
                now,
            ),
        )
        if cursor.rowcount:
            run_id = int(cursor.lastrowid)
        else:
            row = self.db.execute(
                """SELECT id FROM canary_runs
                    WHERE btc_market_id=? AND eth_market_id=? LIMIT 1""",
                (int(btc_market_id), int(eth_market_id)),
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to persist canary run")
            run_id = int(row["id"])
        self.db.commit()
        return run_id

    def update(
        self,
        run_id: int,
        *,
        status: str,
        quoted_cost: Decimal | None = None,
        details: dict[str, Any] | None = None,
        message: str = "",
    ) -> None:
        self.db.execute(
            """UPDATE canary_runs
                  SET status=?, quoted_cost_per_share=COALESCE(?, quoted_cost_per_share),
                      details_json=CASE WHEN ? IS NULL THEN details_json ELSE ? END,
                      message=?, updated_at=?
                WHERE id=?""",
            (
                status,
                float(quoted_cost) if quoted_cost is not None else None,
                None if details is None else 1,
                json.dumps(details, default=_json_default, sort_keys=True)
                if details is not None else None,
                message[:500],
                utc_iso(),
                int(run_id),
            ),
        )
        self.db.commit()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM canary_runs ORDER BY id DESC LIMIT ?",
            (max(1, min(100, int(limit))),),
        ).fetchall()
        return [dict(row) for row in rows]


def choose_trial(
    trials: Sequence[dict[str, Any]], selection: str
) -> dict[str, Any] | None:
    eligible = [
        trial
        for trial in trials
        if trial.get("eligible") is True
        and _decimal(trial.get("cost_per_share")) is not None
    ]
    if selection != "CHEAPEST_ELIGIBLE":
        eligible = [trial for trial in eligible if trial.get("variant") == selection]
    return min(eligible, key=lambda item: float(item["cost_per_share"]), default=None)


def build_canary_plan(
    *,
    chosen: dict[str, Any],
    btc: MarketRef,
    eth: MarketRef,
    max_leg_reprice: Decimal,
) -> CanaryPlan:
    shares = _decimal(chosen.get("filled_shares"))
    btc_vwap = _decimal(chosen.get("btc_vwap"))
    eth_vwap = _decimal(chosen.get("eth_vwap"))
    cost_per_share = _decimal(chosen.get("cost_per_share"))
    if any(value is None for value in (shares, btc_vwap, eth_vwap, cost_per_share)):
        raise ValueError("eligible paper trial is missing sizing fields")
    assert shares is not None and btc_vwap is not None
    assert eth_vwap is not None and cost_per_share is not None
    if shares <= 0:
        raise ValueError("target shares must be positive")

    def make_leg(
        market: MarketRef, symbol: str, side: str, vwap: Decimal
    ) -> CanaryLeg:
        stake = (shares * vwap).quantize(
            Decimal("0.000000000000000001"), rounding=ROUND_DOWN
        )
        if stake < MIN_LEG_STAKE_USDT:
            raise ValueError(
                f"{symbol} equal-share stake {stake} is below local 0.01 USDT floor"
            )
        return CanaryLeg(
            symbol=symbol,
            side=side,
            market_id=market.market_id,
            token_id=market.up_token_id if side == "UP" else market.down_token_id,
            fee_bps=market.fee_bps,
            signal_price=vwap,
            price_limit=min(Decimal("0.99"), vwap + max_leg_reprice),
            requested_stake=stake,
        )

    btc_side = str(chosen["btc_side"]).upper()
    eth_side = str(chosen["eth_side"]).upper()
    return CanaryPlan(
        variant=str(chosen["variant"]),
        btc_market=btc,
        eth_market=eth,
        cost_per_share=cost_per_share,
        target_shares=shares,
        legs=(
            make_leg(btc, "BTC", btc_side, btc_vwap),
            make_leg(eth, "ETH", eth_side, eth_vwap),
        ),
    )


def _validate_quote(
    quote: dict[str, Any],
    *,
    leg: CanaryLeg,
    requested_amount_wei: int,
    server_now_ms: int,
) -> None:
    if not quote.get("quoteId"):
        raise ValueError(f"{leg.symbol} quote has no quoteId")
    if str(quote.get("side") or "BUY").upper() != "BUY":
        raise ValueError(f"{leg.symbol} quote changed side")
    if str(quote.get("orderType") or "LIMIT").upper() != "LIMIT":
        raise ValueError(f"{leg.symbol} quote changed order type")
    if quote.get("tokenId") is not None and str(quote["tokenId"]) != leg.token_id:
        raise ValueError(f"{leg.symbol} quote token mismatch")
    amount_in = _decimal(quote.get("amountIn"))
    amount_out = _decimal(quote.get("amountOut"))
    average = _decimal(quote.get("averagePrice"))
    if amount_in is None or not Decimal("0") < amount_in <= requested_amount_wei:
        raise ValueError(f"{leg.symbol} quote amountIn is invalid")
    if amount_out is None or amount_out <= 0:
        raise ValueError(f"{leg.symbol} quote amountOut is invalid")
    if average is None or average <= 0 or average > leg.price_limit + Decimal("1e-8"):
        raise ValueError(
            f"{leg.symbol} quote average price exceeds {_price_text(leg.price_limit)}"
        )
    expiry = int(quote.get("expireAt") or 0)
    if expiry and expiry < server_now_ms + QUOTE_MIN_EXPIRY_MS:
        raise ValueError(f"{leg.symbol} quote expires too soon")


def _request_quote(
    client: BinancePredictionTradingClient,
    wallet_address: str,
    leg: CanaryLeg,
    amount_in_wei: int,
    slippage_bps: int,
) -> dict[str, Any]:
    quote = client.get_quote(
        wallet_address=wallet_address,
        token_id=leg.token_id,
        amount_in_wei=str(amount_in_wei),
        price_limit=_price_text(leg.price_limit),
        slippage_bps=slippage_bps,
        fee_rate_bps=leg.fee_bps,
        funding_source="MPC",
        side="BUY",
        order_type="LIMIT",
    )
    _validate_quote(
        quote,
        leg=leg,
        requested_amount_wei=amount_in_wei,
        server_now_ms=client.server_timestamp_ms(),
    )
    return quote


def quote_equal_share_pair(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    plan: CanaryPlan,
    pair_budget: Decimal,
    max_total_cost_per_share: Decimal,
    slippage_bps: int,
) -> tuple[list[dict[str, Any]], Decimal]:
    requested = [_wei(leg.requested_stake) for leg in plan.legs]

    def quote_all(amounts: Sequence[int]) -> list[dict[str, Any]]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    _request_quote,
                    client,
                    wallet_address,
                    leg,
                    amount,
                    slippage_bps,
                )
                for leg, amount in zip(plan.legs, amounts)
            ]
            return [future.result() for future in futures]

    initial = quote_all(requested)
    initial_inputs = [Decimal(int(item["amountIn"])) for item in initial]
    initial_outputs = [Decimal(int(item["amountOut"])) for item in initial]
    capacity = min(
        quoted / requested_amount
        for quoted, requested_amount in zip(initial_inputs, requested)
    )
    if capacity < QUOTE_MIN_CAPACITY:
        raise ValueError(f"signed quote input capacity {capacity:.1%} is below 95%")

    target_output = min(initial_outputs)
    revised = [
        int((amount * target_output / output).to_integral_value(rounding=ROUND_DOWN))
        for amount, output in zip(initial_inputs, initial_outputs)
    ]
    if min(revised) < _wei(MIN_LEG_STAKE_USDT):
        raise ValueError("equal-share requote leaves one leg below 0.01 USDT")

    final = quote_all(revised)
    outputs = [Decimal(int(item["amountOut"])) for item in final]
    inputs = [Decimal(int(item["amountIn"])) for item in final]
    matched_output = min(outputs)
    if matched_output <= 0:
        raise ValueError("final signed quote shares are invalid")
    mismatch = (max(outputs) - matched_output) / matched_output
    if mismatch > QUOTE_MAX_SHARE_MISMATCH:
        raise ValueError(
            f"final signed share mismatch {mismatch:.3%} exceeds 0.25%"
        )

    total_input = sum((_from_wei(value) for value in inputs), Decimal("0"))
    if total_input > pair_budget + Decimal("1e-8"):
        raise ValueError(
            f"final signed quotes require {total_input:.8f} USDT, above pair budget"
        )
    shares = _from_wei(matched_output)
    averages = [Decimal(str(item["averagePrice"])) for item in final]
    fees = sum(
        (
            Decimal(str(entry_fee(1.0, float(price), leg.fee_bps)))
            for price, leg in zip(averages, plan.legs)
        ),
        Decimal("0"),
    )
    cost_per_share = total_input / shares + fees
    if cost_per_share > max_total_cost_per_share + Decimal("1e-8"):
        raise ValueError(
            f"final signed cost/share {cost_per_share:.6f} exceeds "
            f"{max_total_cost_per_share:.6f}"
        )
    return final, cost_per_share


def preflight_wallet(
    client: BinancePredictionTradingClient,
    *,
    account_type: str,
    required_balance: Decimal,
) -> tuple[str, str, Decimal]:
    wallets = client.wallets().get("wallets") or []
    if len(wallets) != 1:
        raise RuntimeError(f"expected one Prediction wallet, got {len(wallets)}")
    wallet_address = str(wallets[0].get("walletAddress") or "")
    wallet_id = str(wallets[0].get("walletId") or "")
    if not wallet_address or not wallet_id:
        raise RuntimeError("Prediction wallet metadata is incomplete")
    client.quota_status()

    balance_payload = client.payment_option_balances()
    enabled = [
        item for item in (balance_payload.get("items") or [])
        if item.get("enabled") is True
    ]
    matching = [
        item for item in enabled
        if str(item.get("accountType") or "").upper() == account_type
    ]
    available = sum(
        (
            _decimal(item.get("availableBalanceDisplay")) or Decimal("0")
            for item in (matching or enabled)
        ),
        Decimal("0"),
    )
    if available < required_balance:
        raise RuntimeError(
            f"enabled Prediction balance {available:.8f} is below "
            f"required {required_balance:.8f} USDT"
        )
    if client.active_orders(wallet_address, limit=100).get("orders") or []:
        raise RuntimeError(
            "existing active Prediction orders detected; stop the other live executor"
        )
    portfolio = client.portfolio(wallet_address, activeOnly=True)
    if int(portfolio.get("activePositionsCount") or 0) > 0:
        raise RuntimeError(
            "existing active Prediction positions detected; wait for settlement"
        )
    return wallet_address, wallet_id, available


def _place_leg(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    wallet_id: str,
    account_type: str,
    slippage_bps: int,
    leg: CanaryLeg,
    quote: dict[str, Any],
) -> dict[str, Any]:
    try:
        placed = client.place_limit_order(
            wallet_address=wallet_address,
            wallet_id=wallet_id,
            quote_id=str(quote["quoteId"]),
            price_limit=_price_text(leg.price_limit),
            slippage_bps=slippage_bps,
            account_type=account_type,
            funding_source="MPC",
        )
        order_id = str(placed.get("orderId") or "")
        if not order_id:
            raise ApiTransportError("placement response did not contain orderId")
        return {"ok": True, "status": "SUBMITTED", "orderId": order_id}
    except Exception as exc:
        ambiguous = isinstance(exc, ApiTransportError) or (
            isinstance(exc, ApiHttpError) and exc.status_code >= 500
        )
        return {
            "ok": False,
            "status": "AMBIGUOUS" if ambiguous else "REJECTED",
            "error": str(exc)[:500],
        }


def place_pair(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    wallet_id: str,
    account_type: str,
    slippage_bps: int,
    plan: CanaryPlan,
    quotes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _place_leg,
                client,
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                account_type=account_type,
                slippage_bps=slippage_bps,
                leg=leg,
                quote=quote,
            )
            for leg, quote in zip(plan.legs, quotes)
        ]
        return [future.result() for future in futures]


def reconcile_orders(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    order_ids: set[str],
    seconds: float,
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + max(0.0, float(seconds))
    found: dict[str, dict[str, Any]] = {}
    while time.monotonic() <= deadline:
        for order in client.order_history(wallet_address, limit=100).get("orders") or []:
            order_id = str(order.get("orderId") or "")
            if order_id in order_ids:
                found[order_id] = dict(order)
        if order_ids.issubset(found) and all(
            str(found[item].get("status") or "").upper() in TERMINAL_ORDER_STATUSES
            for item in order_ids
        ):
            break
        time.sleep(1.0)
    return found


def _plan_details(plan: CanaryPlan) -> dict[str, Any]:
    return {
        "variant": plan.variant,
        "targetShares": str(plan.target_shares),
        "modeledCostPerShare": str(plan.cost_per_share),
        "legs": [asdict(leg) for leg in plan.legs],
    }


def _quote_details(
    plan: CanaryPlan, quotes: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "plan": _plan_details(plan),
        "quotes": [
            {
                "symbol": leg.symbol,
                "averagePrice": item.get("averagePrice"),
                "amountIn": item.get("amountIn"),
                "amountOut": item.get("amountOut"),
                "expireAt": item.get("expireAt"),
            }
            for leg, item in zip(plan.legs, quotes)
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot BTC/ETH opposite-side execution canary. Dry-run by default; "
            "live placement is non-atomic and requires two explicit unlocks."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--mode", choices=("dry-run", "quote-only", "live"), default="dry-run"
    )
    parser.add_argument(
        "--selection",
        choices=("BTC_DOWN_ETH_UP", "BTC_UP_ETH_DOWN", "CHEAPEST_ELIGIBLE"),
        default="BTC_DOWN_ETH_UP",
    )
    parser.add_argument(
        "--pair-budget-usdt", type=Decimal, default=DEFAULT_PAIR_BUDGET_USDT
    )
    parser.add_argument(
        "--balance-buffer-usdt", type=Decimal,
        default=DEFAULT_BALANCE_BUFFER_USDT,
    )
    parser.add_argument("--max-total-cost", type=Decimal, default=Decimal("0.98"))
    parser.add_argument("--max-leg-reprice", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--entry-seconds-left", type=float, default=180.0)
    parser.add_argument("--entry-window-seconds", type=float, default=10.0)
    parser.add_argument("--max-book-age-ms", type=float, default=2000.0)
    parser.add_argument("--max-book-skew-ms", type=float, default=500.0)
    parser.add_argument("--max-cross-book-skew-ms", type=float, default=1000.0)
    parser.add_argument("--market-time-tolerance-ms", type=int, default=2000)
    parser.add_argument("--slippage-bps", type=int, default=100)
    parser.add_argument("--account-type", choices=("SPOT", "FUNDING"), default="SPOT")
    parser.add_argument("--reconcile-seconds", type=float, default=15.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--discovery-interval", type=float, default=5.0)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not Decimal("0.02") <= args.pair_budget_usdt <= MAX_PAIR_BUDGET_USDT:
        raise SystemExit(
            f"--pair-budget-usdt must be between 0.02 and {MAX_PAIR_BUDGET_USDT}"
        )
    if args.balance_buffer_usdt < 0:
        raise SystemExit("--balance-buffer-usdt cannot be negative")
    if not Decimal("0") < args.max_total_cost < Decimal("2"):
        raise SystemExit("--max-total-cost must be between 0 and 2")
    if not Decimal("0") <= args.max_leg_reprice <= Decimal("0.05"):
        raise SystemExit("--max-leg-reprice must be between 0 and 0.05")
    if args.entry_window_seconds <= 0 or args.entry_window_seconds >= args.entry_seconds_left:
        raise SystemExit("entry window must be positive and smaller than entry time")
    if not 0 <= args.slippage_bps <= 500:
        raise SystemExit("--slippage-bps must be between 0 and 500")
    if args.mode == "live":
        if not args.execute_live:
            raise SystemExit("live mode also requires --execute-live")
        if os.environ.get(LIVE_CONFIRM_ENV) != LIVE_CONFIRM_VALUE:
            raise SystemExit(f"live mode requires {LIVE_CONFIRM_ENV}={LIVE_CONFIRM_VALUE}")


def _print_recent(store: CanaryStore) -> None:
    rows = store.recent()
    if not rows:
        print("No canary runs recorded.")
    for row in rows:
        print(
            f"run={row['id']} mode={row['mode']} status={row['status']} "
            f"variant={row['variant']} modeled={row['modeled_cost_per_share']} "
            f"quoted={row['quoted_cost_per_share']}"
        )


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    store = CanaryStore(args.db)
    if args.summary_only:
        _print_recent(store)
        store.close()
        return 0
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        store.close()
        raise SystemExit("BINANCE_API_KEY and BINANCE_API_SECRET are required")

    client: BinancePredictionClient
    wallet_address = wallet_id = ""
    if args.mode == "dry-run":
        client = BinancePredictionClient(api_key, api_secret)
    else:
        client = BinancePredictionTradingClient(api_key, api_secret)
        required = args.pair_budget_usdt + args.balance_buffer_usdt
        try:
            wallet_address, wallet_id, available = preflight_wallet(
                client,
                account_type=args.account_type,
                required_balance=required,
            )
        except Exception:
            client.close()
            store.close()
            raise
        print(
            f"PREFLIGHT_OK available={available:.8f} required={required:.8f} "
            f"account={args.account_type}"
        )

    print(
        f"Starting {STRATEGY_NAME} mode={args.mode} selection={args.selection} "
        f"pair_budget={args.pair_budget_usdt} max_cost={args.max_total_cost}"
    )
    if args.mode == "live":
        print("LIVE UNLOCKED: concurrent placements are not atomic.")

    pair: tuple[MarketRef, MarketRef] | None = None
    next_discovery = 0.0
    last_status: str | None = None
    try:
        while True:
            loop_started = time.monotonic()
            now_ms = client.server_timestamp_ms()
            if pair is not None and now_ms >= max(pair[0].end_ms, pair[1].end_ms):
                pair = None
            if pair is None and loop_started >= next_discovery:
                next_discovery = loop_started + args.discovery_interval
                pair = discover_active_pair(
                    client,
                    select_binary_market,
                    now_ms=now_ms,
                    tolerance_ms=args.market_time_tolerance_ms,
                )

            if pair is None:
                status = "WAITING_FOR_ALIGNED_BTC_ETH_MARKETS"
            else:
                btc, eth = pair
                seconds_left = max(
                    0.0, (min(btc.end_ms, eth.end_ms) - now_ms) / 1000.0
                )
                if store.has_round(btc.market_id, eth.market_id):
                    status = f"ROUND_ALREADY_RECORDED left={seconds_left:.1f}s"
                elif not (
                    args.entry_seconds_left - args.entry_window_seconds
                    <= seconds_left <= args.entry_seconds_left
                ):
                    status = f"WAITING_ENTRY_WINDOW left={seconds_left:.1f}s"
                else:
                    books = fetch_books(client, btc, eth)
                    capture_ms = client.server_timestamp_ms()
                    trials, _ = build_trials(
                        btc=btc,
                        eth=eth,
                        books=books,
                        total_stake=float(args.pair_budget_usdt),
                        max_total_cost_per_share=float(args.max_total_cost),
                        minimum_filled_shares=1e-8,
                        max_book_age_ms=args.max_book_age_ms,
                        max_book_skew_ms=args.max_book_skew_ms,
                        max_cross_book_skew_ms=args.max_cross_book_skew_ms,
                        now_ms=capture_ms,
                    )
                    chosen = choose_trial(trials, args.selection)
                    if chosen is None:
                        reasons = ", ".join(
                            f"{item['variant']}={item['entry_status']}" for item in trials
                        )
                        store.begin(
                            mode=args.mode,
                            status="SKIPPED_NO_ELIGIBLE_VARIANT",
                            btc_market_id=btc.market_id,
                            eth_market_id=eth.market_id,
                            pair_budget=args.pair_budget_usdt,
                            details={"trials": trials},
                            message=reasons,
                        )
                        print(f"SKIPPED_NO_ELIGIBLE_VARIANT {reasons}")
                        return 0
                    try:
                        plan = build_canary_plan(
                            chosen=chosen,
                            btc=btc,
                            eth=eth,
                            max_leg_reprice=args.max_leg_reprice,
                        )
                    except ValueError as exc:
                        store.begin(
                            mode=args.mode,
                            status="SKIPPED_SIZING",
                            btc_market_id=btc.market_id,
                            eth_market_id=eth.market_id,
                            pair_budget=args.pair_budget_usdt,
                            details={"chosen": chosen},
                            message=str(exc),
                        )
                        print(f"SKIPPED_SIZING: {exc}")
                        return 0

                    run_id = store.begin(
                        mode=args.mode,
                        status="PLANNED",
                        btc_market_id=btc.market_id,
                        eth_market_id=eth.market_id,
                        pair_budget=args.pair_budget_usdt,
                        plan=plan,
                        details=_plan_details(plan),
                    )
                    print(
                        f"PLAN variant={plan.variant} shares={plan.target_shares:.8f} "
                        f"modeled_cost/share={plan.cost_per_share:.6f}"
                    )
                    for leg in plan.legs:
                        print(
                            f"  {leg.symbol}_{leg.side} stake={leg.requested_stake:.8f} "
                            f"signal={leg.signal_price:.6f} limit={leg.price_limit:.6f}"
                        )
                    if args.mode == "dry-run":
                        store.update(run_id, status="DRY_RUN_READY")
                        print("DRY_RUN_READY: no signed quote and no order was submitted.")
                        return 0

                    assert isinstance(client, BinancePredictionTradingClient)
                    try:
                        quotes, quoted_cost = quote_equal_share_pair(
                            client,
                            wallet_address=wallet_address,
                            plan=plan,
                            pair_budget=args.pair_budget_usdt,
                            max_total_cost_per_share=args.max_total_cost,
                            slippage_bps=args.slippage_bps,
                        )
                    except Exception as exc:
                        store.update(
                            run_id,
                            status="QUOTE_REJECTED",
                            message=str(exc),
                        )
                        print(f"QUOTE_REJECTED: {str(exc)[:500]}")
                        return 2
                    quote_details = _quote_details(plan, quotes)
                    store.update(
                        run_id,
                        status="QUOTED",
                        quoted_cost=quoted_cost,
                        details=quote_details,
                    )
                    print(f"QUOTE_PAIR_ACCEPTED cost/share={quoted_cost:.6f}")
                    if args.mode == "quote-only":
                        store.update(
                            run_id,
                            status="QUOTE_ONLY_READY",
                            quoted_cost=quoted_cost,
                            details=quote_details,
                        )
                        print("QUOTE_ONLY_READY: signed quotes accepted; no order submitted.")
                        return 0

                    store.update(run_id, status="PLACE_ATTEMPTED", details=quote_details)
                    results = place_pair(
                        client,
                        wallet_address=wallet_address,
                        wallet_id=wallet_id,
                        account_type=args.account_type,
                        slippage_bps=args.slippage_bps,
                        plan=plan,
                        quotes=quotes,
                    )
                    placement_details = {**quote_details, "placements": results}
                    if not all(item["ok"] for item in results):
                        store.update(
                            run_id,
                            status="PLACEMENT_INCOMPLETE_MANUAL_RECONCILE",
                            details=placement_details,
                            message="one or both placements were rejected or ambiguous",
                        )
                        print("PLACEMENT_INCOMPLETE_MANUAL_RECONCILE")
                        print("Do not rerun; inspect both market IDs in Binance.")
                        return 3

                    order_ids = {str(item["orderId"]) for item in results}
                    store.update(
                        run_id,
                        status="SUBMITTED_BOTH",
                        details=placement_details,
                    )
                    print("SUBMITTED_BOTH: " + ", ".join(sorted(order_ids)))
                    found = reconcile_orders(
                        client,
                        wallet_address=wallet_address,
                        order_ids=order_ids,
                        seconds=args.reconcile_seconds,
                    )
                    statuses = [
                        str(found.get(str(item["orderId"]), {}).get("status") or "NOT_FOUND").upper()
                        for item in results
                    ]
                    filled = sum(item == "FILLED" for item in statuses)
                    final_status = (
                        "FILLED_BOTH" if filled == 2
                        else "ONE_SIDED_FILL_ALERT" if filled == 1
                        else "SUBMITTED_NOT_BOTH_FILLED"
                    )
                    store.update(
                        run_id,
                        status=final_status,
                        details={**placement_details, "orders": found},
                        message="/".join(statuses),
                    )
                    print(f"{final_status}: BTC={statuses[0]} ETH={statuses[1]}")
                    if final_status != "FILLED_BOTH":
                        print("Manual monitoring is required; no retry or cancel occurs.")
                    return 0 if final_status == "FILLED_BOTH" else 4

            if status != last_status:
                print(status)
                last_status = status
            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("Stopped by operator. No automatic retry will occur.")
        return 130
    finally:
        client.close()
        store.close()


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
