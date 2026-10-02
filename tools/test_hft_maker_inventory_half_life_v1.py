from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from statistics import median
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "hft_forward_paper_v1.db"
OUT = ROOT / "data" / "research" / "hourly_novel_tests" / "hft_maker_inventory_half_life_v1_report.json"
TEST_ID = "HFT_MAKER_INVENTORY_HALF_LIFE_V1"
EPS = 1e-9


def maker_half_life(con: sqlite3.Connection, strategy: str, market_id: int, window_end_ms: int) -> dict | None:
    rows = con.execute(
        "SELECT side,shares,fill_ms FROM hft_forward_fills_v1 "
        "WHERE strategy_key=? AND market_id=? AND channel='MAKER' ORDER BY fill_ms,fill_seq",
        (strategy, market_id),
    ).fetchall()
    if not rows:
        return None
    net = 0.0
    path = []
    for r in rows:
        q = float(r["shares"] or 0.0)
        net += q if str(r["side"]) == "UP" else -q
        path.append((int(r["fill_ms"]), abs(net), net))
    peak = max(x[1] for x in path)
    if peak < 18.0 - EPS:
        return None
    peak_idx = next(i for i, x in enumerate(path) if abs(x[1] - peak) <= EPS)
    peak_ms = path[peak_idx][0]
    threshold = peak * 0.5
    recovery_ms = None
    for ms, absnet, _net in path[peak_idx + 1:]:
        if absnet <= threshold + EPS:
            recovery_ms = ms
            break
    censored = recovery_ms is None
    endpoint_ms = int(window_end_ms) if censored else int(recovery_ms)
    half_life_ms = max(0, endpoint_ms - peak_ms)
    return {
        "peakAbsMakerNet": peak,
        "peakMs": peak_ms,
        "halfThreshold": threshold,
        "recoveryMs": recovery_ms,
        "halfLifeMs": half_life_ms,
        "censored": censored,
    }


def main() -> int:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    runs = con.execute(
        "SELECT strategy_key,market_id,window_end_ms,realized_pnl_usdt,total_cost_usdt "
        "FROM hft_forward_runs_v1 WHERE status='COMPLETE' AND winner IS NOT NULL AND realized_pnl_usdt IS NOT NULL"
    ).fetchall()
    by = {(int(r["market_id"]), str(r["strategy_key"])): r for r in runs}
    mids = sorted({int(r["market_id"]) for r in runs if (int(r["market_id"]), "R2") in by and (int(r["market_id"]), "CAP100") in by})

    obs = []
    for mid in mids:
        r2r = by[(mid, "R2")]
        capr = by[(mid, "CAP100")]
        end = int(r2r["window_end_ms"] or capr["window_end_ms"] or 0)
        if end <= 0:
            continue
        r2 = maker_half_life(con, "R2", mid, end)
        cap = maker_half_life(con, "CAP100", mid, end)
        if r2 is None or cap is None:
            continue
        dpnl = float(capr["realized_pnl_usdt"]) - float(r2r["realized_pnl_usdt"])
        dhl = float(cap["halfLifeMs"] - r2["halfLifeMs"])
        obs.append({
            "marketId": mid,
            "r2HalfLifeMs": r2["halfLifeMs"],
            "capHalfLifeMs": cap["halfLifeMs"],
            "deltaHalfLifeMs": dhl,
            "deltaPnl": dpnl,
            "r2Censored": r2["censored"],
            "capCensored": cap["censored"],
            "r2PeakAbsMakerNet": r2["peakAbsMakerNet"],
            "capPeakAbsMakerNet": cap["peakAbsMakerNet"],
        })

    non_equal = [x for x in obs if abs(x["deltaHalfLifeMs"]) > EPS]
    shorter = [x for x in obs if x["deltaHalfLifeMs"] < -EPS]
    longer = [x for x in obs if x["deltaHalfLifeMs"] > EPS]
    equal = [x for x in obs if abs(x["deltaHalfLifeMs"]) <= EPS]
    rho = None
    pvalue = None
    if len(non_equal) >= 3:
        res = spearmanr([x["deltaHalfLifeMs"] for x in non_equal], [x["deltaPnl"] for x in non_equal])
        rho = float(res.statistic)
        pvalue = float(res.pvalue)

    med_short = median([x["deltaPnl"] for x in shorter]) if shorter else None
    med_long = median([x["deltaPnl"] for x in longer]) if longer else None
    med_equal = median([x["deltaPnl"] for x in equal]) if equal else None
    keep = bool(len(non_equal) >= 30 and rho is not None and rho <= -0.20 and med_short is not None and med_long is not None and med_short > med_long)
    if len(non_equal) < 30:
        status = "TESTED_INCONCLUSIVE"
    elif keep:
        status = "TESTED_KEEP_SIGNAL"
    else:
        status = "TESTED_REJECTED"

    report = {
        "testId": TEST_ID,
        "axis": "POST_PEAK_MAKER_INVENTORY_RECOVERY_HALF_LIFE",
        "source": str(DB.relative_to(ROOT)),
        "execution": "HFTBACKTEST_PREDICT_TAPE_CLOSED_LOOP_FORWARD_PAPER",
        "matchedSettledMarkets": len(mids),
        "qualifyingMarkets": len(obs),
        "nonEqualMarkets": len(non_equal),
        "primary": {
            "spearmanDeltaHalfLifeVsDeltaPnl": rho,
            "spearmanPValue": pvalue,
            "capShorterHalfLifeMarkets": len(shorter),
            "capLongerHalfLifeMarkets": len(longer),
            "equalHalfLifeMarkets": len(equal),
            "medianDeltaPnlCapShorterHalfLife": med_short,
            "medianDeltaPnlCapLongerHalfLife": med_long,
            "medianDeltaPnlEqualHalfLife": med_equal,
            "r2CensoredMarkets": sum(bool(x["r2Censored"]) for x in obs),
            "capCensoredMarkets": sum(bool(x["capCensored"]) for x in obs),
            "medianR2HalfLifeMs": median([x["r2HalfLifeMs"] for x in obs]) if obs else None,
            "medianCapHalfLifeMs": median([x["capHalfLifeMs"] for x in obs]) if obs else None,
        },
        "predeclaredKeepRule": "rho <= -0.20 AND median(relative PnL | CAP100 shorter half-life) > median(relative PnL | CAP100 longer half-life), with >=30 non-equal markets",
        "status": status,
        "interpretation": (
            "Post-peak Maker inventory recovery half-life passes the preregistered explanatory rule; preserve as research signal only and validate on independent chronology before any policy change."
            if status == "TESTED_KEEP_SIGNAL" else
            "Post-peak Maker inventory recovery half-life does not pass the preregistered explanatory rule; do not tune frozen policies around it."
            if status == "TESTED_REJECTED" else
            "Too few non-equal markets to decide the preregistered rule; no strategy inference."
        ),
        "rows": obs,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["testId", "matchedSettledMarkets", "qualifyingMarkets", "nonEqualMarkets", "primary", "status", "interpretation"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
