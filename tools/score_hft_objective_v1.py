from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path


def extract_pnl(obj: dict) -> float:
    for path in (
        ("candidate", "pnlDiagnosticOnly"),
        ("candidate", "pnl"),
        ("pnlDiagnosticOnly",),
        ("pnl",),
    ):
        cur = obj
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok and cur is not None:
            return float(cur)
    raise KeyError("no candidate/terminal PnL field")


def extract_int(obj: dict, *names: str) -> int:
    for name in names:
        if isinstance(obj.get("candidate"), dict) and obj["candidate"].get(name) is not None:
            return int(obj["candidate"][name])
        if obj.get(name) is not None:
            return int(obj[name])
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--output")
    a = ap.parse_args()

    files: list[Path] = []
    for raw in a.paths:
        matched = [Path(x) for x in glob.glob(raw)]
        files.extend(matched if matched else [Path(raw)])

    rows = []
    for p in files:
        obj = json.loads(p.read_text(encoding="utf-8"))
        pnl = extract_pnl(obj)
        mid = obj.get("marketId") or obj.get("market_id") or p.parent.name
        fills = extract_int(obj, "fills", "actualFillEvents")
        rounds = extract_int(obj, "rounds", "semanticRounds", "v70dSemanticRounds")
        rows.append({"marketId": mid, "pnl": pnl, "fills": fills, "rounds": rounds, "source": str(p)})

    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] < 0]
    flats = [r for r in rows if abs(r["pnl"]) <= 1e-12]
    n = len(rows)
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(r["pnl"] for r in wins) / len(wins) if wins else None
    avg_loss = sum(r["pnl"] for r in losses) / len(losses) if losses else None
    worst = min((r["pnl"] for r in rows), default=None)
    agg = sum(r["pnl"] for r in rows)
    coverage = sum(r["fills"] > 0 for r in rows) / n if n else 0.0

    gates = {
        "averageWinningMarketPnlGt2": avg_win is not None and avg_win > 2.0,
        "everyLosingMarketLossMagnitudeLt1": not losses or min(r["pnl"] for r in losses) > -1.0,
        "winRateGt50Pct": win_rate > 0.50,
    }
    out = {
        "version": "BTC5M_HFT_OBJECTIVE_SCORER_V1",
        "markets": n,
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(flats),
        "winRate": win_rate,
        "averageWinningMarketPnl": avg_win,
        "averageLosingMarketPnl": avg_loss,
        "worstMarketPnl": worst,
        "aggregatePnl": agg,
        "tradeCoverage": coverage,
        "totalFills": sum(r["fills"] for r in rows),
        "totalSemanticRounds": sum(r["rounds"] for r in rows),
        "gates": gates,
        "objectivePass": all(gates.values()),
        "rows": rows,
    }
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
