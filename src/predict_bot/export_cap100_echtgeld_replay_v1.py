from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPLAY_DB = ROOT / "data" / "cap100_echtgeld_replay_v1.db"
DECISION_DB = ROOT / "data" / "strategy_cap100_echtgeld_v1.db"
OFFICIAL_DB = ROOT / "data" / "target_wallet_official_v1.db"
OUT_DIR = ROOT / "data" / "research" / "cap100_echtgeld_replay_v1"


def _loads(text: Any) -> Any:
    try: return json.loads(str(text or "{}"))
    except Exception: return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default=str(OUT_DIR))
    args = ap.parse_args()
    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    if not REPLAY_DB.exists(): raise SystemExit(f"missing replay DB: {REPLAY_DB}")

    replay = sqlite3.connect(REPLAY_DB); replay.row_factory = sqlite3.Row
    events = [dict(r) for r in replay.execute("SELECT * FROM live_replay_events ORDER BY seq")]
    decisions: dict[str, dict[str, Any]] = {}
    if DECISION_DB.exists():
        db = sqlite3.connect(DECISION_DB); db.row_factory = sqlite3.Row
        for r in db.execute("SELECT * FROM our_decisions"):
            x = dict(r); decisions[str(x["decision_id"])] = x
        db.close()
    winners: dict[int, dict[str, Any]] = {}
    if OFFICIAL_DB.exists():
        db = sqlite3.connect(OFFICIAL_DB); db.row_factory = sqlite3.Row
        try:
            for r in db.execute("SELECT market_id,winner,status,resolved_at_ms FROM target_markets WHERE status='SETTLED' AND winner IN ('UP','DOWN')"):
                x = dict(r); winners[int(x["market_id"])] = x
        finally: db.close()

    cid_to_decision: dict[str, str] = {}
    for e in events:
        if e.get("client_order_id") and e.get("decision_id"):
            cid_to_decision[str(e["client_order_id"])] = str(e["decision_id"])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = out_dir / f"cap100_live_replay_{stamp}.jsonl"
    linked = 0; settled = 0; markets: set[int] = set()
    with jsonl_path.open("w", encoding="utf-8") as f:
        for e in events:
            payload = _loads(e.pop("payload_json", "{}"))
            cid = str(e.get("client_order_id") or "")
            did = str(e.get("decision_id") or cid_to_decision.get(cid) or "")
            decision = decisions.get(did)
            decision_view = None
            if decision:
                linked += 1
                decision_view = {
                    "decisionId": did,
                    "decisionMs": decision.get("decision_ms"),
                    "secondsLeft": decision.get("seconds_left"),
                    "phase": decision.get("phase"),
                    "desiredPortfolioAction": decision.get("desired_portfolio_action"),
                    "executionChoice": decision.get("execution_choice"),
                    "primaryReason": decision.get("primary_reason"),
                    "direction": _loads(decision.get("direction_state_json")),
                    "portfolio": _loads(decision.get("portfolio_state_json")),
                    "economics": _loads(decision.get("economics_state_json")),
                    "arbitration": _loads(decision.get("arbitration_state_json")),
                    "publicState": _loads(decision.get("public_state_json")),
                }
            mid = int(e.get("market_id") or 0)
            if mid: markets.add(mid)
            settlement = winners.get(mid)
            if settlement: settled += 1
            row = {
                "schema": "CAP100_LIVE_REPLAY_TRAINING_V1",
                "realExecution": True,
                "synthetic": False,
                "targetBehaviorData": False,
                "executionEvent": e,
                "eventPayload": payload,
                "decisionContext": decision_view,
                "posthocSettlementLabel": settlement,
            }
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")

    report = {
        "version": "CAP100_LIVE_REPLAY_TRAINING_V1",
        "createdAtUtc": stamp,
        "events": len(events),
        "markets": len(markets),
        "decisionRowsAvailable": len(decisions),
        "eventsLinkedToDecision": linked,
        "eventsWithPosthocSettlementLabel": settled,
        "syntheticIncluded": False,
        "targetBehaviorDataIncluded": False,
        "sources": {"executionReplay": str(REPLAY_DB), "controllerDecisions": str(DECISION_DB), "officialSettlementLabels": str(OFFICIAL_DB)},
        "output": str(jsonl_path),
    }
    (out_dir / f"cap100_live_replay_{stamp}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
