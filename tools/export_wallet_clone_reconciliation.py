from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from predict_bot.core import BinancePredictionTradingClient  # noqa: E402

API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
WEI = 10**18


def dec(value: Any) -> float | None:
    try:
        return float(value) / WEI
    except (TypeError, ValueError):
        return None


def api_get(path: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    request = Request(
        f"{API_BASE}{path}?{query}",
        headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": "BTC-5M-Lab-Clone-Reconciliation/1.1"},
    )
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected Predict response for {path}")
    return payload


def credential_pair() -> tuple[str, str]:
    key = str(os.environ.get("BINANCE_LIVE_API_KEY") or "").strip()
    secret = str(os.environ.get("BINANCE_LIVE_API_SECRET") or "").strip()
    if not key:
        key = input("BINANCE_LIVE_API_KEY (read once, not saved): ").strip()
    if not secret:
        secret = getpass.getpass("BINANCE_LIVE_API_SECRET (hidden, not saved): ").strip()
    if not key or not secret:
        raise SystemExit("Binance LIVE credentials are required unless --wallet-address is supplied")
    return key, secret


def discover_wallet_address() -> str:
    key, secret = credential_pair()
    client = BinancePredictionTradingClient(key, secret)
    try:
        wallets = client.wallets().get("wallets") or []
        if len(wallets) != 1:
            raise SystemExit(f"Expected exactly one Prediction wallet, got {len(wallets)}")
        address = str(wallets[0].get("walletAddress") or "").strip()
        if not address:
            raise SystemExit("Prediction wallet address missing")
        return address
    finally:
        client.close()


def load_clone_rows(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not db_path.exists():
        return [], []
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        pairs = [dict(r) for r in conn.execute("SELECT * FROM wallet_maker_clone_pairs ORDER BY id ASC")]
        orders = [dict(r) for r in conn.execute("SELECT * FROM wallet_maker_clone_orders ORDER BY id ASC")]
        return pairs, orders
    finally:
        conn.close()


def quote_type(row: dict[str, Any]) -> str:
    return str(row.get("quoteType") or "").strip().upper()


def outcome_name(row: dict[str, Any]) -> str:
    outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
    return str(outcome.get("name") or "").strip().upper()


def signer(row: dict[str, Any]) -> str:
    return str(row.get("signer") or "").lower()


def fee_amount(row: dict[str, Any]) -> tuple[float, str]:
    fee = row.get("fee") if isinstance(row.get("fee"), dict) else {}
    return dec(fee.get("amount")) or 0.0, str(fee.get("type") or "").upper()


def classify_match(match: dict[str, Any], address: str) -> list[dict[str, Any]]:
    target = address.lower()
    makers = [x for x in (match.get("makers") or []) if isinstance(x, dict)]
    taker = match.get("taker") if isinstance(match.get("taker"), dict) else {}
    target_makers = [m for m in makers if signer(m) == target]
    results: list[dict[str, Any]] = []
    total_maker_amount = sum(dec(m.get("amount")) or 0.0 for m in makers)
    taker_fee, taker_fee_type = fee_amount(taker)
    for maker in target_makers:
        m_q = quote_type(maker)
        t_q = quote_type(taker)
        m_out = outcome_name(maker)
        t_out = outcome_name(taker)
        if m_q == "BID" and t_q == "BID" and m_out and t_out and m_out != t_out:
            path = "MINT"
        elif m_q == "BID" and t_q == "ASK" and m_out and t_out and m_out == t_out:
            path = "NORMAL"
        elif m_q == "ASK" and t_q == "ASK" and m_out and t_out and m_out != t_out:
            path = "MERGE"
        else:
            path = "OTHER"
        maker_amount = dec(maker.get("amount")) or 0.0
        maker_fee, maker_fee_type = fee_amount(maker)
        allocation = maker_amount / total_maker_amount if total_maker_amount > 0 else 0.0
        predicted_rebate = 0.0
        if path == "MINT" and taker_fee_type == "SHARES":
            predicted_rebate = 0.25 * taker_fee * allocation
        results.append(
            {
                "executedAt": match.get("executedAt"),
                "transactionHash": match.get("transactionHash"),
                "marketId": ((match.get("market") or {}).get("id") if isinstance(match.get("market"), dict) else None),
                "path": path,
                "makerOutcome": m_out,
                "makerPrice": dec(maker.get("price")),
                "makerAmountShares": maker_amount,
                "makerFee": maker_fee,
                "makerFeeType": maker_fee_type,
                "makerHash": maker.get("hash"),
                "takerOutcome": t_out,
                "takerPrice": dec(taker.get("price")),
                "takerAmountShares": dec(taker.get("amount")),
                "takerFee": taker_fee,
                "takerFeeType": taker_fee_type,
                "makerAllocationOfMatch": allocation,
                "predicted25PctMakerRebateShares": predicted_rebate,
            }
        )
    return results


def position_rows(payload: dict[str, Any], market_id: int) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        market = row.get("market") if isinstance(row.get("market"), dict) else {}
        if int(market.get("id") or 0) != market_id:
            continue
        outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
        amount_raw = row.get("amount") or row.get("shares") or row.get("balance")
        amount = dec(amount_raw)
        if amount is None:
            try:
                amount = float(amount_raw)
            except (TypeError, ValueError):
                amount = None
        out.append({"outcome": str(outcome.get("name") or "").upper(), "amount": amount, "raw": row})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only reconciliation of Wallet Maker Clone fills against Predict match/position APIs")
    parser.add_argument("--asset", choices=["ETH", "BNB", "ALL"], default="ALL")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", default="wallet_clone_reconciliation.json")
    parser.add_argument(
        "--wallet-address",
        default=None,
        help="Prediction wallet address. If supplied, Binance LIVE credentials are not needed.",
    )
    args = parser.parse_args()

    api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("PREDICT_FUN_API_KEY is required")
    address = str(args.wallet_address or "").strip() or discover_wallet_address()
    if not address.lower().startswith("0x"):
        raise SystemExit("--wallet-address must be a 0x... Prediction wallet address")
    assets = ["ETH", "BNB"] if args.asset == "ALL" else [args.asset]
    report: dict[str, Any] = {
        "walletAddress": address,
        "note": "25% rebate is an empirical hypothesis from prior clean reconciliations; predicted values below are not protocol documentation.",
        "assets": {},
    }

    local_by_asset: dict[str, dict[str, Any]] = {}
    for asset in assets:
        db = Path(args.data_dir) / f"wallet_maker_clone_{asset.lower()}.db"
        pairs, orders = load_clone_rows(db)
        market_ids = sorted({int(p["market_id"]) for p in pairs if p.get("market_id")})
        local_by_asset[asset] = {"database": str(db), "pairs": pairs, "orders": orders, "marketIds": market_ids}

    positions_payload = api_get(f"/v1/positions/{address}", {"first": 500}, api_key)

    for asset in assets:
        local = local_by_asset[asset]
        market_reports: list[dict[str, Any]] = []
        for market_id in local["marketIds"]:
            matches_payload = api_get(
                "/v1/orders/matches",
                {"first": 500, "marketId": market_id, "signerAddress": address, "isSignerMaker": "true"},
                api_key,
            )
            matches = matches_payload.get("data") if isinstance(matches_payload.get("data"), list) else []
            legs: list[dict[str, Any]] = []
            for match in matches:
                if isinstance(match, dict):
                    legs.extend(classify_match(match, address))
            local_orders = [o for o in local["orders"] if int(o.get("market_id") or 0) == market_id]
            by_outcome = {"UP": 0.0, "DOWN": 0.0}
            rebate_by_outcome = {"UP": 0.0, "DOWN": 0.0}
            for leg in legs:
                side = str(leg.get("makerOutcome") or "").upper()
                if side in by_outcome:
                    by_outcome[side] += float(leg.get("makerAmountShares") or 0.0)
                # MINT rebate is paid in the counterparty/taker outcome in the previously observed mechanism.
                rebate_side = str(leg.get("takerOutcome") or "").upper()
                if rebate_side in rebate_by_outcome:
                    rebate_by_outcome[rebate_side] += float(leg.get("predicted25PctMakerRebateShares") or 0.0)
            positions = position_rows(positions_payload, market_id)
            position_map = {str(p.get("outcome") or "").upper(): p.get("amount") for p in positions}
            residuals = {}
            for side in ("UP", "DOWN"):
                pos = position_map.get(side)
                residuals[side] = None if pos is None else float(pos) - by_outcome[side]
            market_reports.append(
                {
                    "marketId": market_id,
                    "localOrders": local_orders,
                    "predictMakerLegs": legs,
                    "makerLegCount": len(legs),
                    "pathCounts": {name: sum(1 for x in legs if x.get("path") == name) for name in ("MINT", "NORMAL", "MERGE", "OTHER")},
                    "makerFeeTotal": sum(float(x.get("makerFee") or 0.0) for x in legs),
                    "predicted25PctMakerRebateShares": rebate_by_outcome,
                    "matchedMakerShares": by_outcome,
                    "currentPositionShares": position_map,
                    "positionMinusMakerFills": residuals,
                    "positionResidualWarning": "Residual is only attributable to rebate if this wallet had no pre-existing/manual/other-strategy inventory in this market.",
                }
            )
        report["assets"][asset] = {**local, "markets": market_reports}

    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wallet={address}")
    for asset, block in report["assets"].items():
        markets = block["markets"]
        legs = [leg for market in markets for leg in market["predictMakerLegs"]]
        print(
            f"{asset}: markets={len(markets)} maker_legs={len(legs)} "
            f"MINT={sum(1 for x in legs if x['path']=='MINT')} NORMAL={sum(1 for x in legs if x['path']=='NORMAL')} "
            f"maker_fee={sum(float(x.get('makerFee') or 0) for x in legs):.9f} "
            f"predicted_rebate={sum(float(x.get('predicted25PctMakerRebateShares') or 0) for x in legs):.9f} shares"
        )
    print(f"wrote {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
