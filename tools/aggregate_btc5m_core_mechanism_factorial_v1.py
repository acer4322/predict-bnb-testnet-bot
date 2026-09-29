"""Exact-prefix and full-continuation scoring for one frozen mechanism panel."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS, compact

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "data/research"
RET = R / "lan_worker_returns"
STEM = "BTC5M_CORE_MECHANISM_FACTORIAL_RESULT_20260912"
ARMS = ("pa", "aa", "pd", "ad")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trace(path, result):
    with gzip.open(path / "clock_trace.json.gz", "rb") as f:
        raw = f.read(16 * 1024 * 1024 + 1)
    assert len(raw) <= 16 * 1024 * 1024
    assert hashlib.sha256(raw).hexdigest() == result["clock_smoke"]["trace_payload_sha256"]
    return json.loads(raw)


def row_metrics(d, tr, checkpoint):
    out = compact(d)
    hit = d["clock_smoke"]["fresh_hits"][0]
    old = {r["id"]: r for queue in hit["atomic_queues"].values() for r in queue}
    payments = [(ev["t"], p) for ev in d["atomic_responsibility_events"] if ev["t"] > checkpoint
                for p in ev["payments"] if p["responsibility_id"] in old]
    paid = sum(p["qty"] for _, p in payments)
    old_qty = sum(r["remaining"] for r in old.values())
    assert paid <= old_qty + 1e-7
    completed = {p["responsibility_id"] for _, p in payments if p["remaining_after"] <= 1e-8}
    market_manifest = read(ROOT / ".lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/MANIFEST.json")
    market_end = market_manifest["marketWindows"]["2026085"][1]
    outstanding, previous_t, debt_area = old_qty, checkpoint, 0.
    for payment_t, payment in payments:
        assert previous_t <= payment_t <= market_end
        debt_area += outstanding * (payment_t - previous_t) / 1000.
        outstanding = max(0., outstanding - payment["qty"])
        previous_t = payment_t
    debt_area += outstanding * (market_end - previous_t) / 1000.
    all_completed_t = max((time for time,pay in payments if pay["remaining_after"] <= 1e-8), default=None)
    new_births = [b for ev in d["atomic_responsibility_events"] if ev["t"] > checkpoint for b in ev["births"]]
    carrier = d["exact_frontier_carrier"]
    out.update(existing_responsibility_count=len(old), existing_debt_at_fork=old_qty,
        existing_debt_paid_after_fork=paid, existing_debt_remaining=max(0., old_qty-paid),
        existing_responsibilities_completed=len(completed),
        existing_first_payment_delay_ms=(payments[0][0]-checkpoint if payments else None),
        existing_debt_area_share_seconds=debt_area,
        existing_all_completed_delay_ms=(all_completed_t-checkpoint if len(completed)==len(old) else None),
        new_responsibility_qty_after_fork=sum(b["qty"] for b in new_births),
        repair_carrier_qty=carrier["qty"], repair_carrier_filled=carrier["filled"],
        repair_first_fill_latency_ms=carrier["first_fill_latency_ms"],
        repair_terminal_latency_ms=carrier["terminal_latency_ms"])
    release = next((e for e in d["clock_smoke"]["defer_events"] if e["event"] == "DEFER_RELEASE"), None)
    fresh_side = hit["side"]
    selected = [op for plan in tr["plans"] if plan["t"] == checkpoint for op in plan["operations"]
                if op["kind"] == "NEW" and op["side"] == fresh_side]
    assert len(selected) <= 1
    key = selected[0]["key"] if selected else None
    fills = [(ev["t"], fr) for ev in d["atomic_responsibility_events"] for fr in ev["fill_rows"] if fr["key"] == key]
    return dict(metrics=out, repair_carrier={k: carrier[k] for k in ("key", "route", "qty", "filled", "fill_fraction", "payment", "fees", "first_fill_latency_ms", "terminal_latency_ms")},
        fresh_original_carrier=dict(key=key, filled=sum(fr["fill_increment"] for _,fr in fills),
            first_fill_t=(fills[0][0] if fills else None)),
        defer_release=release, deferred_candidate_count=d["clock_smoke"]["deferred_candidate_count"],
        defer_pending_final=d["clock_smoke"]["defer_pending_final"],
        first_subsequent_fresh_side_new=next((dict(t=p["t"], operation=o) for p in tr["plans"] if p["t"] > checkpoint
                                             for o in p["operations"] if o["kind"] == "NEW" and o["side"] == fresh_side), None))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-only", action="store_true")
    args = parser.parse_args()
    baseline_path = RET / "causal-clock-2026085-fixed-train-20260912-v1"
    baseline = read(baseline_path / "result.json")
    control_path = RET / "core-mech-2026085-pa-20260912-v1"
    control = read(control_path / "result.json")
    assert control["status"] == "COMPLETE", control.get("error")
    assert control["safety_gate"]["pass"]
    parity = {k: baseline[k] == control[k] for k in PARITY_FIELDS}
    assert all(parity.values()), parity
    ctr = trace(control_path, control)
    baseline_trace = trace(baseline_path, baseline)
    trace_parity = {k: ctr[k] == baseline_trace[k] for k in ctr}
    assert all(trace_parity.values()), trace_parity
    if args.control_only:
        out = dict(pass_control=True, metrics=parity, full_trace_parity=trace_parity,
                   checkpoint=control["frontier_exact_hits"][0])
        (R / (STEM + "_CONTROL_PARITY.json")).write_text(json.dumps(out,indent=2)+"\n")
        print(json.dumps(out))
        return
    t = control["frontier_exact_hits"][0]["t"]
    common_fields = ("t", "index", "side", "kind", "qty", "passive_price", "active_ask", "frame_id",
                     "gateway_state_id", "prefix_digest", "atomic_repair_need", "repair_gap", "fresh_desire",
                     "fresh_gap", "live_unfilled_before", "controller_deficit", "inv", "cost", "progress")
    rows, gates, provenance = {}, {}, []
    for arm in ARMS:
        p = RET / f"core-mech-2026085-{arm}-20260912-v1"
        d = read(p / "result.json")
        assert d["status"] == "COMPLETE", (arm, d.get("error"))
        assert d["safety_gate"]["pass"]
        tr = trace(p, d)
        hit = d["frontier_exact_hits"][0]
        checks = {k: hit[k] == control["frontier_exact_hits"][0][k] for k in common_fields}
        checks["fresh_pre_action_state"] = d["clock_smoke"]["fresh_hits"] == control["clock_smoke"]["fresh_hits"]
        checks["same_manifest"] = d["clock_smoke"]["manifest_sha256"] == control["clock_smoke"]["manifest_sha256"]
        checks["same_transformed_source"] = d["clock_smoke"]["transformed_source_sha256"] == control["clock_smoke"]["transformed_source_sha256"]
        for name in ("states", "plans", "native_actions", "observations"):
            checks["prefix_" + name] = [x for x in tr[name] if x["t"] < t] == [x for x in ctr[name] if x["t"] < t]
        assert all(checks.values()), (arm, checks)
        gates[arm] = checks
        rows[arm] = row_metrics(d,tr,t)
        provenance.append(dict(arm=arm, path=str((p/"result.json").relative_to(ROOT)),sha256=sha(p/"result.json")))
    values = ("score", "path_mse", "events", "submits", "floor", "best", "debt",
              "existing_debt_paid_after_fork", "existing_debt_remaining", "existing_responsibilities_completed",
              "existing_debt_area_share_seconds", "new_responsibility_qty_after_fork")
    def delta(a,b):
        return {k:rows[a]["metrics"][k]-rows[b]["metrics"][k] for k in values}
    contrasts = dict(active_given_admit=delta("aa","pa"), active_given_defer=delta("ad","pd"),
                     defer_given_passive=delta("pd","pa"), defer_given_active=delta("ad","aa"))
    contrasts["interaction"] = {k: contrasts["active_given_defer"][k]-contrasts["active_given_admit"][k] for k in values}
    decisions = [
        dict(status="KEEP", scope="measurement", finding="以同一批既存責任的實際償付、未償量時間積分及完整延續評估 Repair；單張 carrier 零成交不能代表責任未被服務。"),
        dict(status="REJECT", scope="this_checkpoint_only", finding="此處 Active 提早首次償付，但未縮短全部既存責任完成時間；不支持只憑首次成交提早就採用 Active。"),
        dict(status="REJECT", scope="this_checkpoint_only", finding="此處 DEFER_NEXT_WAKE 在兩種 Repair route 下都增加後續新責任與終局未配對量，不支持本切點的 Fresh 壓力改善假說。"),
        dict(status="NEED_MORE_DATA", scope="conditional_route_selection", finding="Active 對 floor 與既存責任未償量時間積分的效果隨 Fresh 決策改變，需其他預先選定切點重複，才能形成條件規則。"),
    ]
    out = dict(version=STEM, market=2026085, checkpoint=t, control_parity=True, full_prefix_parity=gates,
               safety_all_pass=True, rows=rows, contrasts=contrasts,provenance=provenance, decisions=decisions,
               native_elapsed_seconds_total=sum(rows[a]["metrics"]["native_elapsed_seconds"] for a in ARMS),
               limitations=["One selected checkpoint; no Target mechanism identification or cross-market promotion.",
                   "Fresh deferral is until next atomic/terminal wake, not fixed seconds.",
                   "Active can alter release time of Fresh deferral: this is part of interaction, not an independent fixed dose.",
                   "Only responsibility IDs already present at the common fork may be compared across branches.",
                   "Full continuation measurements; floor/best are payoff endpoints, not realized winner PnL."])
    (R/(STEM+".json")).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    lines=["# BTC5M 單市場核心機制 2×2 對照 — 2026-09-12", "",
        "選點：固定 causal clock=1487 的 2026085 基準，第一個同時有 opposite-side Repair/Fresh frontier 的時點。14 個候選中選最早；未用後續 fill、Target action 或終局收益選點。",
        "", "四組：PA=Passive Repair＋立即 Fresh；AA=Active Repair＋立即 Fresh；PD=Passive Repair＋延後 Fresh；AD=Active Repair＋延後 Fresh。Fresh 延至下一個 atomic transition／execution terminal wake，之後回 frozen manager。", "",
        "## 本輪判讀", "",
        "本輪完成機制實驗，尚未重新擬合或升級 selector。四組 native 合計約 152.3 秒，第二台 worker 依序執行、每組最多 4 threads。", "",
        "- Active 實際成交 20.05／24.7 shares，讓既存責任首次償付由 22.404 秒（PA）或 24.376 秒（PD）提早至 0.628 秒；但四組相同的 10 筆舊責任、203.874958 shares，均在 93.386 秒全部完成。",
        "- 未償量時間積分越低代表這批舊責任整段等待負擔越小。立即 Fresh 時，Active 反而由 13056.12 升至 13624.64 share-seconds；延後 Fresh 時，Active 才由 15476.56 降至 13624.64。首次償付速度不足以代表整體服務改善。",
        "- 延後 Fresh 在 Passive／Active 下，後續新責任分別增加 185.04／210.48 shares，floor 分別降低 12.14／8.93。這個切點不支持延後 Fresh 能減輕後續責任壓力。best 同時提高，代表 payoff 向另一端偏移，不能說所有收益面向都更差。",
        "- 延後解除是策略互動的一部分：PD 在 1.606 秒由 terminal wake 解除，AD 在 0.628 秒由 atomic transition 解除。解除後同側首張 NEW 分別為 30.00／27.86 shares，與原本立即 Fresh 的 19.44 shares 不同。不能把本實驗解讀成固定移除相同數量的 Fresh。",
        "- Active 對 floor 的條件效果由立即 Fresh 的 -1.667，變成延後 Fresh 的 +1.5481；這是本次確定性重播的交互作用，尚無跨切點的統計證據。", "",
        *[f"- **{x['status']}**（{x['scope']}）：{x['finding']}" for x in decisions], "",
        "下一輪建議：保留 causal baseline，按既存責任覆蓋狀態預先選定不同切點，重複同一小型機制面板；先驗證 Active 何時改善整批責任服務，再擬合條件 selector。這是待執行方向，不列為本輪已完成訓練。", "",
        "## 有效性", "", "控制組完整重現既有 causal baseline 的 17 項結果，以及全部 observations/plans/native actions/states。四組同一 frame/gateway/prefix、qty、inventory/cost、atomic queues；全歷史前綴比對及 safety/accounting 通過。", "",
        "## 整局與責任服務結果", "", "| 指標 | PA | AA | PD | AD |", "|---|---:|---:|---:|---:|"]
    for k in ("score","path_mse","events","submits","floor","best","debt","repair_carrier_filled",
              "existing_debt_paid_after_fork","existing_debt_remaining","existing_responsibilities_completed",
              "existing_first_payment_delay_ms","existing_debt_area_share_seconds","existing_all_completed_delay_ms",
              "new_responsibility_qty_after_fork"):
        lines.append("| "+k+" | "+" | ".join(f"{rows[a]['metrics'][k]:.6f}" if rows[a]['metrics'][k] is not None else '未觀測到' for a in ARMS)+" |")
    lines += ["", "existing_* 只追蹤共用 checkpoint 已存在的責任；分叉後的新 responsibility ID 不當作同一責任硬配對。", "", "## 四個條件效果與交互作用", "", "```json",json.dumps(contrasts,indent=2),"```", "", "## 執行與延後細節", ""]
    for a in ARMS:
        lines += [f"### {a.upper()}", "", "```json",json.dumps({k:v for k,v in rows[a].items() if k!='metrics'},indent=2),"```", ""]
    lines += ["## 限制", "", *["- "+x for x in out["limitations"]], "", "## 重現", "", "```powershell", "python tools/aggregate_btc5m_core_mechanism_factorial_v1.py", "```", ""]
    (R/(STEM+".md")).write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps(dict(rows=rows,contrasts=contrasts,all_checks_pass=True),ensure_ascii=False,allow_nan=False))


if __name__ == "__main__":
    main()
