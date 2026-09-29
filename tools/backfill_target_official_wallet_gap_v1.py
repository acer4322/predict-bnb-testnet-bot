from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from predict_bot import target_wallet_official_v1 as official

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_STATE = ROOT / "data" / "research" / "target_official_gap_backfill_v1_state.json"
API_BASE = official.API_BASE
WALLET = official.TARGET_WALLET
PAGE_FIRST = 500  # Predict currently caps this to 150 rows/page.


def iso_to_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def forge_cursor(end_iso: str) -> str:
    payload = {
        "createdAt": end_iso,
        "orderId": 9_999_999_999_999,
        "transactionId": 9_999_999_999_999,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


def market_asset_5m(market: dict[str, Any]) -> str | None:
    slug = str(market.get("categorySlug") or "").strip().lower()
    if "-updown-5m-" not in slug:
        return None
    symbol = str((market.get("variantData") or {}).get("priceFeedSymbol") or "").upper()
    for asset in ("BTC", "ETH", "BNB"):
        if symbol.startswith(asset):
            return asset
    prefix = slug.split("-updown-5m-", 1)[0]
    return {"btc": "BTC", "bitcoin": "BTC", "eth": "ETH", "ethereum": "ETH", "bnb": "BNB"}.get(prefix)


def market_window_end_ms(market: dict[str, Any]) -> int | None:
    for key in ("boostEndsAt",):
        value = official.iso_ms(market.get(key))
        if value is not None:
            return value
    rewards = market.get("rewards") or {}
    current = rewards.get("current") if isinstance(rewards, dict) else None
    if isinstance(current, dict):
        value = official.iso_ms(current.get("endsAt"))
        if value is not None:
            return value
    slug = str(market.get("categorySlug") or "")
    try:
        start_s = int(slug.rsplit("-", 1)[1])
        return start_s * 1000 + 300_000
    except Exception:
        return None


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS target_backfill_provenance_v1 (
            leg_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            role TEXT NOT NULL,
            source_executed_at_ms INTEGER NOT NULL,
            recovered_at_ms INTEGER NOT NULL,
            observed_clock_quality TEXT NOT NULL,
            source_cursor TEXT
        );
        CREATE TABLE IF NOT EXISTS target_backfill_runs_v1 (
            run_id TEXT PRIMARY KEY,
            started_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            start_ms INTEGER NOT NULL,
            end_ms INTEGER NOT NULL,
            state_json TEXT NOT NULL
        );
        """
    )
    db.commit()


def upsert_market(db: sqlite3.Connection, asset: str, raw: dict[str, Any], observed_at_ms: int) -> None:
    market = raw.get("market") or {}
    market_id = official.positive_int(market.get("id"))
    if market_id is None:
        return
    title = str(market.get("title") or market.get("question") or "") or None
    window_end = market_window_end_ms(market)
    winner = official.resolved_winner(market)
    status = "SETTLED" if winner in {"UP", "DOWN"} else "PENDING_SETTLEMENT"
    resolved_at = window_end if winner in {"UP", "DOWN"} and window_end is not None else None
    first_seen = min(observed_at_ms, window_end or observed_at_ms)
    last_seen = max(observed_at_ms, window_end or observed_at_ms)
    db.execute(
        """
        INSERT INTO target_markets(
            market_id,asset,title,window_end_ms,first_seen_ms,last_seen_ms,status,winner,resolved_at_ms
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(market_id) DO UPDATE SET
            asset=excluded.asset,
            title=COALESCE(excluded.title,target_markets.title),
            window_end_ms=COALESCE(excluded.window_end_ms,target_markets.window_end_ms),
            first_seen_ms=MIN(target_markets.first_seen_ms,excluded.first_seen_ms),
            last_seen_ms=MAX(target_markets.last_seen_ms,excluded.last_seen_ms),
            status=CASE
                WHEN COALESCE(target_markets.winner,excluded.winner) IS NOT NULL THEN 'SETTLED'
                ELSE target_markets.status
            END,
            winner=COALESCE(target_markets.winner,excluded.winner),
            resolved_at_ms=COALESCE(target_markets.resolved_at_ms,excluded.resolved_at_ms)
        """,
        (market_id, asset, title, window_end, first_seen, last_seen, status, winner, resolved_at),
    )


def upsert_parent(db: sqlite3.Connection, asset: str, leg: dict[str, Any], at: int) -> None:
    parent_identity = str(leg.get("orderHash") or leg["legId"])
    parent_id = f"{asset}:{leg['role']}:{parent_identity}:{leg['side']}:{leg['quoteType']}"
    existing = db.execute(
        "SELECT average_price,shares,fill_legs,first_event_ms,last_event_ms FROM target_parent_orders WHERE parent_id=?",
        (parent_id,),
    ).fetchone()
    shares = float(leg["shares"])
    price = float(leg["price"])
    event_ms = int(leg["eventMs"])
    if existing is None:
        db.execute(
            """
            INSERT INTO target_parent_orders(
                parent_id,wallet,asset,market_id,role,side,quote_type,order_hash,
                first_event_ms,last_event_ms,average_price,shares,fill_legs,updated_at_ms
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                parent_id, WALLET, asset, int(leg["marketId"]), leg["role"], leg["side"],
                leg["quoteType"], leg.get("orderHash"), event_ms, event_ms, price, shares, 1, at,
            ),
        )
    else:
        previous_shares = float(existing["shares"])
        total = previous_shares + shares
        average = ((float(existing["average_price"]) * previous_shares) + price * shares) / total if total > 0 else price
        db.execute(
            """
            UPDATE target_parent_orders
               SET first_event_ms=?,last_event_ms=?,average_price=?,shares=?,fill_legs=?,updated_at_ms=?
             WHERE parent_id=?
            """,
            (
                min(int(existing["first_event_ms"]), event_ms),
                max(int(existing["last_event_ms"]), event_ms),
                average, total, int(existing["fill_legs"]) + 1, at, parent_id,
            ),
        )


def persist_leg(
    db: sqlite3.Connection,
    asset: str,
    leg: dict[str, Any],
    raw: dict[str, Any],
    *,
    recovered_at_ms: int,
    cursor: str | None,
) -> bool:
    # Historical API recovery cannot reconstruct the original local receipt clock.
    # Use recovery time deliberately (conservative/stale) and record provenance so
    # receipt-clock-sensitive studies cannot mistake it for live low-latency capture.
    observed_at_ms = recovered_at_ms
    cur = db.execute(
        """
        INSERT OR IGNORE INTO wallet_shadow_target_events(
            leg_id,wallet,asset,market_id,role,side,quote_type,order_hash,
            transaction_hash,settlement_id,event_ms,observed_at_ms,price,shares,raw_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            leg["legId"], WALLET, asset, int(leg["marketId"]), leg["role"], leg["side"],
            leg["quoteType"], leg.get("orderHash"), leg.get("transactionHash"),
            leg.get("settlementId"), int(leg["eventMs"]), observed_at_ms,
            float(leg["price"]), float(leg["shares"]),
            json.dumps(raw, separators=(",", ":"), default=str),
        ),
    )
    if cur.rowcount != 1:
        return False
    db.execute(
        """
        INSERT OR IGNORE INTO wallet_shadow_target_event_context(
            leg_id,observed_at_ms,asset,market_id
        ) VALUES(?,?,?,?)
        """,
        (leg["legId"], observed_at_ms, asset, int(leg["marketId"])),
    )
    upsert_parent(db, asset, leg, observed_at_ms)
    db.execute(
        """
        INSERT OR REPLACE INTO target_backfill_provenance_v1(
            leg_id,source,role,source_executed_at_ms,recovered_at_ms,observed_clock_quality,source_cursor
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            leg["legId"], "PREDICT_GLOBAL_WALLET_MATCH_API_V1", leg["role"],
            int(leg["eventMs"]), recovered_at_ms,
            "RECOVERY_TIME_NOT_ORIGINAL_RECEIPT_CLOCK", cursor,
        ),
    )
    return True


def settle_backfilled_markets(db: sqlite3.Connection, start_ms: int, end_ms: int) -> int:
    rows = db.execute(
        """
        SELECT market_id,asset,title,winner,window_end_ms
          FROM target_markets
         WHERE winner IN ('UP','DOWN')
           AND COALESCE(window_end_ms,resolved_at_ms) BETWEEN ? AND ?
        """,
        (start_ms - 600_000, end_ms + 600_000),
    ).fetchall()
    settled = 0
    for item in rows:
        market_id = int(item["market_id"])
        winner = str(item["winner"])
        fills = [
            dict(r) for r in db.execute(
                "SELECT role,side,quote_type,price,shares FROM wallet_shadow_target_events WHERE market_id=? ORDER BY event_ms,id",
                (market_id,),
            )
        ]
        if not fills:
            continue
        total = official.TargetWalletOfficialCollector._account_rows(fills, winner)
        maker = official.TargetWalletOfficialCollector._account_rows(fills, winner, "MAKER")
        taker = official.TargetWalletOfficialCollector._account_rows(fills, winner, "TAKER")
        parent_count = int(db.execute("SELECT COUNT(*) FROM target_parent_orders WHERE market_id=?", (market_id,)).fetchone()[0])
        resolved_at = int(item["window_end_ms"] or end_ms)
        db.execute(
            """
            INSERT INTO target_market_results(
                market_id,asset,title,winner,resolved_at_ms,fill_count,parent_count,
                buy_notional_usdt,sell_proceeds_usdt,payout_usdt,net_pnl_usdt,net_roi,
                maker_net_pnl_usdt,taker_net_pnl_usdt,up_position_shares,down_position_shares,accounting_version
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(market_id) DO UPDATE SET
                asset=excluded.asset,title=excluded.title,winner=excluded.winner,
                resolved_at_ms=excluded.resolved_at_ms,fill_count=excluded.fill_count,
                parent_count=excluded.parent_count,buy_notional_usdt=excluded.buy_notional_usdt,
                sell_proceeds_usdt=excluded.sell_proceeds_usdt,payout_usdt=excluded.payout_usdt,
                net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                maker_net_pnl_usdt=excluded.maker_net_pnl_usdt,
                taker_net_pnl_usdt=excluded.taker_net_pnl_usdt,
                up_position_shares=excluded.up_position_shares,
                down_position_shares=excluded.down_position_shares,
                accounting_version=excluded.accounting_version
            """,
            (
                market_id, item["asset"], item["title"], winner, resolved_at,
                int(total["fillCount"] or 0), parent_count,
                float(total["buyNotionalUsdt"] or 0.0),
                float(total["sellProceedsUsdt"] or 0.0),
                float(total["payoutUsdt"] or 0.0),
                float(total["netPnlUsdt"] or 0.0),
                total["netRoi"],
                float(maker["netPnlUsdt"] or 0.0),
                float(taker["netPnlUsdt"] or 0.0),
                float(total["upPositionShares"] or 0.0),
                float(total["downPositionShares"] or 0.0),
                "FILLED_CASHFLOW_PLUS_WINNER_V1_NO_EXPLICIT_FEE_BACKFILL_V1",
            ),
        )
        settled += 1
    db.commit()
    return settled


def load_state(path: Path, start_iso: str, end_iso: str, reset: bool) -> dict[str, Any]:
    if path.exists() and not reset:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("startIso") != start_iso or state.get("endIso") != end_iso:
            raise RuntimeError("state range mismatch; use --reset-state for a new range")
        return state
    state = {
        "version": "TARGET_OFFICIAL_GAP_BACKFILL_V1",
        "startIso": start_iso,
        "endIso": end_iso,
        "startedAtMs": int(time.time() * 1000),
        "roles": {
            "MAKER": {"cursor": forge_cursor(end_iso), "done": False, "pages": 0, "rows": 0, "insertedLegs": 0, "oldestMs": None},
            "TAKER": {"cursor": forge_cursor(end_iso), "done": False, "pages": 0, "rows": 0, "insertedLegs": 0, "oldestMs": None},
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updatedAtMs"] = int(time.time() * 1000)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_page(client: httpx.Client, *, role: str, cursor: str) -> dict[str, Any]:
    params = {
        "first": PAGE_FIRST,
        "signerAddress": WALLET,
        "isSignerMaker": "true" if role == "MAKER" else "false",
        "after": cursor,
    }
    delay = 0.8
    while True:
        response = client.get(f"{API_BASE}/v1/orders/matches", params=params)
        if response.status_code != 429:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("unexpected matches response")
            return payload
        retry = response.headers.get("retry-after")
        try:
            sleep_s = max(delay, float(retry)) if retry else delay
        except ValueError:
            sleep_s = delay
        time.sleep(min(10.0, sleep_s))
        delay = min(10.0, delay * 1.7)


def run_role(
    db: sqlite3.Connection,
    client: httpx.Client,
    state: dict[str, Any],
    state_path: Path,
    *,
    role: str,
    start_ms: int,
    max_pages: int,
) -> None:
    rs = state["roles"][role]
    if rs.get("done"):
        print(f"{role}: already complete", flush=True)
        return
    pages_this_run = 0
    while not rs.get("done"):
        if max_pages > 0 and pages_this_run >= max_pages:
            break
        cursor = str(rs["cursor"])
        payload = fetch_page(client, role=role, cursor=cursor)
        data = payload.get("data") if isinstance(payload.get("data"), list) else []
        next_cursor = str(payload.get("cursor") or "").strip()
        if not data:
            rs["done"] = True
            save_state(state_path, state)
            break

        recovered_at = int(time.time() * 1000)
        inserted = 0
        oldest = None
        crossed_start = False
        for raw in data:
            if not isinstance(raw, dict):
                continue
            event_ms = official.iso_ms(raw.get("executedAt"))
            if event_ms is None:
                continue
            oldest = event_ms if oldest is None else min(oldest, event_ms)
            if event_ms < start_ms:
                crossed_start = True
                continue
            market = raw.get("market") if isinstance(raw.get("market"), dict) else {}
            asset = market_asset_5m(market)
            if asset is None:
                continue
            upsert_market(db, asset, raw, recovered_at)
            if role == "TAKER":
                leg = official.normalize_match_leg(raw, wallet=WALLET, role="TAKER", maker_index=None)
                if leg is not None and persist_leg(db, asset, leg, raw, recovered_at_ms=recovered_at, cursor=cursor):
                    inserted += 1
            else:
                makers = raw.get("makers") if isinstance(raw.get("makers"), list) else []
                for idx in range(len(makers)):
                    leg = official.normalize_match_leg(raw, wallet=WALLET, role="MAKER", maker_index=idx)
                    if leg is not None and persist_leg(db, asset, leg, raw, recovered_at_ms=recovered_at, cursor=cursor):
                        inserted += 1

        db.commit()
        rs["pages"] = int(rs.get("pages") or 0) + 1
        rs["rows"] = int(rs.get("rows") or 0) + len(data)
        rs["insertedLegs"] = int(rs.get("insertedLegs") or 0) + inserted
        if oldest is not None:
            rs["oldestMs"] = oldest if rs.get("oldestMs") is None else min(int(rs["oldestMs"]), oldest)
        pages_this_run += 1

        if crossed_start or (oldest is not None and oldest < start_ms):
            rs["done"] = True
        elif not next_cursor or next_cursor == cursor:
            rs["done"] = True
        else:
            rs["cursor"] = next_cursor

        save_state(state_path, state)
        if pages_this_run % 25 == 0 or rs.get("done"):
            print(
                f"{role}: run_pages={pages_this_run} total_pages={rs['pages']} "
                f"rows={rs['rows']} inserted={rs['insertedLegs']} "
                f"oldest={ms_to_iso(int(rs['oldestMs'])) if rs.get('oldestMs') else None} done={rs['done']}",
                flush=True,
            )
        time.sleep(0.08)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--state", type=Path, default=DEFAULT_STATE)
    p.add_argument("--start", default="2026-09-16T15:30:00Z")
    p.add_argument("--end", default="2026-09-23T22:45:00Z")
    p.add_argument("--role", choices=("maker", "taker", "both"), default="both")
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--reset-state", action="store_true")
    p.add_argument("--skip-settle", action="store_true")
    args = p.parse_args()

    api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("PREDICT_FUN_API_KEY is required")

    start_ms = iso_to_ms(args.start)
    end_ms = iso_to_ms(args.end)
    if start_ms >= end_ms:
        raise SystemExit("start must be earlier than end")

    db = sqlite3.connect(args.db, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=30000")
    ensure_schema(db)

    state = load_state(args.state, args.start, args.end, args.reset_state)
    run_id = f"{args.start}->{args.end}"
    db.execute(
        """
        INSERT OR REPLACE INTO target_backfill_runs_v1(
            run_id,started_at_ms,updated_at_ms,start_ms,end_ms,state_json
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            run_id, int(state.get("startedAtMs") or time.time() * 1000), int(time.time() * 1000),
            start_ms, end_ms, json.dumps(state, separators=(",", ":")),
        ),
    )
    db.commit()

    client = httpx.Client(
        timeout=httpx.Timeout(20.0, connect=4.0),
        trust_env=False,
        headers={"Accept": "application/json", "x-api-key": api_key},
    )
    try:
        roles = ["MAKER", "TAKER"] if args.role == "both" else [args.role.upper()]
        for role in roles:
            run_role(db, client, state, args.state, role=role, start_ms=start_ms, max_pages=max(0, args.max_pages))
        settled = 0 if args.skip_settle else settle_backfilled_markets(db, start_ms, end_ms)
        db.execute(
            "UPDATE target_backfill_runs_v1 SET updated_at_ms=?,state_json=? WHERE run_id=?",
            (int(time.time() * 1000), json.dumps(state, separators=(",", ":")), run_id),
        )
        db.commit()
        print(json.dumps({"state": state, "settledMarkets": settled}, separators=(",", ":")), flush=True)
    finally:
        client.close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
