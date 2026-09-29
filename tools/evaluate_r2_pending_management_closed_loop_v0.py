from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import run_market

OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
ART = OUT_DIR / "execution_aware_pending_management_v0.joblib"
TEACHER_ROWS = OUT_DIR / "r2_execution_school_teacher_v0_rows.csv"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
REPORT = OUT_DIR / "r2_pending_management_closed_loop_test_v0.json"


def finite(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def derived_positions(port: dict[str, Any]) -> dict[str, float]:
    mg = float(port.get("maker_gross") or 0.0); mn = float(port.get("maker_net") or 0.0)
    tg = float(port.get("taker_gross") or 0.0); tn = float(port.get("taker_net") or 0.0)
    return {
        "makerUp": (mg + mn) / 2.0,
        "makerDown": (mg - mn) / 2.0,
        "takerUp": (tg + tn) / 2.0,
        "takerDown": (tg - tn) / 2.0,
    }


def realized_pnl(roll: dict[str, Any], winner: str | None) -> float | None:
    if winner not in {"UP", "DOWN"}:
        return None
    pos = derived_positions(roll.get("finalPortfolio") or {})
    payout = pos["makerUp"] + pos["takerUp"] if winner == "UP" else pos["makerDown"] + pos["takerDown"]
    cost = float(roll.get("makerCostUsdt") or 0.0) + float(roll.get("takerCostUsdt") or 0.0) + float(roll.get("takerFeesUsdt") or 0.0)
    return payout - cost


def winners(ids: list[int]) -> dict[int, str]:
    con = sqlite3.connect(f"file:{TARGET_DB.resolve().as_posix()}?mode=ro", uri=True)
    try:
        out: dict[int, str] = {}
        for st in range(0, len(ids), 200):
            b = ids[st:st+200]
            qs = ",".join("?" for _ in b)
            for r in con.execute(f"select market_id,winner from target_market_results where asset='BTC' and market_id in ({qs}) and winner in ('UP','DOWN')", b):
                out[int(r[0])] = str(r[1]).upper()
        return out
    finally:
        con.close()


def q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    y = sorted(xs)
    if len(y) == 1:
        return y[0]
    z = (len(y)-1)*p; lo = int(math.floor(z)); hi = int(math.ceil(z)); w = z-lo
    return y[lo]*(1-w)+y[hi]*w


def stats(xs: list[float]) -> dict[str, Any]:
    y = [float(x) for x in xs if finite(x) is not None]
    return {
        "n": len(y), "mean": statistics.fmean(y) if y else None,
        "median": statistics.median(y) if y else None,
        "p10": q(y, .10), "p90": q(y, .90), "min": min(y) if y else None, "max": max(y) if y else None,
    }


def teacher_index(test_ids: list[int]) -> dict[tuple[int,int,str], dict[str, Any]]:
    d = pd.read_csv(TEACHER_ROWS)
    d = d[d.marketId.astype(int).isin(test_ids)].copy()
    out: dict[tuple[int,int,str], dict[str, Any]] = {}
    # Target fields are market/time/side context. Prefer a row with active Target same-side lifecycle if duplicated.
    for _, r in d.iterrows():
        key = (int(r.marketId), int(r.checkpointMs), str(r.side))
        z = r.to_dict()
        old = out.get(key)
        if old is None or float(z.get("targetActiveSameCount") or 0) > float(old.get("targetActiveSameCount") or 0):
            out[key] = z
    return out


def baseline_path(mid: int) -> Path:
    return OUT_DIR / f"r2_execution_school_market{mid}_mid_risk_v0.json"


def main() -> int:
    art = joblib.load(ART)
    test_ids = [int(x) for x in art.get("testMarkets") or []]
    if not test_ids:
        raise RuntimeError("artifact has no testMarkets")
    win = winners(test_ids)
    ti = teacher_index(test_ids)

    rows: list[dict[str, Any]] = []
    all_vetoes: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, mid in enumerate(test_ids, 1):
        try:
            bp = baseline_path(mid)
            if not bp.exists():
                raise FileNotFoundError(bp)
            base = json.loads(bp.read_text(encoding="utf-8"))
            skill = run_market(mid, pending_management_artifact=ART)
            sp = OUT_DIR / f"r2_pending_skill_market{mid}_test_v0.json"
            sp.write_text(json.dumps(skill, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
            br = base["studentRollout"]; sr = skill["studentRollout"]
            bport = br.get("finalPortfolio") or {}; sport = sr.get("finalPortfolio") or {}
            winner = win.get(mid)
            b_pnl = realized_pnl(br, winner); s_pnl = realized_pnl(sr, winner)

            # Index skill order hindsight rows so each veto can be audited against what actually happened next.
            state_index: dict[tuple[int,str], dict[str, Any]] = {}
            for x in skill.get("orderStateRows") or []:
                state_index[(int(x.get("checkpointMs") or -1), str(x.get("orderId") or ""))] = x
            vetoes = skill.get("pendingSkillVetoEvents") or []
            vfill1 = vfill3 = vfill5 = veventual = 0
            target_keep = target_refresh = target_none = 0
            for v in vetoes:
                cp = int(v["atMs"]); oid = str(v["existingOrderId"]); side = str(v["attemptedSide"])
                st = state_index.get((cp, oid)) or {}
                vfill1 += int(st.get("labelAnyFill1s") or 0)
                vfill3 += int(st.get("labelAnyFill3s") or 0)
                vfill5 += int(st.get("labelAnyFill5s") or 0)
                veventual += int(float(st.get("eventualAdditionalFillShares") or 0.0) > 1e-9)
                tr = ti.get((mid, cp, side)) or {}
                if int(float(tr.get("targetKeepProxy1s") or 0)) == 1:
                    target_keep += 1
                    tclass = "TARGET_KEEP_PROXY"
                elif finite(tr.get("targetNextPlacementDelayMs")) is not None and float(tr.get("targetNextPlacementDelayMs")) <= 1000 and str(tr.get("targetNextPlacementSide") or "") == side:
                    target_refresh += 1
                    tclass = "TARGET_SAMESIDE_REFRESH_1S"
                else:
                    target_none += 1
                    tclass = "NO_TARGET_KEEP_REFRESH_PROXY"
                all_vetoes.append({
                    "marketId": mid, **v,
                    "hftFill1sAfterVeto": int(st.get("labelAnyFill1s") or 0),
                    "hftFill3sAfterVeto": int(st.get("labelAnyFill3s") or 0),
                    "hftFill5sAfterVeto": int(st.get("labelAnyFill5s") or 0),
                    "hftEventualFillAfterVeto": int(float(st.get("eventualAdditionalFillShares") or 0.0) > 1e-9),
                    "targetPosthocClass": tclass,
                    "targetActiveSameCount": tr.get("targetActiveSameCount"),
                    "targetKeepProxy1s": tr.get("targetKeepProxy1s"),
                    "targetNextPlacementDelayMs": tr.get("targetNextPlacementDelayMs"),
                    "targetNextPlacementPrice": tr.get("targetNextPlacementPrice"),
                })

            rows.append({
                "marketId": mid, "winner": winner,
                "baselineMakerPlacements": int(br.get("makerPlacements") or 0), "skillMakerPlacements": int(sr.get("makerPlacements") or 0),
                "deltaMakerPlacements": int(sr.get("makerPlacements") or 0) - int(br.get("makerPlacements") or 0),
                "baselineMakerFilledShares": float(br.get("makerFilledShares") or 0.0), "skillMakerFilledShares": float(sr.get("makerFilledShares") or 0.0),
                "deltaMakerFilledShares": float(sr.get("makerFilledShares") or 0.0) - float(br.get("makerFilledShares") or 0.0),
                "baselineTakerFills": int(br.get("takerFills") or 0), "skillTakerFills": int(sr.get("takerFills") or 0),
                "deltaTakerFills": int(sr.get("takerFills") or 0) - int(br.get("takerFills") or 0),
                "vetoes": len(vetoes), "vetoHftFill1s": vfill1, "vetoHftFill3s": vfill3, "vetoHftFill5s": vfill5, "vetoHftEventualFill": veventual,
                "vetoTargetKeepProxy": target_keep, "vetoTargetSameSideRefresh1s": target_refresh, "vetoTargetNoProxy": target_none,
                "baselineMakerPairedCoverage": finite(bport.get("maker_paired_coverage")), "skillMakerPairedCoverage": finite(sport.get("maker_paired_coverage")),
                "deltaMakerPairedCoverage": (float(sport.get("maker_paired_coverage") or 0.0)-float(bport.get("maker_paired_coverage") or 0.0)),
                "baselineCombinedPairedCoverage": finite(bport.get("combined_paired_coverage")), "skillCombinedPairedCoverage": finite(sport.get("combined_paired_coverage")),
                "deltaCombinedPairedCoverage": (float(sport.get("combined_paired_coverage") or 0.0)-float(bport.get("combined_paired_coverage") or 0.0)),
                "baselineMakerAbsNet": finite(bport.get("maker_abs_net")), "skillMakerAbsNet": finite(sport.get("maker_abs_net")),
                "deltaMakerAbsNet": float(sport.get("maker_abs_net") or 0.0)-float(bport.get("maker_abs_net") or 0.0),
                "baselineCombinedAbsNet": finite(bport.get("combined_abs_net")), "skillCombinedAbsNet": finite(sport.get("combined_abs_net")),
                "deltaCombinedAbsNet": float(sport.get("combined_abs_net") or 0.0)-float(bport.get("combined_abs_net") or 0.0),
                "baselineWorstCaseFloor": finite(bport.get("worst_case_floor")), "skillWorstCaseFloor": finite(sport.get("worst_case_floor")),
                "deltaWorstCaseFloor": float(sport.get("worst_case_floor") or 0.0)-float(bport.get("worst_case_floor") or 0.0),
                "baselineCapital": float(br.get("makerCostUsdt") or 0.0)+float(br.get("takerCostUsdt") or 0.0)+float(br.get("takerFeesUsdt") or 0.0),
                "skillCapital": float(sr.get("makerCostUsdt") or 0.0)+float(sr.get("takerCostUsdt") or 0.0)+float(sr.get("takerFeesUsdt") or 0.0),
                "baselineRealizedPnl": b_pnl, "skillRealizedPnl": s_pnl,
                "deltaRealizedPnl": (s_pnl-b_pnl) if s_pnl is not None and b_pnl is not None else None,
            })
            print(json.dumps({"progress": i, "marketId": mid, "vetoes": len(vetoes), "dMakerShares": rows[-1]["deltaMakerFilledShares"], "dCoverage": rows[-1]["deltaCombinedPairedCoverage"], "dPnl": rows[-1]["deltaRealizedPnl"]}, ensure_ascii=False), flush=True)
        except Exception as exc:
            errors.append({"marketId": mid, "error": f"{type(exc).__name__}: {exc}"})
            print(json.dumps({"progress": i, "marketId": mid, "error": errors[-1]["error"]}, ensure_ascii=False), flush=True)

    def sumk(k: str) -> float:
        return float(sum(float(r.get(k) or 0.0) for r in rows))
    n_v = int(sumk("vetoes"))
    pnl_rows = [r for r in rows if r.get("deltaRealizedPnl") is not None]
    report = {
        "reportVersion": "R2_PENDING_MANAGEMENT_CLOSED_LOOP_TEST_V0",
        "researchOnly": True,
        "liveTradingChanges": False,
        "studentScale": "PRE_CAP100_ORIGINAL_R2",
        "cohort": {"split": "chronological TEST only", "requestedMarkets": len(test_ids), "completedMarkets": len(rows), "errors": errors, "marketIds": test_ids},
        "policy": {"artifact": str(ART), "intervention": "veto same-side Maker add only when an already venue-ACKed NEW/PARTIALLY_FILLED same-side order has model P(CONTINUE_PENDING)>=0.5", "threshold": 0.5, "thresholdSelection": "natural classifier boundary; no PnL/holdout sweep", "allOtherStrategyLogic": "frozen R2 unchanged", "dreamFillAllowed": False},
        "aggregate": {
            "vetoes": n_v,
            "marketsWithVeto": sum(int(r["vetoes"]) > 0 for r in rows),
            "vetoHftFill1sRate": sumk("vetoHftFill1s")/n_v if n_v else None,
            "vetoHftFill3sRate": sumk("vetoHftFill3s")/n_v if n_v else None,
            "vetoHftFill5sRate": sumk("vetoHftFill5s")/n_v if n_v else None,
            "vetoHftEventualFillRate": sumk("vetoHftEventualFill")/n_v if n_v else None,
            "vetoTargetKeepProxyRate": sumk("vetoTargetKeepProxy")/n_v if n_v else None,
            "vetoTargetSameSideRefresh1sRate": sumk("vetoTargetSameSideRefresh1s")/n_v if n_v else None,
            "baselineMakerPlacements": sumk("baselineMakerPlacements"), "skillMakerPlacements": sumk("skillMakerPlacements"),
            "baselineMakerFilledShares": sumk("baselineMakerFilledShares"), "skillMakerFilledShares": sumk("skillMakerFilledShares"),
            "baselineTakerFills": sumk("baselineTakerFills"), "skillTakerFills": sumk("skillTakerFills"),
            "baselineCapital": sumk("baselineCapital"), "skillCapital": sumk("skillCapital"),
            "baselineRealizedPnl": sum(float(r["baselineRealizedPnl"]) for r in pnl_rows),
            "skillRealizedPnl": sum(float(r["skillRealizedPnl"]) for r in pnl_rows),
            "deltaRealizedPnl": sum(float(r["deltaRealizedPnl"]) for r in pnl_rows),
            "pnlEvaluationMarkets": len(pnl_rows),
        },
        "distribution": {
            "deltaMakerPlacements": stats([r["deltaMakerPlacements"] for r in rows]),
            "deltaMakerFilledShares": stats([r["deltaMakerFilledShares"] for r in rows]),
            "deltaCombinedPairedCoverage": stats([r["deltaCombinedPairedCoverage"] for r in rows]),
            "deltaMakerAbsNet": stats([r["deltaMakerAbsNet"] for r in rows]),
            "deltaCombinedAbsNet": stats([r["deltaCombinedAbsNet"] for r in rows]),
            "deltaWorstCaseFloor": stats([r["deltaWorstCaseFloor"] for r in rows]),
            "deltaRealizedPnl": stats([float(r["deltaRealizedPnl"]) for r in pnl_rows]),
        },
        "rows": rows,
        "vetoAudit": all_vetoes,
        "interpretationGuard": [
            "Target fields are post-hoc audit only and never enter the skill rollout.",
            "Settlement winner/PnL is post-hoc evaluation only and was not used to choose threshold or fit the model.",
            "A veto is not automatically good because it reduces duplicate placement; inventory acquisition, paired coverage, residual risk, and realized HFT settlement performance must remain acceptable.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT_DIR / "r2_pending_management_closed_loop_test_v0_markets.csv", index=False)
    pd.DataFrame(all_vetoes).to_csv(OUT_DIR / "r2_pending_management_closed_loop_test_v0_vetoes.csv", index=False)
    print(json.dumps({"ok": True, "report": str(REPORT), "aggregate": report["aggregate"], "distribution": report["distribution"], "errors": errors}, ensure_ascii=False, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
