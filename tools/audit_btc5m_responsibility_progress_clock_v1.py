"""Bounded artifact-only audit. Does not import runners, replay HFT, or change policy.

Payment authority is atomic events, not carrier intent at submission. Clock probe
executes AST-extracted arithmetic only; it is not a native action/fill experiment.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data/research"
MIDS = (2022527, 2022538, 2022602, 2023438, 2026085, 2026817, 2028352, 2029246)
EPS = 1e-8
STEM = "BTC5M_RESPONSIBILITY_PROGRESS_CLOCK_AUDIT_V1_20260912"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    assert path.stat().st_size < 12 * 1024 * 1024, path
    return json.loads(path.read_text(encoding="utf-8"))


def rebuild(events):
    """Check every birth/payment and reconstruct responsibility lifecycles."""
    responsibilities = {}
    previous_t = -1
    for ev in events:
        t = int(ev["t"])
        assert t >= previous_t
        previous_t = t
        for payment in ev["payments"]:
            r = responsibilities[payment["responsibility_id"]]
            assert r["side"] == payment["responsibility_side"]
            assert payment["fill_side"] != r["side"]
            qty = float(payment["qty"])
            assert qty > 0
            r["remaining"] -= qty
            assert abs(r["remaining"] - payment["remaining_after"]) < EPS
            assert r["remaining"] >= -EPS
            r["payments"].append({"t": t, "qty": qty,
                                  "remaining_after": payment["remaining_after"],
                                  "batch_carrier_keys": [x["key"] for x in ev.get("fill_rows", [])]})
            if r["remaining"] <= EPS:
                assert r["completed_t"] is None
                r["completed_t"] = t
        for birth in ev["births"]:
            rid = birth["responsibility_id"]
            assert rid not in responsibilities
            responsibilities[rid] = dict(id=rid, side=birth["side"], born_t=t,
                initial=float(birth["qty"]), remaining=float(birth["qty"]),
                payments=[], completed_t=None)
        for side in ("UP", "DOWN"):
            actual = sum(r["remaining"] for r in responsibilities.values() if r["side"] == side)
            assert abs(actual - ev["outstanding_after"][side]) < EPS
    return responsibilities


def annotate_attempts(r, attempts):
    """Atomic update precedes frontier creation at a clock: payments <= t are visible.

    FIRST refers only to the first tracked Repair-frontier attempt, not first
    service/payment of the responsibility. Runs count consecutive no-progress
    RETRIES; first attempt and a payment between attempts reset the run.
    """
    rows, previous_paid, run = [], None, 0
    for i, a in enumerate(sorted(attempts, key=lambda x: (x["t"], x["key"]))):
        visible = [p for p in r["payments"] if p["t"] <= a["t"]]
        paid = sum(p["qty"] for p in visible)
        remaining = r["initial"] - paid
        assert abs(remaining - a["remaining_at_birth"]) < EPS
        assert remaining > EPS, "attempt linked to completed responsibility"
        if previous_paid is None:
            kind = "FIRST_ATTEMPT"
            run = 0
        elif paid > previous_paid + EPS:
            kind = "RETRY_AFTER_PROGRESS"
            run = 0
        else:
            kind = "RETRY_NO_PROGRESS"
            run += 1
        rows.append(dict(attempt=i + 1, key=a["key"], t=a["t"], category=kind,
            paid_before_attempt=paid, remaining_before_attempt=remaining,
            payment_since_previous_attempt=(None if previous_paid is None else paid - previous_paid),
            no_progress_retry_run=run,
            last_payment_t=(visible[-1]["t"] if visible else None),
            elapsed_since_last_payment_ms=(a["t"] - visible[-1]["t"] if visible else None),
            responsibility_age_ms=a["t"] - r["born_t"]))
        previous_paid = paid
    return rows


def audit_market(mid, doc, old):
    assert doc["status"] == "COMPLETE" and doc["safety_gate"]["pass"]
    events = doc["atomic_responsibility_events"]
    rs = rebuild(events)
    summary = doc["atomic_responsibility_summary"]
    assert len(rs) == summary["births"], "truncated/missing births"
    assert sum(r["completed_t"] is not None for r in rs.values()) == summary["completed"]
    for side in ("UP", "DOWN"):
        assert abs(sum(e["fill_up" if side == "UP" else "fill_down"] for e in events)
                   - summary["total_fill"][side]) < EPS, "truncated/missing fills"
        assert abs(sum(r["remaining"] for r in rs.values() if r["side"] == side)
                   - summary["outstanding"][side]) < EPS
    lineages = []
    positive_runs = []
    counts = dict(FIRST_ATTEMPT=0, RETRY_NO_PROGRESS=0, RETRY_AFTER_PROGRESS=0)
    for old_r in doc["repair_lineage_rows"]:
        r = rs[old_r["responsibility_id"]]
        assert r["born_t"] == old_r["born_t"] and r["side"] == old_r["responsibility_side"]
        attempts = annotate_attempts(r, old_r["attempts"])
        first = r["payments"][0]["t"] if r["payments"] else None
        for a in attempts:
            counts[a["category"]] += 1
        run = 0
        for a in attempts:
            if a["category"] == "RETRY_NO_PROGRESS":
                run += 1
            else:
                if run:
                    positive_runs.append(run)
                run = 0
        if run:
            positive_runs.append(run)
        lineages.append(dict(responsibility_id=r["id"], side=r["side"], born_t=r["born_t"],
            initial=r["initial"], terminal_remaining=max(0., r["remaining"]),
            completed_t=r["completed_t"], first_payment_t=first,
            first_payment_latency_ms=(None if first is None else first - r["born_t"]),
            legacy_first_payment_t=old_r["first_payment_t"],
            legacy_carrier_zero_fill_attempts=old_r["zero_fill_attempts"],
            first_payment_corrected=first != old_r["first_payment_t"],
            false_never_paid=old_r["first_payment_t"] is None and first is not None,
            paid_before_first_tracked_attempt=bool(attempts[0]["paid_before_attempt"] > EPS),
            max_no_progress_retry_run=max(a["no_progress_retry_run"] for a in attempts),
            payments=r["payments"], attempts=attempts))
    assert sum(counts.values()) == doc["repair_capacity_frontier_wakes"]
    assert sum(counts.values()) == doc["repair_lineage_summary"]["carriers"]
    old_counts = {k: old[k] for k in counts}
    report = dict(market=mid, integrity_pass=True, total_atomic_responsibilities=len(rs),
        tracked_repair_responsibilities=len(lineages), total_attempts=sum(counts.values()),
        corrected_first_payment_count=sum(r["first_payment_corrected"] for r in lineages),
        false_never_paid_count=sum(r["false_never_paid"] for r in lineages),
        actual_never_paid_tracked_count=sum(r["first_payment_t"] is None for r in lineages),
        paid_before_first_tracked_attempt_count=sum(r["paid_before_first_tracked_attempt"] for r in lineages),
        corrected_counts=counts, legacy_v35_counts=old_counts,
        v35_category_counts_unchanged=counts == old_counts,
        no_progress_fraction=counts["RETRY_NO_PROGRESS"] / max(1, sum(counts.values())),
        max_no_progress_retry_run=max(r["max_no_progress_retry_run"] for r in lineages),
        positive_no_progress_run_episodes=len(positive_runs),
        median_positive_no_progress_run=statistics.median(positive_runs) if positive_runs else None,
        median_per_lineage_max_no_progress_retry_run=statistics.median(r["max_no_progress_retry_run"] for r in lineages),
        legacy_v35_median_no_progress_run=old["medianNoProgressRun"],
        v35_max_and_median_reproduced=(max(r["max_no_progress_retry_run"] for r in lineages) == old["maxNoProgressRun"]
            and (statistics.median(positive_runs) if positive_runs else None) == old["medianNoProgressRun"]),
        lineages=lineages)
    return report


def clock_probe(docs, checkpoint):
    paths = [ROOT / "tools" / name for name in (
        "run_target_core_cycle_dual_capacity_frontier_v20.py",
        "run_target_core_cycle_dual_capacity_frontier_transfer5_v31.py",
        "run_target_core_cycle_frontier_route_consumed8_v36.py")]
    target_names = {"w", "p", "progress", "exposure", "up", "share", "gross", "desired"}
    rows = []
    train_scale = statistics.median(docs[m]["source_frames"] for m in (2022527, 2022538))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        policy = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Policy")
        produce = next(n for n in policy.body if isinstance(n, ast.FunctionDef) and n.name == "produce")
        selected = [n for n in produce.body if isinstance(n, ast.Assign)
                    and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                    and n.targets[0].id in target_names]
        assert {n.targets[0].id for n in selected} == target_names
        code = compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(path), "exec")
        assert "len(sim.payload['updates'])" in source
        def evaluate(total_frames):
            inv = checkpoint["inv"]
            env = dict(math=math, self=SimpleNamespace(theta=docs[2026085]["theta"], total_frames=total_frames),
                       f={"index": checkpoint["index"]},
                       x={"own_net": (inv["UP"] - inv["DOWN"]) / (1 + inv["UP"] + inv["DOWN"])})
            exec(code, env)
            return {k: env[k] for k in ("p", "progress", "gross", "desired")}
        n = docs[2026085]["source_frames"]
        variants = []
        base = evaluate(n)
        assert abs(base["p"] - checkpoint["progress"]) < 1e-12
        for extra in (0, 1, n - checkpoint["index"] - 1):
            value = evaluate(n + extra)
            variants.append(dict(extra_future_events=extra, total_frames=n + extra, **value,
                                 gross_delta=value["gross"] - base["gross"]))
        fixed_values = [evaluate(train_scale) for _ in variants]
        assert variants[1]["gross"] != base["gross"]
        assert all(v == fixed_values[0] for v in fixed_values)
        rows.append(dict(source=str(path.relative_to(ROOT)), sha256=sha(path),
            arithmetic_lines=sorted(set(n.lineno for n in selected)),
            same_checkpoint=dict(t=checkpoint["t"], index=checkpoint["index"], inv=checkpoint["inv"], cost=checkpoint["cost"]),
            variants=variants, future_count_invariance_pass=False,
            fixed_training_count_negative_control=dict(scale=train_scale, invariance_pass=True,
                result=fixed_values[0], promoted=False)))
    return dict(level="EXACT_SOURCE_ARITHMETIC_COMPONENT_ONLY", native_replay=False,
        submitted_action_or_economic_effect_tested=False,
        conclusion="FUTURE_SUFFIX_EVENT_COUNT_CHANGES_CURRENT_DESIRED_EXPOSURE",
        note="Fixed TRAIN-count control demonstrates dependency removal only; it is not a selected replacement clock.",
        rows=rows)


def self_test():
    r = dict(initial=20., born_t=1, payments=[dict(t=5, qty=3.), dict(t=10, qty=2.)])
    aa = [dict(t=t, key=str(i), remaining_at_birth=q) for i, (t, q) in enumerate(((6, 17.), (8, 17.), (10, 15.), (11, 15.)))]
    rows = annotate_attempts(r, aa)
    assert [r["category"] for r in rows] == ["FIRST_ATTEMPT", "RETRY_NO_PROGRESS", "RETRY_AFTER_PROGRESS", "RETRY_NO_PROGRESS"]
    assert rows[0]["paid_before_attempt"] == 3. and rows[2]["last_payment_t"] == 10
    assert rows[3]["no_progress_retry_run"] == 1
    try:
        annotate_attempts(r, [dict(t=10, key="bad", remaining_at_birth=20.)])
    except AssertionError:
        pass
    else:
        raise AssertionError("must reject missing/misaligned payment history")


def markdown_report(report):
    agg = report["aggregate"]
    witness = report["witness_2026085_r62"]
    lines = ["# BTC5M responsibility progress / causal clock 最小稽核 — 2026-09-12", "",
        "範圍：只重算本地既有 consumed8 artifacts，並執行原始 clock 算式的元件檢查。未執行 native replay，未修改 frozen runners、實單服務、sizing 或 threshold。", "",
        "## 判定", "",
        "- **REWRITE：V34 first-payment 統計與交接敘述。** Carrier intent 不能代替 responsibility 的實際 payments。",
        "- **KEEP（描述性證據）：V35 的 no-progress 分類與分布。** 八場的三類計數、最大 run 和正長度 run 中位數均由 atomic payments 重現。",
        "- **NEED_MORE_DATA：stall 是否改變 Active marginal value。** 仍值得追，但沒有新因果／收益證據；目前不增加 fast-world 或 native 運算。",
        "- **FAIL：現有 event-count clock 的未來不變性。** V20/V31/V36 的當下 desired exposure 依賴未來事件總數；現有結果不能作為 strict-past controller 的升格證据。帳本守恆／native 執行結果本身不因此被改寫。", "",
        "## 實際支付重算", "",
        f"八場共 {agg['tracked_responsibilities']} 筆有 Repair-frontier attempts 的責任；{agg['first_payment_changed']} 筆 first-payment 時間改變，其中 {agg['false_never_paid']} 筆原本誤記為從未支付。這個分母不是全部 atomic births。", "",
        "每筆 atomic birth、payment、completion、各時點 remaining 與 terminal outstanding 均核對；fill 總量與 summary 相符，未發現事件截斷。每次 attempt 的 remaining 也與付款累計獨立對上。", "",
        "| Market | 受追蹤責任 | first-payment 修正 | 誤記從未支付 | 無進度 retries / attempts | 正長度 run 中位數 | 最大 run |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for m in report["markets"]:
        lines.append(f"| {m['market']} | {m['tracked_repair_responsibilities']} | {m['corrected_first_payment_count']} | {m['false_never_paid_count']} | {m['corrected_counts']['RETRY_NO_PROGRESS']} / {m['total_attempts']} | {m['median_positive_no_progress_run']:g} | {m['max_no_progress_retry_run']} |")
    lines += ["", "Run 定義：連續 RETRY_NO_PROGRESS 次數，FIRST 不算；有該責任的實際支付即重置。中位數以正長度的各段 run 為單位，包括最後觀察到但尚未結束的段落；不是每筆責任最大 run 的中位數，也不是經刪失修正的 hazard 估計。", "",
        "V34 原統計只從指定 Repair-frontier carriers 的 first fill 推 first payment，會漏掉其他 carrier 的支付，也可能把後來支付別筆責任的 fill 歸到原先責任。新統計直接查 atomic payments 中的 responsibility_id；多 carrier 同 clock 時只保留 batch contributors，不假裝有唯一逐筆配對。", "",
        "## 2026085 responsibility #62", "",
        f"Birth t={witness['born_t']}，initial={witness['initial']:g} shares。實際 first payment t={witness['first_payment_t']}，距 birth {witness['first_payment_latency_ms']} ms。",
        "UP_418 在 t=1788758353790 支付 10.4195137803 shares，再於 t=1788758357779 支付 7.33；terminal remaining=42.2504862197，未完成。60 張指定 Repair frontier carriers 全零成交仍然成立，但責任從未支付的說法不成立。", "",
        "| 選定 attempt | 當下已支付 | 當下 remaining | 連續無進度 retry | 距最近實際支付 ms |",
        "|---|---:|---:|---:|---:|"]
    for a in witness["attempts"]:
        if a["attempt"] in (1, 30, 60):
            lines.append(f"| {a['attempt']} | {a['paid_before_attempt']:.6f} | {a['remaining_before_attempt']:.6f} | {a['no_progress_retry_run']} | {a['elapsed_since_last_payment_ms']} |")
    lines += ["", "正確假說是『部分支付後再次停滯的責任，Active 路徑價值如何變化』。Attempt 1 發生在實際支付後 201 ms，不能當作自 birth 起毫無進度的早期樣本；30/60 仍是長時間沒有新增支付的樣本。跨 attempt 的價格、剩餘市場時間、capacity 同時不同，不能把它們的 route-value 差直接歸因於 stall。", "",
        "## Clock 的最小因果檢查", "",
        "直接從 V20/V31/V36 的 Policy.produce AST 抽出 w/p/progress/exposure/share/gross/desired 賦值，執行原算式；不 import runner 或 HFT。三份程式均以 len(sim.payload['updates']) 提供 total_frames。固定 #62 Attempt 1 的 index=1255、inventory、cost、theta，僅改分母代表的未來事件數。", "",
        "| 額外未來事件 | total_frames | 當下 desired gross | 相對原值 |",
        "|---|---:|---:|---:|"]
    for v in report["clock"]["rows"][0]["variants"]:
        lines.append(f"| {v['extra_future_events']} | {v['total_frames']} | {v['gross']:.6f} | {v['gross_delta']:+.6f} |")
    lines += ["", "三份原始算式都未通過未來不變性。以固定 TRAIN 兩場事件數中位數作對照時，相同測試輸出不變；這僅證明移除該依賴可恢復此元件的不變性，沒有選定／部署這個替代 clock，也不代表整個 controller 已因果合格。", "",
        "本測試證明 desired exposure 前視依賴，沒有測 native 提交是否改變；既有 30-share ticket 等機制可能遮蔽部分局部差異，更不能由此宣稱修正後 PnL 一定改善。最終影響仍需之後的一個市場小型 native 對照。", "",
        "## 研究成本與下一步", "",
        f"此次 artifact 重算與 clock 元件檢查約 {report['elapsed_seconds']:.3f} 秒（只計 Python 稽核段，不含閱讀、開發及寫報告時間），零個新增 native jobs。未擴展 fast-world。", "",
        "1. 後續報告使用本檔及同名 JSON 的 corrected payment fields，保留舊檔作 provenance。",
        "2. 保留 V35 的描述性 stall 證據；不必因 first-payment bug 重跑八場。",
        "3. 若繼續因果／promotion 研究，先明確定義可在線計算的 clock，再做單市場小型對照；本次未變更 frozen controller。",
        "4. 尚未回收／核驗的 V36 Attempt 1 Active result 不因 progress completed 而視為已驗證，亦未重送 job。", "",
        "## 重現", "",
        "```powershell", "python tools/audit_btc5m_responsibility_progress_clock_v1.py --self-test",
        "python tools/audit_btc5m_responsibility_progress_clock_v1.py", "```", "",
        "同名 JSON 含每筆 responsibility 的 payment、逐 attempt 的因果可見狀態、input/source SHA256 與 clock 算式行號。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        print("PASS: before-first payment, same-clock visibility, retry reset, inconsistent history rejection")
        return
    started = time.perf_counter()
    old_path = RESEARCH / "V35_REPAIR_SERVICE_PROGRESS_8BTC_AUDIT_20260912.json"
    old = {r["market"]: r for r in read_json(old_path)["rows"]}
    inputs = [old_path]
    docs, markets = {}, []
    for mid in MIDS:
        path = RESEARCH / "lan_worker_returns" / f"v34-repair-lineage-{mid}-20260912-v1/result.json"
        inputs.append(path)
        docs[mid] = read_json(path)
        markets.append(audit_market(mid, docs[mid], old[mid]))
    cp_path = RESEARCH / "lan_worker_returns/v36-2026085-r62-attempt1-passive-20260912-v1/result.json"
    inputs.append(cp_path)
    checkpoint = read_json(cp_path)["frontier_exact_hits"][0]
    clock = clock_probe(docs, checkpoint)
    witness = next(r for m in markets if m["market"] == 2026085 for r in m["lineages"] if r["responsibility_id"] == 62)
    report = dict(version=STEM, scope="LOCAL_EXISTING_ARTIFACT_REAGGREGATION_AND_CLOCK_COMPONENT_AUDIT",
        no_native_replay=True, no_live_changes=True, no_new_markets=True,
        provenance=[dict(path=str(p.relative_to(ROOT)), sha256=sha(p)) for p in inputs],
        script_sha256=sha(Path(__file__)),
        definitions=dict(payment_authority="atomic_responsibility_events.payments.responsibility_id",
            attempts="Existing Repair-frontier carriers only; not all orders servicing a responsibility",
            visible_payment="payment.t <= attempt.t; atomic updates precede frontier creation",
            retry_progress="Increase of this responsibility's cumulative actual payments since previous tracked attempt",
            first_attempt="First tracked Repair-frontier attempt, possibly after actual payments",
            no_progress_run="Consecutive RETRY_NO_PROGRESS entries, excluding FIRST; resets after actual payment",
            positive_run_median="Across positive observed run episodes, including unfinished last segments; not a censor-adjusted hazard estimate",
            latency="Descriptive observed milliseconds only, not an admission threshold",
            incomplete_responsibilities="Keep first payment/completion null; do not impute a zero duration",
            batch_carrier_keys="Contributors to the payment clock, not a unique per-payment attribution when multiple fills coexist"),
        markets=markets, witness_2026085_r62=witness, clock=clock,
        aggregate=dict(markets=len(markets), integrity_all_pass=all(m["integrity_pass"] for m in markets),
            tracked_responsibilities=sum(m["tracked_repair_responsibilities"] for m in markets),
            first_payment_changed=sum(m["corrected_first_payment_count"] for m in markets),
            false_never_paid=sum(m["false_never_paid_count"] for m in markets),
            v35_count_unchanged_markets=sum(m["v35_category_counts_unchanged"] for m in markets),
            v35_max_median_reproduced_markets=sum(m["v35_max_and_median_reproduced"] for m in markets)),
        elapsed_seconds=time.perf_counter() - started)
    dest = RESEARCH / (STEM + ".json")
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (RESEARCH / (STEM + ".md")).write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(dict(output=str(dest), aggregate=report["aggregate"],
        rows=[{k: v for k, v in m.items() if k != "lineages"} for m in markets],
        witness={k: v for k, v in witness.items() if k != "attempts"},
        witness_selected_attempts=[a for a in witness["attempts"] if a["attempt"] in (1, 30, 60)],
        clock=clock, elapsed_seconds=report["elapsed_seconds"]), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
