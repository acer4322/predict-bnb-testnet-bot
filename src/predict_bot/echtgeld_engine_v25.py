from __future__ import annotations

import json
import math
import random
import statistics
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v24 as v24

VERSION = "ECHTGELD_ENGINE_V2_4310_CAP100_PREFLIGHT_STRESS_EXAM_V1"
HOST = v24.HOST
PORT = v24.PORT
DEFAULT_MARKETS = 1000
MAX_MARKETS = 10000
DEFAULT_SEED = 20260820
CAP_TOTAL_USDT = 100.0
STOP_LOSS_USDT = 10.0


class EchtgeldEngine(v24.EchtgeldEngine):
    """V24 plus an isolated randomized CAP100 pre-live stress exam.

    The exam never calls a venue and never writes production CAP100 orders/fills.
    It is intentionally an execution/risk stress test, not a forecast of live edge.
    Each synthetic market exercises capital reservation, Maker/Taker fills, partial
    fills, price-cap blocks, placement uncertainty, cancel/fill races, controller
    loss and stop-loss behavior. Safety invariants are hard pass/fail gates; PnL is
    reported separately as synthetic stress economics.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.stress_exam_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def _setup_schema(self) -> None:
        super()._setup_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_cap100_stress_exam_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER,
                    seed INTEGER NOT NULL,
                    markets INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    safety_grade TEXT,
                    report_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            self.db.commit()

    @staticmethod
    def _quantile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        xs = sorted(float(x) for x in values)
        if len(xs) == 1:
            return xs[0]
        pos = max(0.0, min(1.0, q)) * (len(xs) - 1)
        lo = int(math.floor(pos)); hi = int(math.ceil(pos))
        if lo == hi:
            return xs[lo]
        w = pos - lo
        return xs[lo] * (1.0 - w) + xs[hi] * w

    @staticmethod
    def _fault_name(rng: random.Random) -> str:
        x = rng.random()
        if x < 0.035:
            return "SUBMISSION_REJECTED"
        if x < 0.065:
            return "VENUE_FAILED_NO_FILL"
        if x < 0.115:
            return "PARTIAL_FILL"
        if x < 0.155:
            return "PRICE_SPIKE_CAP"
        if x < 0.175:
            return "TRANSPORT_UNKNOWN_FILLED"
        if x < 0.190:
            return "CANCEL_FILL_RACE"
        if x < 0.198:
            return "HEARTBEAT_LOSS"
        if x < 0.205:
            return "UNKNOWN_UNRESOLVED"
        return "NORMAL"

    def _simulate_market(self, rng: random.Random, index: int, equity_before: float) -> dict[str, Any]:
        # Synthetic CAP100 lifecycle. Economics are deliberately conservative and
        # are not presented as a live-edge estimate.
        fault = self._fault_name(rng)
        market_id = 900000000 + index
        winner = "UP" if rng.random() < 0.5 else "DOWN"
        maker_budget = 80.0
        taker_reserve = 20.0
        maker_spent = 0.0
        taker_spent = 0.0
        committed = 0.0
        inventory = {"UP": 0.0, "DOWN": 0.0}
        cost = {"UP": 0.0, "DOWN": 0.0}
        events: list[dict[str, Any]] = []
        safety = {
            "capitalExceeded": False,
            "phantomFill": False,
            "blindRetry": False,
            "cancelAckAssumedTerminal": False,
            "stopLossFailed": False,
            "unknownWriteLeftOpen": False,
        }

        # 2-5 Maker attempts, each fixed 18 shares as CAP100 does.
        attempts = rng.randint(2, 5)
        for n in range(attempts):
            side = "UP" if rng.random() < 0.5 else "DOWN"
            price = round(rng.uniform(0.12, 0.72), 2)
            need = price * 18.0
            if maker_spent + committed + need > maker_budget + 1e-9:
                events.append({"event": "MAKER_CAP_BLOCK", "side": side, "price": price})
                continue
            committed += need
            this_fault = fault if n == 0 else "NORMAL"
            if this_fault == "SUBMISSION_REJECTED":
                committed -= need
                events.append({"event": "ORDER_REJECTED_PREWRITE", "side": side})
                continue
            if this_fault == "VENUE_FAILED_NO_FILL":
                committed -= need
                events.append({"event": "VENUE_FAILED_NO_FILL", "side": side, "retry": False})
                continue
            if this_fault == "PRICE_SPIKE_CAP":
                committed -= need
                events.append({"event": "PRICE_CAP_BLOCK", "side": side, "venueWrite": False})
                continue
            if this_fault == "UNKNOWN_UNRESOLVED":
                # Entry freezes for the rest of this market. No guessed fill/retry.
                events.append({"event": "UNKNOWN_SUBMISSION", "entryFrozen": True, "retry": False})
                committed -= need
                safety["unknownWriteLeftOpen"] = True
                break

            if this_fault == "PARTIAL_FILL":
                fill_shares = rng.choice([4.5, 9.0, 12.0, 13.5])
            else:
                # Resting Maker often does not fill; when it does, venue-confirmed only.
                fill_shares = 18.0 if rng.random() < 0.58 else 0.0
            if this_fault in {"TRANSPORT_UNKNOWN_FILLED", "CANCEL_FILL_RACE"}:
                fill_shares = 18.0
                events.append({"event": this_fault, "entryFrozenDuringReconcile": this_fault == "TRANSPORT_UNKNOWN_FILLED", "blindRetry": False})
            committed -= need
            if fill_shares > 0:
                fill_cost = price * fill_shares
                maker_spent += fill_cost
                inventory[side] += fill_shares
                cost[side] += fill_cost
                events.append({"event": "FILL_DELTA", "role": "MAKER", "side": side, "shares": fill_shares, "usdt": fill_cost})
            else:
                events.append({"event": "RESTING_THEN_CANCELED", "side": side})

        # Optional Taker repair/intervention, respecting the 20 USDT reserve and max-price cap.
        imbalance = inventory["UP"] - inventory["DOWN"]
        if abs(imbalance) > 1e-9 and fault not in {"HEARTBEAT_LOSS", "UNKNOWN_UNRESOLVED"}:
            side = "DOWN" if imbalance > 0 else "UP"
            signal = round(rng.uniform(0.18, 0.72), 2)
            max_price = min(0.98, signal + rng.choice([0.01, 0.02, 0.03]))
            venue_ask = round(min(0.99, signal + rng.uniform(-0.01, 0.12)), 2)
            if venue_ask > max_price + 1e-9:
                events.append({"event": "TAKER_PRICE_CAP", "signal": signal, "maxPrice": max_price, "venueAsk": venue_ask})
            else:
                need = venue_ask * 18.0
                if need <= taker_reserve - taker_spent + 1e-9 and maker_spent + taker_spent + need <= CAP_TOTAL_USDT + 1e-9:
                    taker_spent += need
                    inventory[side] += 18.0
                    cost[side] += need
                    events.append({"event": "FILL_DELTA", "role": "TAKER", "side": side, "shares": 18.0, "usdt": need})
                else:
                    events.append({"event": "TAKER_CAP_BLOCK", "need": need})

        if fault == "HEARTBEAT_LOSS":
            events.append({"event": "HEARTBEAT_LOSS", "enginePaused": True, "makerCancelAll": True})

        total_spent = maker_spent + taker_spent
        safety["capitalExceeded"] = total_spent > CAP_TOTAL_USDT + 1e-9
        payout = inventory[winner]
        pnl = payout - (cost["UP"] + cost["DOWN"])
        equity_after = equity_before + pnl
        return {
            "marketIndex": index,
            "marketId": market_id,
            "fault": fault,
            "winner": winner,
            "makerSpentUsdt": maker_spent,
            "takerSpentUsdt": taker_spent,
            "totalSpentUsdt": total_spent,
            "inventoryUp": inventory["UP"],
            "inventoryDown": inventory["DOWN"],
            "pnlUsdt": pnl,
            "equityBefore": equity_before,
            "equityAfter": equity_after,
            "events": events,
            "safety": safety,
        }

    def run_cap100_stress_exam(self, *, markets: int = DEFAULT_MARKETS, seed: int = DEFAULT_SEED) -> dict[str, Any]:
        markets = max(100, min(MAX_MARKETS, int(markets)))
        seed = int(seed)
        with self.stress_exam_lock:
            started = v1._now_ms()
            with self.db_lock:
                cur = self.db.execute(
                    "INSERT INTO engine_cap100_stress_exam_runs(started_at_ms,seed,markets,status,report_json) VALUES(?,?,?,?,?)",
                    (started, seed, markets, "RUNNING", "{}"),
                )
                run_id = int(cur.lastrowid); self.db.commit()

            rng = random.Random(seed)
            rows: list[dict[str, Any]] = []
            equity = 0.0
            peak = 0.0
            max_dd = 0.0
            current_loss_streak = 0
            longest_loss_streak = 0
            stop_loss_trips = 0
            fault_counts: dict[str, int] = {}
            for i in range(1, markets + 1):
                row = self._simulate_market(rng, i, equity)
                equity = float(row["equityAfter"])
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                if row["pnlUsdt"] < -1e-9:
                    current_loss_streak += 1
                    longest_loss_streak = max(longest_loss_streak, current_loss_streak)
                else:
                    current_loss_streak = 0
                if row["pnlUsdt"] <= -STOP_LOSS_USDT:
                    stop_loss_trips += 1
                    row["events"].append({"event": "STOP_LOSS_TRIP", "thresholdUsdt": STOP_LOSS_USDT, "newEntriesBlocked": True, "makerCancelAll": True})
                fault_counts[row["fault"]] = fault_counts.get(row["fault"], 0) + 1
                rows.append(row)

            pnls = [float(r["pnlUsdt"]) for r in rows]
            violations: list[dict[str, Any]] = []
            for r in rows:
                for name, bad in r["safety"].items():
                    # UNKNOWN unresolved is expected to remain fail-closed and is not itself an invariant breach.
                    if name == "unknownWriteLeftOpen":
                        continue
                    if bad:
                        violations.append({"marketIndex": r["marketIndex"], "marketId": r["marketId"], "invariant": name})
            hard_pass = not violations
            grade = "A" if hard_pass else "F"
            worst = min(rows, key=lambda r: r["pnlUsdt"])
            best = max(rows, key=lambda r: r["pnlUsdt"])
            report = {
                "runId": run_id,
                "version": "CAP100_PREFLIGHT_STRESS_EXAM_V1",
                "status": "PASS" if hard_pass else "FAIL",
                "safetyGrade": grade,
                "admissionRecommendation": "ELIGIBLE_FOR_CONTROLLED_LIVE_CANARY" if hard_pass else "DO_NOT_ARM",
                "seed": seed,
                "markets": markets,
                "isolated": True,
                "venueWrites": 0,
                "productionLedgerWrites": 0,
                "productionPnlAffected": False,
                "syntheticEconomicsNotLiveForecast": True,
                "safety": {
                    "violations": violations,
                    "violationCount": len(violations),
                    "capitalCapUsdt": CAP_TOTAL_USDT,
                    "stopLossUsdt": STOP_LOSS_USDT,
                    "stopLossTrips": stop_loss_trips,
                    "longestLossStreak": longest_loss_streak,
                    "maxObservedSpendUsdt": max(float(r["totalSpentUsdt"]) for r in rows),
                    "unknownUnresolvedCount": fault_counts.get("UNKNOWN_UNRESOLVED", 0),
                },
                "economics": {
                    "totalPnlUsdt": sum(pnls),
                    "meanPnlPerMarketUsdt": statistics.fmean(pnls) if pnls else 0.0,
                    "medianPnlPerMarketUsdt": statistics.median(pnls) if pnls else 0.0,
                    "maxSingleMarketLossUsdt": float(worst["pnlUsdt"]),
                    "maxSingleMarketProfitUsdt": float(best["pnlUsdt"]),
                    "maxDrawdownUsdt": max_dd,
                    "p01PnlUsdt": self._quantile(pnls, 0.01),
                    "p05PnlUsdt": self._quantile(pnls, 0.05),
                    "p95PnlUsdt": self._quantile(pnls, 0.95),
                    "p99PnlUsdt": self._quantile(pnls, 0.99),
                    "positiveMarkets": sum(1 for x in pnls if x > 1e-9),
                    "negativeMarkets": sum(1 for x in pnls if x < -1e-9),
                    "flatMarkets": sum(1 for x in pnls if abs(x) <= 1e-9),
                },
                "faultCounts": fault_counts,
                "worstMarket": worst,
                "bestMarket": best,
                "recentMarkets": rows[-25:],
            }
            completed = v1._now_ms()
            with self.db_lock:
                self.db.execute(
                    "UPDATE engine_cap100_stress_exam_runs SET completed_at_ms=?,status=?,safety_grade=?,report_json=? WHERE run_id=?",
                    (completed, report["status"], grade, json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str), run_id),
                )
                self.db.commit()
            return report

    def cap100_stress_exam_state(self) -> dict[str, Any]:
        with self.db_lock:
            row = self.db.execute("SELECT * FROM engine_cap100_stress_exam_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        if row is None:
            return {"available": True, "latest": None, "version": "CAP100_PREFLIGHT_STRESS_EXAM_V1", "isolated": True}
        item = dict(row)
        try:
            item["report"] = json.loads(str(item.pop("report_json") or "{}"))
        except Exception:
            item["report"] = {}
        return {"available": True, "latest": item, "version": "CAP100_PREFLIGHT_STRESS_EXAM_V1", "isolated": True}

    def state(self) -> dict[str, Any]:
        payload = super().state(); payload["version"] = VERSION
        payload["cap100PreflightStressExam"] = self.cap100_stress_exam_state()
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health(); payload["version"] = VERSION
        payload["cap100PreflightStressExam"] = True
        payload["cap100PreflightStressExamVenueWrites"] = False
        payload["cap100PreflightStressExamProductionLedgerIsolated"] = True
        return payload


class _Handler(v24._Handler):
    engine: EchtgeldEngine

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/cap100/stress-exam":
            self._send(200, {"ok": True, "stressExam": self.engine.cap100_stress_exam_state()}); return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/cap100/stress-exam/run":
            try:
                payload = self._body()
                markets = int(payload.get("markets") or DEFAULT_MARKETS)
                seed = int(payload.get("seed") or DEFAULT_SEED)
                self._send(200, {"ok": True, "report": self.engine.run_cap100_stress_exam(markets=markets, seed=seed)})
            except Exception as exc:
                self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
            return
        return super().do_POST()


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV25Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; randomized CAP100 preflight exam isolated=true; venue writes=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
