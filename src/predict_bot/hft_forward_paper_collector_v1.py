from __future__ import annotations

import json
import lzma
import math
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HOST = os.environ.get("HFT_FORWARD_PAPER_HOST", "127.0.0.1")
PORT = int(os.environ.get("HFT_FORWARD_PAPER_PORT", "8788"))
POLL_SECONDS = max(2.0, float(os.environ.get("HFT_FORWARD_PAPER_POLL_SECONDS", "10")))
ENTRY_LATENCY_MS = int(os.environ.get("HFT_FORWARD_PAPER_ENTRY_LATENCY_MS", "1092"))
RESPONSE_LATENCY_MS = int(os.environ.get("HFT_FORWARD_PAPER_RESPONSE_LATENCY_MS", "273"))
QUEUE_MODEL = os.environ.get("HFT_FORWARD_PAPER_QUEUE_MODEL", "risk")
TRADE_OFFSET = os.environ.get("HFT_FORWARD_PAPER_TRADE_OFFSET", "mid")
TAKER_CONFIRM_MS = int(os.environ.get("HFT_FORWARD_PAPER_TAKER_CONFIRM_MS", "2200"))
DB_PATH = Path(os.environ.get("HFT_FORWARD_PAPER_DB", ROOT / "data" / "hft_forward_paper_v1.db"))
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
STRATEGY_DB = ROOT / "data" / "strategy_target_compare_v1.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
ARCHIVE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"
REPORT_DIR = ROOT / "data" / "hft_forward_paper_v1" / "markets"
VERSION = "HFT_FORWARD_PAPER_COLLECTOR_V1"
EXECUTION_LABEL = "HFTBACKTEST_PREDICT_TAPE_CLOSED_LOOP_FORWARD_PAPER"
R2_VERSION = "UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER"
CAP_VERSION = "UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER"


def now_ms() -> int:
    return int(time.time() * 1000)


