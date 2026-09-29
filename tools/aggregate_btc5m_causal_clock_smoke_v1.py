"""Verify control reproduction before permitting the second native clock arm."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "data/research"
RETURNS = R / "lan_worker_returns"
BASELINE = RETURNS / "v34-repair-lineage-2026085-20260912-v1/result.json"
JOBS = {"legacy": "causal-clock-2026085-legacy-20260912-v1",
        "fixed_train": "causal-clock-2026085-fixed-train-20260912-v1"}
STEM = "BTC5M_CAUSAL_CLOCK_NATIVE_SMOKE_2026085_V1_20260912"
PARITY_FIELDS = ("theta", "fixed_train_share_unit", "core_similarity", "path_mse", "coordinate_mse",
    "our_profile", "submits", "passive_native_submits", "active_native_submits", "cancel_requests",
    "final_inventory", "final_cost", "terminal_worst", "atomic_responsibility_summary",
    "atomic_responsibility_events", "repair_capacity_frontier_wakes", "fresh_capacity_frontier_wakes")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gate(doc):
    assert doc["status"] == "COMPLETE", doc.get("error")
    assert doc["safety_gate"]["pass"]
    assert doc["active_native_submits"] == 0
    assert doc["unresolved_owners"] == 0


def compact(doc):
    a = doc["atomic_responsibility_summary"]
    return dict(score=doc["core_similarity"], path_mse=doc["path_mse"],
        events=doc["our_profile"]["event_batches"], submits=doc["submits"],
        cancels=doc["cancel_requests"], floor=doc["terminal_worst"],
        best=max(doc["final_inventory"].values()) - doc["final_cost"],
        debt=sum(a["outstanding"].values()), cost=doc["final_cost"],
        inventory_up=doc["final_inventory"]["UP"], inventory_down=doc["final_inventory"]["DOWN"],
        births=a["births"], completed=a["completed"],
        repair_frontiers=doc["repair_capacity_frontier_wakes"], fresh_frontiers=doc["fresh_capacity_frontier_wakes"],
        native_elapsed_seconds=doc["elapsed_seconds"], accounting_valid=doc["execution_accounting_valid"])


def first_difference(left, right):
    for i, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return dict(index=i, legacy=a, fixed_train=b)
    if len(left) != len(right):
        i = min(len(left), len(right))
        return dict(index=i, legacy=(left[i] if i < len(left) else None),
                    fixed_train=(right[i] if i < len(right) else None))
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--control-only", action="store_true")
    args = p.parse_args()
    baseline, control = read(BASELINE), read(RETURNS / JOBS["legacy"] / "result.json")
    gate(baseline)
    gate(control)
    parity = {k: baseline[k] == control[k] for k in PARITY_FIELDS}
    assert all(parity.values()), parity
    assert control["clock_smoke"]["runtime_clock_total_frames"] == control["clock_smoke"]["actual_replay_frames"]
    if args.control_only:
        out = dict(control_parity_pass=True, fields=parity, metrics=compact(control))
        (R / (STEM + "_CONTROL_PARITY.json")).write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out))
        return
    candidate = read(RETURNS / JOBS["fixed_train"] / "result.json")
    gate(candidate)
    assert candidate["clock_smoke"]["runtime_clock_total_frames"] == 1487
    assert candidate["clock_smoke"]["actual_replay_frames"] == control["clock_smoke"]["actual_replay_frames"]
    for k in ("manifest_sha256", "wrapper_sha256", "frozen_runner_sha256", "fixed_train_total_frames"):
        assert candidate["clock_smoke"][k] == control["clock_smoke"][k]
    traces = {}
    for key, doc in (("legacy", control), ("fixed_train", candidate)):
        path = RETURNS / JOBS[key] / doc["clock_smoke"]["trace_file"]
        with gzip.open(path, "rb") as f:
            raw = f.read(16 * 1024 * 1024 + 1)
        assert len(raw) <= 16 * 1024 * 1024
        assert hashlib.sha256(raw).hexdigest() == doc["clock_smoke"]["trace_payload_sha256"]
        traces[key] = json.loads(raw)
    metrics = {"legacy": compact(control), "fixed_train": compact(candidate)}
    delta = {k: metrics["fixed_train"][k] - metrics["legacy"][k]
             for k in metrics["legacy"] if k != "accounting_valid"}
    diffs = {k: first_difference(traces["legacy"][k], traces["fixed_train"][k])
             for k in ("observations", "plans", "native_actions", "states")}
    paths = [BASELINE, *(RETURNS / j / "result.json" for j in JOBS.values())]
    report = dict(version=STEM, market=2026085, control_parity=parity, control_parity_pass=True,
        safety_both_pass=True, provenance=[dict(path=str(p.relative_to(ROOT)), sha256=sha(p)) for p in paths],
        metrics=metrics, delta=delta, first_differences=diffs,
        conclusion="CLOCK_DEPENDENCY_REMOVED_IN_ONE_CANDIDATE; SINGLE_MARKET_EFFECT_MEASURED; NOT_PROMOTED",
        decisions={"causal_clock_requirement": "KEEP", "fixed_train_1487_controller": "NEED_MORE_DATA",
                   "universal_improvement_or_stronger_repair_claim": "NOT_SUPPORTED"},
        limitations=["Whole-episode clock intervention, not a same-state route fork.",
            "A causal denominator alone does not certify all other runtime inputs.",
            "Fixed scale 1487 was frozen from TRAIN event counts, not tuned to this market.",
            "Responsibility IDs can change after divergence; do not match equal IDs across arms.",
            "Floor/best are conditional payoff endpoints, not realized winner PnL.",
            "Only one consumed market. No universal effectiveness claim."])
    (R / (STEM + ".json")).write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# BTC5M causal-clock 單市場 native 對照 — 2026-09-12", "",
        "市場：2026085。只比較 LEGACY_REPLAY_EVENTS 與 FIXED_TRAIN_EVENTS；後者分母固定為原 TRAIN 2022527/2022538 事件數 1486/1488 的中位數 1487。兩組均 Passive-only、同一 frozen V20 manager，不調 sizing/threshold、不啟用 Active。", "",
        "## 研究判定", "",
        "- **KEEP：移除未來事件總數依賴的因果要求。** 這次 clock 變化確實傳到 native 下單與成交路徑，不只是報表數字變化。",
        "- **NEED_MORE_DATA：固定 TRAIN 尺度 1487 這個候選。** 單市場 score/floor/path 改善伴隨 best payoff 減少，尚不能升格成最佳 clock。",
        "- **不支持：單憑 debt 下降宣稱 Repair 服務能力改善。** 完成責任數仍為 61；terminal DOWN inventory 少約 244.08、UP 只少約 1.23，曝險形成路徑的改變是必須分開看的因素。",
        "- 本次沒有擴大市場或 sweep clock 尺度，也不以 best 下降為理由回到含前視依賴的基準。", "",
        "## 驗證", "",
        f"原版控制組完整重現既有 V34：theta、score/path、成交後 inventory/cost、提交/取消數、atomic events/summary 與 frontier 次數等 {len(PARITY_FIELDS)} 項逐項完全相同。兩組 safety/accounting 通過，unresolved owners=0。",
        "本地 compile 與兩組程式差異檢查通過；兩組執行程式唯一差別是 total_frames assignment。相同 telemetry 與獨立工作目錄保護套用於兩組。", "",
        "## 結果（固定 TRAIN 尺度減原版）", "",
        "| 指標 | 原版 | 固定 TRAIN | 差值 |", "|---|---:|---:|---:|"]
    for k in ("score", "path_mse", "events", "submits", "cancels", "floor", "best", "debt", "births", "completed", "repair_frontiers", "fresh_frontiers"):
        lines.append(f"| {k} | {metrics['legacy'][k]:.6f} | {metrics['fixed_train'][k]:.6f} | {delta[k]:+.6f} |")
    lines += ["", "Floor/best 為兩種結算方向的 payoff 端點，並非已實現 winner PnL；path_mse 越低越好，其餘不能壓成單一畢業分數。", "",
        "## 第一個觀察到的差異", ""]
    for key, value in diffs.items():
        lines += [f"### {key}", "", "```json", json.dumps(value, indent=2, ensure_ascii=False), "```", ""]
    lines += ["## 解讀邊界", "",
        "本實驗量測移除 future-event-count 分母依賴後的整局變化。固定 TRAIN 尺度只是事前指定的一個 causal 候選；不能由一場決定最終 clock，更不能把結果當跨市場 promotion。", "",
        "兩組从 clock 造成第一個決策差異後會形成不同的責任與執行歷史，不可拿相同 responsibility ID 作同一責任比較。保留上一輪依 atomic payments 修正的 progress 統計口徑。", "",
        "## 成本與重現", "",
        f"第二台逐組執行、每組 max_threads=4。兩組 native runner elapsed 合計 {metrics['legacy']['native_elapsed_seconds'] + metrics['fixed_train']['native_elapsed_seconds']:.2f} 秒；不含 staging、排程、SSH 與回收時間。", "",
        "```powershell", "python tools/aggregate_btc5m_causal_clock_smoke_v1.py --control-only",
        "python tools/aggregate_btc5m_causal_clock_smoke_v1.py", "```", "",
        "同名 JSON 含來源 hash、完整 parity、數值差與最早 trace 差異。原始 traces 保存在各 job 的 clock_trace.json.gz。"]
    (R / (STEM + ".md")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
