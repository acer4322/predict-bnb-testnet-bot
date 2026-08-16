from __future__ import annotations

import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v5 as v5
from . import poly_gap_live as poly_base
from .core import ApiTransportError, BinancePredictionTradingClient
from .target_taker_live_execution_v5 import select_single_binance_prediction_wallet

VERSION = "ECHTGELD_ENGINE_V2_4310_BALANCE_PNL_STOP_LOSS_REDEEM_POLY_FAST_V3_LIFECYCLE"
HOST = v5.HOST
PORT = v5.PORT
POLY_STRATEGY = "R_PINNED_BINANCE_POLY_DIVERGENCE"
EXIT_SLIPPAGE_BPS = 2000


class EchtgeldEngine(v5.EchtgeldEngine):
    """8781 Echtgeld owner with durable Poly Fast round/exit lifecycle.

    Entry execution still uses the hardened Target-Taker Binance adapter internally.
    The Poly identity is retained in a dedicated durable table and exposed as the
    canonical strategy identity.  Exits are risk-reducing SELL MARKET/FOK orders
    owned only by 8781; 8792 never receives venue credentials.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._poly_exit_lock = threading.RLock()
        self._poly_client: BinancePredictionTradingClient | None = None
        self._poly_wallet: dict[str, str] | None = None
        super().__init__(*args, **kwargs)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_poly_rounds (
                    entry_intent_id TEXT PRIMARY KEY,
                    round_id TEXT NOT NULL UNIQUE,
                    asset TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    fee_rate_bps INTEGER NOT NULL DEFAULT 200,
                    created_at_ms INTEGER NOT NULL,
                    exit_intent_id TEXT,
                    exit_status TEXT,
                    exit_order_id TEXT,
                    exit_proceeds_usdt REAL,
                    exit_completed_at_ms INTEGER,
                    exit_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_engine_poly_rounds_asset_market
                    ON engine_poly_rounds(asset,market_id,created_at_ms DESC);
                """
            )
            self.db.commit()

    def _poly_ensure_client(self) -> tuple[BinancePredictionTradingClient, dict[str, str]]:
        api_key = str(os.environ.get("BINANCE_API_KEY") or "").strip()
        api_secret = str(os.environ.get("BINANCE_API_SECRET") or "").strip()
        if not api_key or not api_secret:
            raise v1.EchtgeldEngineError("Poly exit requires BINANCE_API_KEY and BINANCE_API_SECRET")
        if self._poly_client is None:
            self._poly_client = BinancePredictionTradingClient(api_key, api_secret)
        if self._poly_wallet is None:
            self._poly_wallet = select_single_binance_prediction_wallet(self._poly_client.wallets())
        return self._poly_client, dict(self._poly_wallet)

    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        round_id = str(raw.get("roundId") or "").strip()
        token_id = str(raw.get("tokenId") or "").strip()
        asset = str(raw.get("asset") or "").strip().upper()
        side = str(raw.get("side") or "").strip().upper()
        market_id = int(v1._finite(raw.get("marketId")) or 0)
        created_at_ms = int(v1._finite(raw.get("createdAtMs")) or 0)
        fee_rate_bps = int(v1._finite(raw.get("feeRateBps")) or 200)
        if not round_id or not token_id:
            raise v1.EchtgeldEngineError("Poly Fast roundId and tokenId are required")
        with self.db_lock:
            active = self.db.execute(
                """SELECT r.entry_intent_id,r.round_id,i.status,o.status AS order_status,r.exit_status
                     FROM engine_poly_rounds r
                     LEFT JOIN engine_intents i ON i.intent_id=r.entry_intent_id
                     LEFT JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                    WHERE r.asset=? AND r.market_id=?
                      AND COALESCE(r.exit_status,'') NOT IN ('FLAT','CLOSED')
                    ORDER BY r.created_at_ms DESC LIMIT 1""",
                (asset, market_id),
            ).fetchone()
        if active is not None and str(active["round_id"]) != round_id:
            return {
                "ok": True,
                "accepted": False,
                "queued": False,
                "status": "ACTIVE_ROUND_EXISTS",
                "message": "Poly Fast already has an active/potential round; wait for confirmed flat before re-entry",
                "activeRoundId": str(active["round_id"]),
            }
        intent_id = str(raw.get("intentId") or "").strip()
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO engine_poly_rounds(
                       entry_intent_id,round_id,asset,market_id,side,token_id,fee_rate_bps,created_at_ms
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (intent_id, round_id, asset, market_id, side, token_id, fee_rate_bps, created_at_ms),
            )
            self.db.commit()
        result = super().submit_poly_fast_intent(raw)
        result["roundId"] = round_id
        return result

    def poly_intent_status(self, intent_id: str) -> dict[str, Any]:
        intent_id = str(intent_id or "").strip()
        with self.db_lock:
            intent = self.db.execute("SELECT * FROM engine_intents WHERE intent_id=?", (intent_id,)).fetchone()
            order = self.db.execute("SELECT * FROM engine_orders WHERE intent_id=?", (intent_id,)).fetchone()
            poly = self.db.execute("SELECT * FROM engine_poly_rounds WHERE entry_intent_id=?", (intent_id,)).fetchone()
        if intent is None and poly is None:
            return {"ok": False, "status": "NOT_FOUND", "intentId": intent_id}
        result: dict[str, Any] = {}
        if order is not None:
            try:
                result = json.loads(str(order["result_json"] or "{}"))
            except Exception:
                result = {}
        return {
            "ok": True,
            "intentId": intent_id,
            "intentStatus": str(intent["status"] if intent is not None else ""),
            "orderStatus": str(order["status"] if order is not None else ""),
            "result": result,
            "round": dict(poly) if poly is not None else None,
        }

    def _poly_active_round(self, asset: str) -> dict[str, Any] | None:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT r.*,i.status AS intent_status,o.status AS order_status,
                          o.submitted_usdt,o.shares,o.vendor_order_id,o.result_json
                     FROM engine_poly_rounds r
                     LEFT JOIN engine_intents i ON i.intent_id=r.entry_intent_id
                     LEFT JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                    WHERE r.asset=?
                    ORDER BY r.created_at_ms DESC LIMIT 25""",
                (str(asset).upper(),),
            ).fetchall()
        for raw in rows:
            row = dict(raw)
            if str(row.get("exit_status") or "").upper() in {"FLAT", "CLOSED"}:
                continue
            intent_status = str(row.get("intent_status") or "").upper()
            order_status = str(row.get("order_status") or "").upper()
            if intent_status in {"REJECTED", "IGNORED_PAUSED", "IGNORED_COHORT", "ABANDONED_RESTART"} and not order_status:
                continue
            if order_status == "REJECTED":
                continue
            phase = "ENTRY_PENDING"
            if order_status == "SUBMITTED":
                phase = "OPEN"
            elif order_status == "AMBIGUOUS":
                phase = "ENTRY_AMBIGUOUS"
            if str(row.get("exit_status") or "").upper() in {"ATTEMPTING", "SUBMITTED", "AMBIGUOUS"}:
                phase = "EXIT_" + str(row.get("exit_status") or "").upper()
            row["phase"] = phase
            row.pop("result_json", None)
            return row
        return None

    def poly_lifecycle(self) -> dict[str, Any]:
        return {asset: self._poly_active_round(asset) for asset in ("BTC", "ETH", "BNB")}

    def submit_poly_exit_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        if str(self.config.venue).strip().lower() != "binance":
            raise v1.EchtgeldEngineError("Poly Fast exit requires Echtgeld venue=binance")
        asset = str(raw.get("asset") or "").strip().upper()
        round_id = str(raw.get("roundId") or "").strip()
        exit_intent_id = str(raw.get("intentId") or "").strip()
        if asset not in {"BTC", "ETH", "BNB"} or not round_id or not exit_intent_id:
            raise v1.EchtgeldEngineError("Poly exit requires asset, roundId and intentId")
        active = self._poly_active_round(asset)
        if not active or str(active.get("round_id")) != round_id:
            return {"ok": True, "accepted": False, "status": "NO_MATCHING_ACTIVE_ROUND"}
        if str(active.get("exit_status") or "").upper() in {"ATTEMPTING", "SUBMITTED", "AMBIGUOUS", "FLAT", "CLOSED"}:
            return {"ok": True, "accepted": False, "status": "EXIT_ALREADY_HANDLED", "roundId": round_id}

        token_id = str(active.get("token_id") or "")
        market_id = int(active.get("market_id") or 0)
        with self._poly_exit_lock:
            with self.db_lock:
                self.db.execute(
                    "UPDATE engine_poly_rounds SET exit_intent_id=?,exit_status='ATTEMPTING',exit_error=NULL WHERE round_id=?",
                    (exit_intent_id, round_id),
                )
                self.db.commit()
            try:
                client, wallet = self._poly_ensure_client()
                wallet_address = wallet["walletAddress"]
                wallet_id = wallet["walletId"]
                position = client.position_by_token(wallet_address, token_id)
                shares = poly_base._first_number(position, ("availableShares", "availableShareQty", "shares", "shareQty", "quantity"))
                if shares is not None and shares <= 1e-9:
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status='FLAT',exit_completed_at_ms=? WHERE round_id=?",
                            (v1._now_ms(), round_id),
                        )
                        self.db.commit()
                    return {"ok": True, "accepted": True, "status": "FLAT", "roundId": round_id}
                if shares is None or shares <= 0:
                    raise v1.EchtgeldEngineError("Poly exit could not read a positive available position")
                quote = client.get_quote(
                    wallet_address=wallet_address,
                    token_id=token_id,
                    amount_in_wei=poly_base._to_wei(float(shares)),
                    price_limit=None,
                    slippage_bps=EXIT_SLIPPAGE_BPS,
                    fee_rate_bps=int(active.get("fee_rate_bps") or 200),
                    funding_source="MPC",
                    side="SELL",
                    order_type="MARKET",
                )
                quote_id = str(quote.get("quoteId") or "")
                proceeds = poly_base._from_wei(quote.get("amountOut"))
                if not quote_id:
                    raise v1.EchtgeldEngineError("Poly exit signed quote contained no quoteId")
                try:
                    placed = client.place_market_order(
                        wallet_address=wallet_address,
                        wallet_id=wallet_id,
                        quote_id=quote_id,
                        slippage_bps=EXIT_SLIPPAGE_BPS,
                        account_type=str(os.environ.get("PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE") or "SPOT").strip().upper(),
                        funding_source="MPC",
                    )
                except ApiTransportError as exc:
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE engine_poly_rounds SET exit_status='AMBIGUOUS',exit_error=? WHERE round_id=?",
                            (f"{type(exc).__name__}: {str(exc)[:400]}", round_id),
                        )
                        self.db.commit()
                    return {"ok": False, "accepted": True, "status": "AMBIGUOUS", "roundId": round_id, "error": str(exc)[:400]}
                order_id = poly_base._first_text(placed, ("orderId", "order_id", "id"))
                flat = False
                for delay in (0.10, 0.20, 0.35, 0.50, 0.75):
                    time.sleep(delay)
                    pos = client.position_by_token(wallet_address, token_id)
                    remaining = poly_base._first_number(pos, ("availableShares", "availableShareQty", "shares", "shareQty", "quantity"))
                    if remaining is not None and remaining <= 1e-9:
                        flat = True
                        break
                status = "FLAT" if flat else "AMBIGUOUS"
                error = None if flat else "SELL submitted but flat position was not confirmed; no automatic duplicate sell"
                with self.db_lock:
                    self.db.execute(
                        """UPDATE engine_poly_rounds SET exit_status=?,exit_order_id=?,exit_proceeds_usdt=?,
                               exit_completed_at_ms=?,exit_error=? WHERE round_id=?""",
                        (status, order_id, proceeds, v1._now_ms() if flat else None, error, round_id),
                    )
                    self.db.commit()
                return {"ok": flat, "accepted": True, "status": status, "roundId": round_id, "vendorOrderId": order_id, "exitProceedsUsdt": proceeds, "error": error}
            except Exception as exc:
                with self.db_lock:
                    self.db.execute(
                        "UPDATE engine_poly_rounds SET exit_status=NULL,exit_error=? WHERE round_id=?",
                        (f"{type(exc).__name__}: {str(exc)[:400]}", round_id),
                    )
                    self.db.commit()
                raise

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = super().orders(limit)
        with self.db_lock:
            mapping = {
                str(row["entry_intent_id"]): dict(row)
                for row in self.db.execute("SELECT * FROM engine_poly_rounds")
            }
        for row in rows:
            meta = mapping.get(str(row.get("intent_id") or ""))
            if not meta:
                continue
            row["executionNamespaceStrategy"] = row.get("strategy")
            row["strategy"] = POLY_STRATEGY
            row["sourceStrategy"] = POLY_STRATEGY
            row["asset"] = meta.get("asset")
            row["roundId"] = meta.get("round_id")
            row["exitStatus"] = meta.get("exit_status")
            row["exitProceedsUsdt"] = meta.get("exit_proceeds_usdt")
        return rows

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        allowed = list(payload.get("allowedStrategies") or [])
        if POLY_STRATEGY not in allowed:
            allowed.append(POLY_STRATEGY)
        payload["allowedStrategies"] = sorted(set(allowed))
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway.update(
                canonicalLedgerIdentity=POLY_STRATEGY,
                executionNamespace="TARGET_TAKER_INTERNAL_ADAPTER",
                roundScopedLifecycle=True,
                oneActiveRoundAtATime=True,
                sameMarketReentryAfterConfirmedFlat=True,
                exitEndpoint="/poly-exit-intent",
                exitPolicy="POLY_DIRECTION_REVERSAL",
            )
        payload["polyFastLifecycle"] = self.poly_lifecycle()
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyFastRoundLifecycle"] = True
        payload["polyFastExitEndpoint"] = "/poly-exit-intent"
        return payload

    def close(self) -> None:
        if self._poly_client is not None:
            try:
                self._poly_client.close()
            except Exception:
                pass
        super().close()


class _Handler(v5._Handler):
    engine: EchtgeldEngine

    def do_GET(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        if path == "/poly-lifecycle":
            self._send(200, {"ok": True, "lifecycle": self.engine.poly_lifecycle()})
            return
        if path == "/poly-intent-status":
            from urllib.parse import parse_qs
            intent_id = str((parse_qs(query).get("intentId") or [""])[0])
            self._send(200, self.engine.poly_intent_status(intent_id))
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/poly-exit-intent":
            return super().do_POST()
        try:
            payload = self._body()
            self._send(200, self.engine.submit_poly_exit_intent(payload))
        except v1.EchtgeldEngineError as exc:
            self._send(400, {"ok": False, "error": str(exc)[:500]})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV6Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; Poly Fast round lifecycle restored; 8781-only venue owner",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
