from __future__ import annotations

"""Build a clean long-form Phase-B counterfactual teacher table.

Sources:
1) current-V3B same-prefix exact role-switch forks (future physical labels), and
2) clean OUR V3B PRE_SUBMIT Decision/Responsibility Capsule (runtime state).

Each prefix yields two candidate rows: NEXT_REPAIR and NEXT_REEXPAND.  Runtime
state/action columns are causal/current; every future branch consequence is `label_*`.
"""

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path

import duckdb

EPS = 1e-9
BRANCHES = ("NEXT_REPAIR", "NEXT_REEXPAND")


def qpath(p: Path) -> str:
    return p.resolve().as_posix().replace("'", "''")


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def close(a, b) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-9)


def opp(side: str) -> str:
    return "DOWN" if side == "UP" else "UP"


def immediate_geometry(state: dict, side: str, price: float, qty: float) -> dict:
    u = float(state["state_up_qty"])
    d = float(state["state_down_qty"])
    c = float(state["state_cost"])
    pre_up = u - c
    pre_dn = d - c
    pre_floor = min(pre_up, pre_dn)
    pre_best = max(pre_up, pre_dn)
    if side == "UP":
        u += qty
    else:
        d += qty
    c += price * qty
    pu = u - c
    pd = d - c
    return {
        "context_immediate_delta_floor": min(pu, pd) - pre_floor,
        "context_immediate_delta_best": max(pu, pd) - pre_best,
        "context_immediate_delta_gap": abs(pu - pd) - abs(pre_up - pre_dn),
        "context_immediate_post_floor": min(pu, pd),
        "context_immediate_post_best": max(pu, pd),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fork-json", required=True, type=Path)
    ap.add_argument("--our-parquet", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    a = ap.parse_args()

    fork_path = a.fork_json.resolve()
    our_path = a.our_parquet.resolve()
    outdir = a.output_dir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    out_parquet = outdir / "phaseb_counterfactual_teacher_v1.parquet"
    manifest_path = outdir / "phaseb_counterfactual_teacher_v1.manifest.json"

    forks = json.loads(fork_path.read_text(encoding="utf-8"))
    source_rows = forks.get("rows") or []
    if not source_rows:
        raise SystemExit("fork source has no rows")

    con = duckdb.connect(database=":memory:")
    qp = qpath(our_path)
    desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{qp}')").fetchall()
    all_cols = [r[0] for r in desc]
    pure_state_cols = [c for c in all_cols if c.startswith("state_")]
    id_cols = ["market_id", "decision_ms", "carrier_key", "window_end_ms", "state_timing", "candidate_already_in_state"]
    select_cols = id_cols + pure_state_cols
    raw = con.execute(
        f"SELECT {','.join(qident(c) for c in select_cols)} FROM read_parquet('{qp}')"
    ).fetchall()
    by_key: dict[tuple[int, int], list[dict]] = {}
    for vals in raw:
        rec = dict(zip(select_cols, vals))
        by_key.setdefault((int(rec["market_id"]), int(rec["decision_ms"])), []).append(rec)

    out_rows = []
    join_missing = []
    join_ambiguous = []
    state_mismatch = []
    prefix_fail = []
    ledger_fail = []

    for fr in source_rows:
        mid = int(fr["marketId"])
        spec = fr["stateSpec"]
        t = int(spec["t"])
        if not bool(fr.get("valid")) or not bool((fr.get("checks") or {}).get("prefixParity")):
            prefix_fail.append((mid, t))
            continue
        if not bool((fr.get("checks") or {}).get("allLedgerClean")):
            ledger_fail.append((mid, t))
            continue
        matches = by_key.get((mid, t), [])
        if not matches:
            join_missing.append((mid, t))
            continue
        if len(matches) != 1:
            join_ambiguous.append((mid, t, len(matches)))
            continue
        st = matches[0]
        checks = {
            "up": close(spec["inventory"]["UP"], st["state_up_qty"]),
            "down": close(spec["inventory"]["DOWN"], st["state_down_qty"]),
            "cost": close(spec["cost"], st["state_cost"]),
            "floor": close(spec["floor"], st["state_floor"]),
            "best": close(spec["best"], st["state_best"]),
            "liveSlots": int(spec["liveSlots"]) == int(st["state_live_slots"]),
            "repairFamilySlots": int(spec["liveRepairSlots"]) == int(st["state_repair_family_live_slots"]),
            "qLadder": bool(spec["qLadderLive"]) == bool(st["state_q_ladder_live"]),
        }
        if not all(checks.values()):
            state_mismatch.append({"marketId": mid, "t": t, "checks": checks})
            continue

        prefix_digest = str((fr.get("prefixDigests") or {}).get("NATIVE") or "")
        base_row = {
            "market_id": mid,
            "decision_ms": t,
            "pair_id": f"{mid}:{t}:{prefix_digest[:16]}",
            "prefix_digest": prefix_digest,
            "window_end_ms": int(st["window_end_ms"]),
            "state_timing": st["state_timing"],
            "candidate_already_in_state": int(st["candidate_already_in_state"]),
        }
        for c in pure_state_cols:
            base_row[c] = st[c]
        # Additional exact-FIFO service state available at the fork prefix.
        base_row.update({
            "state_initial_debt_qty": float(spec["initialDebtQty"]),
            "state_paid_debt_qty": float(spec["paidDebtQty"]),
            "state_remaining_debt_qty": float(spec["remainingDebtQty"]),
            "state_repair_progress_frac": float(spec["repairProgressFrac"]),
            "state_free_slots": int(spec["freeSlots"]),
            "state_expand_family_live_slots": int(spec["liveExpandSlots"]),
            "state_q_pending_active": 1.0 if bool(spec.get("qPendingActive")) else 0.0,
        })

        for branch in BRANCHES:
            forced = ((fr.get("interventions") or {}).get(branch) or {}).get("forced") or {}
            if not bool(forced.get("ok")):
                raise RuntimeError(f"expected exercised branch {branch} at {mid}/{t}")
            side = str(forced["side"])
            role = str(forced["role"])
            price = float(forced["price"])
            qty = float(forced["qty"])
            action_class = "REPAIR" if branch == "NEXT_REPAIR" else "EXPAND"
            book = spec["book"]
            if branch == "NEXT_REPAIR":
                bid, ask = float(book["weakBid"]), float(book["weakAsk"])
            else:
                bid, ask = float(book["expandBid"]), float(book["expandAsk"])
            dominant = str(st["state_dominant_side"])
            debt_opposite = float(st["state_debt_down"] if side == "UP" else st["state_debt_up"])
            row = dict(base_row)
            row.update({
                "context_side_bid": bid,
                "context_side_ask": ask,
                "context_side_mid": (bid + ask) / 2.0,
                "context_side_is_dominant": 1.0 if dominant == side else 0.0,
                "context_side_is_weak": 1.0 if dominant in {"UP", "DOWN"} and dominant != side else 0.0,
                "context_target_debt_for_action_side": debt_opposite,
                "action_branch": branch,
                "action_class": action_class,
                "action_side": side,
                "action_role": role,
                "action_route": "PASSIVE",
                "action_price": price,
                "action_qty": qty,
                "action_price_to_bid": price - bid,
                "action_ask_to_price": ask - price,
                "action_pair_legal": 1.0,
                "action_q_arm_used": 1.0 if bool(forced.get("qArmUsed")) else 0.0,
            })
            row.update(immediate_geometry(st, side, price, qty))

            tm = (fr.get("terminalMetrics") or {})[branch]
            dn = (fr.get("terminalDeltaVsNative") or {}).get(branch) or {}
            res = (fr.get("branchResolution") or {}).get(branch)
            row.update({
                "label_terminal_floor": float(tm["floor"]),
                "label_terminal_best": float(tm["best"]),
                "label_terminal_gap": float(tm["gap"]),
                "label_terminal_favored_payoff": float(tm["favoredPayoff"]),
                "label_terminal_weak_payoff": float(tm["weakPayoff"]),
                "label_terminal_up_qty": float(tm["upQty"]),
                "label_terminal_down_qty": float(tm["downQty"]),
                "label_terminal_buy_notional": float(tm["buyNotional"]),
                "label_terminal_fills": int(tm["fills"]),
                "label_terminal_submits": int(tm["submits"]),
                "label_terminal_alternations": int(tm["alternations"]),
                "label_terminal_active_submits": int(tm["activeSubmits"]),
                "label_terminal_managed_repair_qty": float(tm["managedRepairQty"]),
                "label_terminal_managed_overflow_qty": float(tm["managedOverflowQty"]),
                "label_delta_vs_native_floor": float(dn.get("floor") or 0.0),
                "label_delta_vs_native_best": float(dn.get("best") or 0.0),
                "label_delta_vs_native_gap": float(dn.get("gap") or 0.0),
                "label_delta_vs_native_favored_payoff": float(dn.get("favoredPayoff") or 0.0),
                "label_delta_vs_native_weak_payoff": float(dn.get("weakPayoff") or 0.0),
                "label_delta_vs_native_fills": float(dn.get("fills") or 0.0),
                "label_delta_vs_native_submits": float(dn.get("submits") or 0.0),
                "label_delta_vs_native_alternations": float(dn.get("alternations") or 0.0),
                "label_resolution_kind": None if res is None else str(res.get("kind")),
                "label_resolution_lag_ms": None if res is None else int(res.get("lagMs")),
                "label_structural_fill": 1 if res is not None and str(res.get("kind")) == "FILL" else 0,
                "label_resolution_delta_floor": None if res is None else float((res.get("deltaFromPrefix") or {}).get("floor") or 0.0),
                "label_resolution_delta_best": None if res is None else float((res.get("deltaFromPrefix") or {}).get("best") or 0.0),
                "label_resolution_delta_gap": None if res is None else float((res.get("deltaFromPrefix") or {}).get("gap") or 0.0),
                "label_resolution_delta_favored_payoff": None if res is None else float((res.get("deltaFromPrefix") or {}).get("favoredPayoff") or 0.0),
                "label_resolution_delta_weak_payoff": None if res is None else float((res.get("deltaFromPrefix") or {}).get("weakPayoff") or 0.0),
                "label_resolution_delta_serviced_repair_debt": None if res is None else float((res.get("deltaFromPrefix") or {}).get("targetRepairDebt") or 0.0),
                "label_resolution_delta_total_repair_debt": None if res is None else float((res.get("deltaFromPrefix") or {}).get("totalRepairDebt") or 0.0),
            })
            out_rows.append(row)

    with tempfile.TemporaryDirectory(prefix="phaseb_teacher_") as td:
        jsonl = Path(td) / "rows.jsonl"
        with jsonl.open("w", encoding="utf-8", newline="\n") as fh:
            for r in out_rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        qj = qpath(jsonl)
        qo = qpath(out_parquet)
        con.execute(
            f"COPY (SELECT * FROM read_json_auto('{qj}', format='newline_delimited') ORDER BY market_id,decision_ms,action_branch) "
            f"TO '{qo}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )

    desc_out = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{qpath(out_parquet)}')").fetchall()
    cols_out = [r[0] for r in desc_out]
    row_count = int(con.execute(f"SELECT count(*) FROM read_parquet('{qpath(out_parquet)}')").fetchone()[0])
    pair_count = int(con.execute(f"SELECT count(DISTINCT pair_id) FROM read_parquet('{qpath(out_parquet)}')").fetchone()[0])
    bad_pair_count = int(con.execute(
        f"SELECT count(*) FROM (SELECT pair_id,count(*) n,count(DISTINCT action_branch) b FROM read_parquet('{qpath(out_parquet)}') GROUP BY pair_id HAVING n!=2 OR b!=2)"
    ).fetchone()[0])
    forbidden = [c for c in cols_out if "winner" in c.lower() or "target" in c.lower() and c != "context_target_debt_for_action_side"]
    labels = [c for c in cols_out if c.startswith("label_")]
    checks = {
        "sourceAllCorrectnessPass": bool(forks.get("allCorrectnessPass")),
        "noPrefixFailures": not prefix_fail,
        "noLedgerFailures": not ledger_fail,
        "cleanStateJoinComplete": not join_missing and not join_ambiguous,
        "cleanStateExactOnSharedSemantics": not state_mismatch,
        "twoBranchesPerPrefix": bad_pair_count == 0 and row_count == pair_count * 2,
        "futureOutcomesLabelPrefixed": len(labels) > 0,
        "noWinnerOrTargetTeacherColumns": not forbidden,
    }
    manifest = {
        "version": "MANAGEMENT_PHASEB_COUNTERFACTUAL_TEACHER_V1",
        "date": "2026-09-07",
        "researchOnly": True,
        "actionAuthority": False,
        "forkSource": str(fork_path),
        "forkSourceSha256": sha256_file(fork_path),
        "ourStateSource": str(our_path),
        "ourStateSourceSha256": sha256_file(our_path),
        "output": str(out_parquet),
        "prefixes": pair_count,
        "rows": row_count,
        "columns": cols_out,
        "checks": checks,
        "promotionGate": {"pass": all(checks.values()), "required": list(checks)},
        "stateSemantics": "clean PRE_SUBMIT current-V3B state joined by exact market_id+decision_ms; branches share exact fork prefix digest",
        "labelSemantics": "future same-prefix exact-HFT branch consequences only; label_* never runtime input by default",
        "boundaries": [
            "two legal exercised Passive candidate branches per prefix: Repair and Re-Expand",
            "same exact strict-past prefix across branches",
            "exact FIFO ledger clean",
            "no scalar reward",
            "no winner/Target/future runtime input",
            "no NEW24-B",
            "no dream fill",
            "no 8781",
        ],
        "diagnostics": {
            "joinMissing": join_missing,
            "joinAmbiguous": join_ambiguous,
            "stateMismatch": state_mismatch,
            "prefixFail": prefix_fail,
            "ledgerFail": ledger_fail,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "prefixes": pair_count,
        "rows": row_count,
        "parquetBytes": out_parquet.stat().st_size,
        "checks": checks,
        "promotionPass": manifest["promotionGate"]["pass"],
        "output": str(out_parquet),
        "manifest": str(manifest_path),
    }, indent=2, ensure_ascii=False))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