def _connect(path: Path, *, ro: bool = False) -> sqlite3.Connection:
    if ro:
        con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=20)
        con.execute("PRAGMA query_only=ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=20, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=10000")
    con.row_factory = sqlite3.Row
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS hft_forward_meta_v1 (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at_ms INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hft_forward_runs_v1 (
          strategy_key TEXT NOT NULL,
          strategy_version TEXT NOT NULL,
          market_id INTEGER NOT NULL,
          window_end_ms INTEGER,
          status TEXT NOT NULL,
          tape_quality_status TEXT,
          tape_archive_path TEXT,
          report_archive_path TEXT,
          execution_evidence_label TEXT,
          entry_latency_ms INTEGER NOT NULL,
          response_latency_ms INTEGER NOT NULL,
          queue_model TEXT NOT NULL,
          trade_offset TEXT NOT NULL,
          started_at_ms INTEGER,
          completed_at_ms INTEGER,
          last_attempt_at_ms INTEGER,
          attempt_count INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          decision_count INTEGER,
          maker_placements INTEGER,
          maker_fill_events INTEGER,
          maker_filled_shares REAL,
          taker_fills INTEGER,
          taker_filled_shares REAL,
          maker_up_shares REAL,
          maker_down_shares REAL,
          taker_up_shares REAL,
          taker_down_shares REAL,
          maker_cost_usdt REAL,
          taker_cost_usdt REAL,
          taker_fees_usdt REAL,
          total_cost_usdt REAL,
          final_maker_net REAL,
          final_abs_net REAL,
          winner TEXT,
          winner_payout_usdt REAL,
          realized_pnl_usdt REAL,
          settled_at_ms INTEGER,
          summary_json TEXT,
          PRIMARY KEY(strategy_key, market_id)
        );
        CREATE INDEX IF NOT EXISTS idx_hft_forward_runs_status_v1
          ON hft_forward_runs_v1(status,market_id);
        CREATE INDEX IF NOT EXISTS idx_hft_forward_runs_market_v1
          ON hft_forward_runs_v1(market_id,strategy_key);
        CREATE TABLE IF NOT EXISTS hft_forward_fills_v1 (
          strategy_key TEXT NOT NULL,
          market_id INTEGER NOT NULL,
          fill_seq INTEGER NOT NULL,
          channel TEXT NOT NULL,
          side TEXT NOT NULL,
          price REAL NOT NULL,
          shares REAL NOT NULL,
          fill_ms INTEGER NOT NULL,
          decision_ms INTEGER,
          order_id TEXT,
          hft_status TEXT,
          payload_json TEXT NOT NULL,
          PRIMARY KEY(strategy_key,market_id,fill_seq)
        );
        CREATE INDEX IF NOT EXISTS idx_hft_forward_fills_market_v1
          ON hft_forward_fills_v1(market_id,strategy_key,fill_ms);
        """
    )
    con.commit()


def _meta_get(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM hft_forward_meta_v1 WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def _meta_set(con: sqlite3.Connection, key: str, value: Any) -> None:
    payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    con.execute(
        "INSERT INTO hft_forward_meta_v1(key,value,updated_at_ms) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at_ms=excluded.updated_at_ms",
        (key, payload, now_ms()),
    )
    con.commit()


def _archive_report(strategy_key: str, market_id: int, report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{int(market_id)}_{strategy_key.lower()}_hft_closed_loop_v1.json.xz"
    raw = json.dumps(report, ensure_ascii=False, separators=(",", ":"), allow_nan=True).encode("utf-8")
    with lzma.open(path, "wb", preset=6) as f:
        f.write(raw)
    return path


def _snapshot_count(version: str, market_id: int) -> int:
    try:
        con = _connect(STRATEGY_DB, ro=True)
        try:
            return int(con.execute(
                "SELECT COUNT(*) FROM our_decisions WHERE strategy_version=? AND market_id=?",
                (version, int(market_id)),
            ).fetchone()[0])
        finally:
            con.close()
    except Exception:
        return 0


def _latest_archive_boundary() -> tuple[int, int | None]:
    files = sorted(ARCHIVE_DIR.glob("*.json.xz"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    if not files:
        return 0, None
    from src.predict_bot.execution_tape_archive_v1 import load_archive
    for path in files[:20]:
        try:
            tape = load_archive(path)
            market = tape.get("market") or {}
            end = int(market.get("window_end_ms") or 0)
            if end > 0:
                return end, int(tape.get("marketId") or path.name.split(".",1)[0])
        except Exception:
            continue
    return 0, None


def _archive_candidates(
    boundary_end_ms: int,
    activated_at_ms: int,
    *,
    assessment_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] | None = None,
    skip_market_ids: set[int] | None = None,
    scan_stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from src.predict_bot.execution_tape_quality_v1 import assess_archive

    started = time.monotonic()
    # Candidate order is normalized by window_end_ms below, so avoid a second
    # filesystem stat for every archive just to pre-sort paths.
    files = list(ARCHIVE_DIR.glob("*.json.xz"))
    out: list[dict[str, Any]] = []
    live_cache_keys: set[str] = set()
    assessed = 0
    cache_hits = 0
    skipped_complete = 0
    # Durable archive files are the source of truth. The DB quality index may be stale after collector restarts.
    for path in files:
        try:
            stat = path.stat()
            mtime_ms = int(stat.st_mtime_ns // 1_000_000)
            if mtime_ms < int(activated_at_ms) - 5_000:
                continue
            try:
                market_hint = int(path.name.split(".", 1)[0])
            except (TypeError, ValueError):
                market_hint = None
            if market_hint is not None and skip_market_ids and market_hint in skip_market_ids:
                skipped_complete += 1
                if assessment_cache is not None:
                    assessment_cache.pop(str(path), None)
                continue

            cache_key = str(path)
            fingerprint = (int(stat.st_mtime_ns), int(stat.st_size))
            live_cache_keys.add(cache_key)
            cached = assessment_cache.get(cache_key) if assessment_cache is not None else None
            if cached is not None and cached[0] == fingerprint:
                q = cached[1]
                cache_hits += 1
            else:
                q = assess_archive(path)
                assessed += 1
                if assessment_cache is not None:
                    assessment_cache[cache_key] = (fingerprint, dict(q))
            end = int(q.get("windowEndMs") or 0)
            if end <= int(boundary_end_ms):
                continue
            # Archive assessment is read-only here. The 8778 capture service owns its SQLite quality index.
            if q.get("qualityStatus") != "COMPLETE_FORWARD_V1" or not bool(q.get("eligibleExecutionTraining")):
                continue
            out.append({
                "market_id": int(q["marketId"]),
                "window_end_ms": end,
                "quality_status": str(q["qualityStatus"]),
                "archive_path": str(path),
                "archive_mtime_ms": mtime_ms,
            })
        except Exception:
            continue
    if assessment_cache is not None:
        for cache_key in set(assessment_cache) - live_cache_keys:
            assessment_cache.pop(cache_key, None)
    out.sort(key=lambda x: (int(x["window_end_ms"]), int(x["market_id"])))
    if scan_stats is not None:
        scan_stats.update({
            "archiveFiles": len(files),
            "assessedArchives": assessed,
            "assessmentCacheHits": cache_hits,
            "skippedCompletedMarkets": skipped_complete,
            "candidateMarkets": len(out),
            "durationMs": int(round((time.monotonic() - started) * 1000.0)),
        })
    return out


def _initial_boundary() -> int:
    end, _ = _latest_archive_boundary()
    return int(end)


def _run_r2(market_id: int) -> dict[str, Any]:
    from tools import hftbacktest_r2_execution_school_v0 as r2
    report = r2.run_market(
        int(market_id),
        entry_latency_ms=ENTRY_LATENCY_MS,
        response_latency_ms=RESPONSE_LATENCY_MS,
        queue_model=QUEUE_MODEL,
        trade_offset=TRADE_OFFSET,
        taker_confirm_ms=TAKER_CONFIRM_MS,
    )
    report["executionEvidenceLabel"] = EXECUTION_LABEL
    return report


def _run_cap100(market_id: int) -> dict[str, Any]:
    from tools import hftbacktest_cap100_closed_loop_v0 as cap
    report = cap.run_market(
        int(market_id),
        entry_latency_ms=ENTRY_LATENCY_MS,
        response_latency_ms=RESPONSE_LATENCY_MS,
        queue_model=QUEUE_MODEL,
        trade_offset=TRADE_OFFSET,
        taker_mode="hft",
        diagnostic_only=False,
    )
    report["executionEvidenceLabel"] = EXECUTION_LABEL
    return report


def _summary(strategy_key: str, report: dict[str, Any]) -> dict[str, Any]:
    if strategy_key == "R2":
        roll = report.get("studentRollout") or {}
        port = roll.get("finalPortfolio") or {}
        maker_up = float(port.get("maker_gross", 0.0) + port.get("maker_net", 0.0)) / 2.0
        maker_down = float(port.get("maker_gross", 0.0) - port.get("maker_net", 0.0)) / 2.0
        taker_up = float(port.get("taker_gross", 0.0) + port.get("taker_net", 0.0)) / 2.0
        taker_down = float(port.get("taker_gross", 0.0) - port.get("taker_net", 0.0)) / 2.0
        maker_cost = float(roll.get("makerCostUsdt") or 0.0)
        taker_cost = float(roll.get("takerCostUsdt") or 0.0)
        fees = float(roll.get("takerFeesUsdt") or 0.0)
        return {
            "decisionCount": int(roll.get("decisions") or 0),
            "makerPlacements": int(roll.get("makerPlacements") or 0),
            "makerFillEvents": int(roll.get("makerFillEvents") or 0),
            "makerFilledShares": float(roll.get("makerFilledShares") or 0.0),
            "takerFills": int(roll.get("takerFills") or 0),
            "takerFilledShares": float(roll.get("takerFilledShares") or 0.0),
            "makerUpShares": maker_up, "makerDownShares": maker_down,
            "takerUpShares": taker_up, "takerDownShares": taker_down,
            "makerCostUsdt": maker_cost, "takerCostUsdt": taker_cost, "takerFeesUsdt": fees,
            "totalCostUsdt": maker_cost + taker_cost + fees,
            "finalMakerNet": float(port.get("maker_net") or 0.0),
            "finalAbsNet": float(port.get("combined_abs_net") or 0.0),
            "runMetrics": roll.get("runMetrics") or {},
            "submitRejects": len(roll.get("submitRejects") or []),
        }
    closed = report.get("closedLoop") or {}
    ledger = closed.get("ledger") or {}
    port = closed.get("finalPortfolio") or {}
    maker_cost = float(ledger.get("makerCostUsdt") or 0.0)
    taker_cost = float(ledger.get("takerCostUsdt") or 0.0)
    fees = float(ledger.get("takerFeesUsdt") or 0.0)
    return {
        "decisionCount": int(closed.get("decisions") or 0),
        "makerPlacements": int(closed.get("makerPlacements") or 0),
        "makerFillEvents": int(closed.get("makerFillEvents") or 0),
        "makerFilledShares": float(closed.get("makerFilledShares") or 0.0),
        "takerFills": int(closed.get("takerFills") or 0),
        "takerFilledShares": float(sum(float(x.get("shares") or 0.0) for x in report.get("takerEvents") or [])),
        "makerUpShares": float(ledger.get("makerUpShares") or 0.0),
        "makerDownShares": float(ledger.get("makerDownShares") or 0.0),
        "takerUpShares": float(ledger.get("takerUpShares") or 0.0),
        "takerDownShares": float(ledger.get("takerDownShares") or 0.0),
        "makerCostUsdt": maker_cost, "takerCostUsdt": taker_cost, "takerFeesUsdt": fees,
        "totalCostUsdt": maker_cost + taker_cost + fees,
        "finalMakerNet": float(port.get("maker_net") or 0.0),
        "finalAbsNet": float(port.get("combined_abs_net") or 0.0),
        "runMetrics": closed.get("runMetrics") or {},
        "submitRejects": len(closed.get("submitRejects") or []),
    }


def _fill_rows(strategy_key: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in report.get("makerFillEvents") or []:
        out.append({
            "channel": "MAKER", "side": str(row.get("side")), "price": float(row.get("price")),
            "shares": float(row.get("deltaShares") or row.get("shares") or 0.0),
            "fillMs": int(row.get("atMs") or 0), "decisionMs": None,
            "orderId": row.get("orderId"), "hftStatus": row.get("status") or row.get("hftStatus"),
            "payload": row,
        })
    for row in report.get("takerEvents") or []:
        out.append({
            "channel": "TAKER", "side": str(row.get("side")), "price": float(row.get("price")),
            "shares": float(row.get("shares") or 0.0), "fillMs": int(row.get("atMs") or 0),
            "decisionMs": int(row.get("decisionMs")) if row.get("decisionMs") is not None else None,
            "orderId": None, "hftStatus": row.get("hftStatus"), "payload": row,
        })
    out.sort(key=lambda x: (x["fillMs"], 0 if x["channel"] == "MAKER" else 1))
    return out


class Collector:
    def __init__(self) -> None:
        if os.environ.get("PREDICT_LIVE_ENABLED", "false").lower() not in {"0", "false", "no", "off", ""}:
            raise RuntimeError("HFT forward PAPER collector refuses to start while PREDICT_LIVE_ENABLED is true")
        self.db = _connect(DB_PATH)
        ensure_schema(self.db)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started_at_ms = now_ms()
        self.last_loop_ms: int | None = None
        self.last_error: str | None = None
        self.current_job: dict[str, Any] | None = None
        self.archive_assessment_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self.last_archive_scan: dict[str, Any] = {}
        boundary_raw = _meta_get(self.db, "activation_after_window_end_ms")
        activated_raw = _meta_get(self.db, "activated_at_ms")
        if boundary_raw is None:
            boundary = _initial_boundary()
            _meta_set(self.db, "activation_after_window_end_ms", str(boundary))
            _meta_set(self.db, "activated_at_ms", str(self.started_at_ms))
            _meta_set(self.db, "policy", {
                "officialPaperExecution": "HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP",
                "legacyQueueclear": "DIAGNOSTIC_ONLY_NOT_OFFICIAL_FORWARD_EVIDENCE",
                "entryLatencyMs": ENTRY_LATENCY_MS,
                "responseLatencyMs": RESPONSE_LATENCY_MS,
                "queueModel": QUEUE_MODEL,
                "tradeOffset": TRADE_OFFSET,
                "takerConfirmMs": TAKER_CONFIRM_MS,
            })
            self.activation_after_window_end_ms = boundary
            self.activated_at_ms = self.started_at_ms
        else:
            self.activation_after_window_end_ms = int(boundary_raw)
            self.activated_at_ms = int(activated_raw or self.started_at_ms)

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.db.close()
        except Exception:
            pass

    def start(self) -> None:
        threading.Thread(target=self._loop, name="hft-forward-paper-v1", daemon=True).start()

    def _run_exists(self, strategy_key: str, market_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM hft_forward_runs_v1 WHERE strategy_key=? AND market_id=?",
            (strategy_key, int(market_id)),
        ).fetchone()

    def _eligible_to_attempt(self, strategy_key: str, market_id: int) -> bool:
        row = self._run_exists(strategy_key, market_id)
        if row is None:
            return True
        if str(row["status"]) == "COMPLETE":
            return False
        last = int(row["last_attempt_at_ms"] or 0)
        attempts = int(row["attempt_count"] or 0)
        delay = min(300_000, 15_000 * max(1, attempts))
        return now_ms() - last >= delay

    def _record_start(self, strategy_key: str, strategy_version: str, market_id: int, window_end_ms: int, quality: str) -> None:
        at = now_ms()
        self.db.execute(
            """INSERT INTO hft_forward_runs_v1(
                 strategy_key,strategy_version,market_id,window_end_ms,status,tape_quality_status,tape_archive_path,
                 execution_evidence_label,entry_latency_ms,response_latency_ms,queue_model,trade_offset,
                 started_at_ms,last_attempt_at_ms,attempt_count)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(strategy_key,market_id) DO UPDATE SET
                 status='RUNNING',last_attempt_at_ms=excluded.last_attempt_at_ms,
                 attempt_count=hft_forward_runs_v1.attempt_count+1,error=NULL""",
            (strategy_key, strategy_version, int(market_id), int(window_end_ms), "RUNNING", quality,
             str(ARCHIVE_DIR / f"{int(market_id)}.json.xz"),
             EXECUTION_LABEL,
             ENTRY_LATENCY_MS, RESPONSE_LATENCY_MS, QUEUE_MODEL, TRADE_OFFSET, at, at),
        )
        self.db.commit()

    def _record_failure(self, strategy_key: str, market_id: int, exc: Exception) -> None:
        self.db.execute(
            "UPDATE hft_forward_runs_v1 SET status='RETRYABLE_ERROR',error=?,last_attempt_at_ms=? WHERE strategy_key=? AND market_id=?",
            (f"{type(exc).__name__}: {str(exc)[:1000]}", now_ms(), strategy_key, int(market_id)),
        )
        self.db.commit()

    def _record_complete(self, strategy_key: str, market_id: int, report: dict[str, Any]) -> None:
        summary = _summary(strategy_key, report)
        report_path = _archive_report(strategy_key, market_id, report)
        at = now_ms()
        self.db.execute(
            """UPDATE hft_forward_runs_v1 SET status='COMPLETE',completed_at_ms=?,error=NULL,
                 report_archive_path=?,decision_count=?,maker_placements=?,maker_fill_events=?,maker_filled_shares=?,
                 taker_fills=?,taker_filled_shares=?,maker_up_shares=?,maker_down_shares=?,taker_up_shares=?,taker_down_shares=?,
                 maker_cost_usdt=?,taker_cost_usdt=?,taker_fees_usdt=?,total_cost_usdt=?,final_maker_net=?,final_abs_net=?,summary_json=?
               WHERE strategy_key=? AND market_id=?""",
            (at, str(report_path), summary["decisionCount"], summary["makerPlacements"], summary["makerFillEvents"], summary["makerFilledShares"],
             summary["takerFills"], summary["takerFilledShares"], summary["makerUpShares"], summary["makerDownShares"],
             summary["takerUpShares"], summary["takerDownShares"], summary["makerCostUsdt"], summary["takerCostUsdt"],
             summary["takerFeesUsdt"], summary["totalCostUsdt"], summary["finalMakerNet"], summary["finalAbsNet"],
             json.dumps(summary, ensure_ascii=False, separators=(",", ":"), allow_nan=True), strategy_key, int(market_id)),
        )
        self.db.execute("DELETE FROM hft_forward_fills_v1 WHERE strategy_key=? AND market_id=?", (strategy_key, int(market_id)))
        for seq, row in enumerate(_fill_rows(strategy_key, report), start=1):
            self.db.execute(
                """INSERT INTO hft_forward_fills_v1(
                     strategy_key,market_id,fill_seq,channel,side,price,shares,fill_ms,decision_ms,order_id,hft_status,payload_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (strategy_key, int(market_id), seq, row["channel"], row["side"], row["price"], row["shares"], row["fillMs"],
                 row["decisionMs"], row["orderId"], row["hftStatus"], json.dumps(row["payload"], ensure_ascii=False, separators=(",", ":"), allow_nan=True)),
            )
        self.db.commit()

    def _refresh_settlements(self) -> None:
        rows = self.db.execute(
            "SELECT DISTINCT market_id FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NULL"
        ).fetchall()
        if not rows or not TARGET_DB.exists():
            return
        target = _connect(TARGET_DB, ro=True)
        try:
            for rr in rows:
                mid = int(rr[0])
                tr = target.execute(
                    "SELECT winner,resolved_at_ms FROM target_markets WHERE market_id=? AND asset='BTC' AND winner IN ('UP','DOWN')",
                    (mid,),
                ).fetchone()
                if tr is None:
                    continue
                winner = str(tr["winner"])
                for run in self.db.execute(
                    "SELECT * FROM hft_forward_runs_v1 WHERE market_id=? AND status='COMPLETE'",
                    (mid,),
                ).fetchall():
                    payout = float(run["maker_up_shares"] or 0.0) + float(run["taker_up_shares"] or 0.0) if winner == "UP" else float(run["maker_down_shares"] or 0.0) + float(run["taker_down_shares"] or 0.0)
                    cost = float(run["total_cost_usdt"] or 0.0)
                    pnl = payout - cost
                    self.db.execute(
                        "UPDATE hft_forward_runs_v1 SET winner=?,winner_payout_usdt=?,realized_pnl_usdt=?,settled_at_ms=? WHERE strategy_key=? AND market_id=?",
                        (winner, payout, pnl, int(tr["resolved_at_ms"] or now_ms()), str(run["strategy_key"]), mid),
                    )
            self.db.commit()
        finally:
            target.close()

    def _process(self, strategy_key: str, version: str, market_id: int, window_end_ms: int, quality: str) -> None:
        self.current_job = {"strategy": strategy_key, "marketId": int(market_id), "startedAtMs": now_ms()}
        self._record_start(strategy_key, version, market_id, window_end_ms, quality)
        try:
            snapshots = _snapshot_count(version, market_id)
            if snapshots <= 0:
                raise RuntimeError(f"no forward public snapshots recorded for {strategy_key} market {market_id}")
            report = _run_r2(market_id) if strategy_key == "R2" else _run_cap100(market_id)
            self._record_complete(strategy_key, market_id, report)
        except Exception as exc:
            self._record_failure(strategy_key, market_id, exc)
            raise
        finally:
            self.current_job = None

    def _tick(self) -> None:
        completed_market_ids = {
            int(row[0])
            for row in self.db.execute(
                """SELECT market_id
                     FROM hft_forward_runs_v1
                    WHERE status='COMPLETE' AND strategy_key IN ('R2','CAP100')
                    GROUP BY market_id
                   HAVING COUNT(DISTINCT strategy_key)=2"""
            ).fetchall()
        }
        scan_stats: dict[str, Any] = {}
        candidates = _archive_candidates(
            self.activation_after_window_end_ms,
            self.activated_at_ms,
            assessment_cache=self.archive_assessment_cache,
            skip_market_ids=completed_market_ids,
            scan_stats=scan_stats,
        )
        self.last_archive_scan = {**scan_stats, "atMs": now_ms()}
        for q in candidates:
            if self.stop_event.is_set():
                break
            mid = int(q["market_id"])
            end = int(q["window_end_ms"] or 0)
            quality = str(q["quality_status"])
            for key, version in (("R2", R2_VERSION), ("CAP100", CAP_VERSION)):
                if not self._eligible_to_attempt(key, mid):
                    continue
                # If this lane was not running for the market, do not synthesize a forward observation.
                if _snapshot_count(version, mid) <= 0:
                    continue
                try:
                    self._process(key, version, mid, end, quality)
                except Exception as exc:
                    self.last_error = f"{key}:{mid}:{type(exc).__name__}: {str(exc)[:700]}"
        self._refresh_settlements()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                with self.lock:
                    self._tick()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
            self.last_loop_ms = now_ms()
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.5, POLL_SECONDS - elapsed))

    def health_snapshot(self) -> dict[str, Any]:
        """Memory-only health response that stays responsive during HFT replay."""
        return {
            "ok": self.last_error is None,
            "status": "ACTIVE" if self.last_error is None else "DEGRADED",
            "version": VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "officialPaperExecution": "HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP",
            "activationAfterWindowEndMs": self.activation_after_window_end_ms,
            "activatedAtMs": self.activated_at_ms,
            "currentJob": dict(self.current_job) if self.current_job else None,
            "lastLoopMs": self.last_loop_ms,
            "lastLoopAgeMs": now_ms() - self.last_loop_ms if self.last_loop_ms else None,
            "lastError": self.last_error,
            "archiveScan": {
                **self.last_archive_scan,
                "assessmentCacheEntries": len(self.archive_assessment_cache),
            },
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            counts = {str(r["status"]): int(r["n"]) for r in self.db.execute(
                "SELECT status,COUNT(*) AS n FROM hft_forward_runs_v1 GROUP BY status"
            ).fetchall()}
            latest = [dict(r) for r in self.db.execute(
                """SELECT strategy_key,market_id,status,completed_at_ms,winner,realized_pnl_usdt,
                          maker_filled_shares,taker_fills,final_abs_net,error
                   FROM hft_forward_runs_v1 ORDER BY market_id DESC,strategy_key LIMIT 8"""
            ).fetchall()]
            settled = self.db.execute(
                "SELECT COUNT(*) FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL"
            ).fetchone()[0]
            return {
                "ok": self.last_error is None,
                "status": "ACTIVE" if self.last_error is None else "DEGRADED",
                "version": VERSION,
                "paperOnly": True,
                "liveOrdersAffected": False,
                "targetDataUsedForDecision": False,
                "targetSettlementPostHocOnly": True,
                "officialPaperExecution": "HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP",
                "legacyQueueclearStatus": "DIAGNOSTIC_ONLY_NOT_OFFICIAL_FORWARD_EVIDENCE",
                "activationAfterWindowEndMs": self.activation_after_window_end_ms,
                "activatedAtMs": self.activated_at_ms,
                "entryLatencyMs": ENTRY_LATENCY_MS,
                "responseLatencyMs": RESPONSE_LATENCY_MS,
                "queueModel": QUEUE_MODEL,
                "tradeOffset": TRADE_OFFSET,
                "takerConfirmMs": TAKER_CONFIRM_MS,
                "database": str(DB_PATH),
                "reportDirectory": str(REPORT_DIR),
                "runCounts": counts,
                "settledRunCount": int(settled),
                "currentJob": self.current_job,
                "archiveScan": {
                    **self.last_archive_scan,
                    "assessmentCacheEntries": len(self.archive_assessment_cache),
                },
                "lastLoopMs": self.last_loop_ms,
                "lastLoopAgeMs": now_ms() - self.last_loop_ms if self.last_loop_ms else None,
                "lastError": self.last_error,
                "latestRuns": latest,
                "boundary": "A market becomes official PAPER evidence only after COMPLETE_FORWARD_V1 Predict tape is archived and HftBacktest closed-loop replay completes. Intra-market QUEUECLEAR_PASS remains legacy intent/diagnostic state only.",
            }


class Handler(BaseHTTPRequestHandler):
    runtime: Collector
    def log_message(self, *_args: Any) -> None:
        return
    def _write(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def do_GET(self) -> None:
        request_path = self.path.split("?", 1)[0]
        if request_path == "/health":
            self._write(self.runtime.health_snapshot())
        elif request_path in {"/", "/state"}:
            self._write(self.runtime.snapshot())
        else:
            self._write({"ok": False, "error": "not found"}, 404)


def main() -> int:
    runtime = Collector()
    runtime.start()
    handler = type("HftForwardPaperHandler", (Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/health; officialPaperExecution=HFTBACKTEST_PREDICT_EXECUTION_TAPE_V1_CLOSED_LOOP; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
