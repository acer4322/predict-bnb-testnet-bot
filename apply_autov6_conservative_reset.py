from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def replace_function(text: str, start: str, end: str, replacement: str, label: str) -> str:
    match = re.search(start + r".*?(?=" + end + r")", text, flags=re.S | re.M)
    if match is None:
        raise RuntimeError(f"{label}: function block anchor not found")
    return text[: match.start()] + replacement.rstrip() + "\n\n" + text[match.end() :]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_research_forward(text: str) -> str:
    if '"APPLY_V6_WARMUP"' in text:
        raise RuntimeError("AUTO_V6 conservative fallback already appears installed")

    replacement = '''def observer_v6_auto_decision(
    history: list[dict[str, Any]],
    current_gate: dict[str, Any] | None,
    *,
    expected_market_id: int | None = None,
) -> dict[str, Any]:
    """Choose whether V6 should be applied from prior official source results.

    V6 is the safe default. Warm-up, cohort shortage, and mixed evidence no
    longer bypass the current V6 decision. Bypass is allowed only when the
    trades V6 would have blocked were profitable in both fast and slow windows.
    """

    usable: list[dict[str, Any]] = []
    for row in history[-OBSERVER_AUTO_V6_SLOW_WINDOW:]:
        try:
            unit_pnl = float(row["unit_pnl"])
            v6_allowed = row["v6_allowed"]
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(unit_pnl) or not isinstance(v6_allowed, bool):
            continue
        usable.append(
            {
                "market_id": row.get("market_id"),
                "unit_pnl": unit_pnl,
                "v6_allowed": v6_allowed,
            }
        )

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        allowed_rows = [row for row in rows if row["v6_allowed"]]
        blocked_rows = [row for row in rows if not row["v6_allowed"]]
        return {
            "samples": len(rows),
            "allowedSamples": len(allowed_rows),
            "blockedSamples": len(blocked_rows),
            "passRatePct": (
                len(allowed_rows) / len(rows) * 100.0 if rows else None
            ),
            "sourceUnitPnl": sum(row["unit_pnl"] for row in rows),
            "allowedUnitPnl": sum(row["unit_pnl"] for row in allowed_rows),
            "blockedUnitPnl": sum(row["unit_pnl"] for row in blocked_rows),
        }

    fast = summarize(usable[-OBSERVER_AUTO_V6_FAST_WINDOW:])
    slow = summarize(usable)
    history_ready = slow["samples"] >= OBSERVER_AUTO_V6_SLOW_WINDOW
    cohorts_ready = bool(
        fast["allowedSamples"] >= OBSERVER_AUTO_V6_MIN_FAST_COHORT
        and fast["blockedSamples"] >= OBSERVER_AUTO_V6_MIN_FAST_COHORT
        and slow["allowedSamples"] >= OBSERVER_AUTO_V6_MIN_SLOW_COHORT
        and slow["blockedSamples"] >= OBSERVER_AUTO_V6_MIN_SLOW_COHORT
    )
    v6_proven_better = bool(
        history_ready
        and cohorts_ready
        and fast["allowedUnitPnl"] > 0
        and slow["allowedUnitPnl"] > 0
        and fast["blockedUnitPnl"] < 0
        and slow["blockedUnitPnl"] < 0
    )
    bypass_proven_better = bool(
        history_ready
        and cohorts_ready
        and fast["blockedUnitPnl"] > 0
        and slow["blockedUnitPnl"] > 0
    )

    if not history_ready:
        mode = "APPLY_V6_WARMUP"
        mode_reason = "OFFICIAL_HISTORY_WARMUP_USE_V6"
    elif not cohorts_ready:
        mode = "APPLY_V6_COHORT_WARMUP"
        mode_reason = "INSUFFICIENT_ALLOW_BLOCK_COHORTS_USE_V6"
    elif v6_proven_better:
        mode = "APPLY_V6_PROVEN"
        mode_reason = "V6_IMPROVES_FAST_AND_SLOW_OFFICIAL_WINDOWS"
    elif bypass_proven_better:
        mode = "BYPASS_V6_PROVEN"
        mode_reason = "BLOCKED_COHORT_PROFITABLE_FAST_AND_SLOW"
    else:
        mode = "APPLY_V6_UNCERTAIN"
        mode_reason = "MIXED_HISTORY_USE_V6"

    current_v6 = futures_lead_observer_decision(
        "V6", current_gate, expected_market_id=expected_market_id
    )
    bypass = mode == "BYPASS_V6_PROVEN"
    allowed = bypass or current_v6["allowed"] is True
    return {
        "allowed": allowed,
        "status": "ALLOW" if allowed else "BLOCK",
        "mode": mode,
        "reason": (
            mode_reason
            if allowed
            else str(current_v6.get("reason") or mode_reason)
        ),
        "modeReason": mode_reason,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "officialHistoryOnly": True,
        "currentMarketExcluded": True,
        "currentGateAvailable": isinstance(current_gate, dict),
        "v6ProvenBetter": v6_proven_better,
        "bypassProvenBetter": bypass_proven_better,
        "fallbackPolicy": (
            "apply V6 unless bypass is proven by profitable blocked cohorts "
            "in both fast and slow official-history windows"
        ),
        "fastWindow": fast,
        "slowWindow": slow,
        "minimumFastCohort": OBSERVER_AUTO_V6_MIN_FAST_COHORT,
        "minimumSlowCohort": OBSERVER_AUTO_V6_MIN_SLOW_COHORT,
        "currentV6Decision": current_v6,
    }'''

    return replace_function(
        text,
        r"^def observer_v6_auto_decision\(",
        r"^def _finite\(",
        replacement,
        "replace observer_v6_auto_decision",
    )


def patch_server(text: str) -> str:
    if "_research_observer_auto_v6_reset_state" in text:
        raise RuntimeError("AUTO_V6 reset-aware history already appears installed")

    reset_and_history = '''    def _research_observer_auto_v6_reset_state(
        self,
        strategy: str,
    ) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute(
                """SELECT cutoff_trade_id, reset_at
                     FROM strategy_measurement_resets
                    WHERE strategy=?
                    ORDER BY id DESC
                    LIMIT 1""",
                (str(strategy).upper(),),
            ).fetchone()
        return {
            "resetAt": str(row["reset_at"]) if row else None,
            "cutoffTradeId": int(row["cutoff_trade_id"]) if row else 0,
        }

    def _research_observer_auto_v6_history(
        self,
        source_strategy: str,
        *,
        before_market_id: int,
        after_trade_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Return post-reset prior official source results with frozen V6 decisions."""
        rows = self.db.execute(
            """SELECT t.id AS source_trade_id, t.market_id, t.stake, t.pnl,
                      t.diagnostics_json
                 FROM trades AS t
                 JOIN market_settlements AS s ON s.market_id=t.market_id
                WHERE t.strategy=? AND t.market_id < ? AND t.id > ?
                  AND s.status='OFFICIAL' AND s.official_winner IS NOT NULL
                  AND t.status IN ('SETTLED_WIN', 'SETTLED_LOSS')
                  AND t.pnl IS NOT NULL AND t.stake > 0
                ORDER BY t.market_id DESC, t.id DESC
                LIMIT ?""",
            (
                source_strategy,
                int(before_market_id),
                max(0, int(after_trade_id)),
                OBSERVER_AUTO_V6_SLOW_WINDOW * 5,
            ),
        ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            try:
                diagnostics = json.loads(str(row["diagnostics_json"] or "{}"))
                context = diagnostics["realtime_context"]
                gates = context["m01o_observer_gates"]
                gate = gates["F1"]
                market_id = int(row["market_id"])
                sample_count = int(gate["historicalSampleCount"])
                minimum_samples = int(gate.get("minSettledSamples") or 6)
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue
            if (
                not isinstance(gate, dict)
                or str(gate.get("profile") or "").upper() != "F1"
                or str(gate.get("dataQualityStatus") or "").upper() != "READY"
                or sample_count < minimum_samples
            ):
                continue
            decision = futures_lead_observer_decision(
                "V6", gate, expected_market_id=market_id
            )
            history.append(
                {
                    "source_trade_id": int(row["source_trade_id"]),
                    "market_id": market_id,
                    "unit_pnl": float(row["pnl"]) / float(row["stake"]),
                    "v6_allowed": bool(decision["allowed"]),
                }
            )
            if len(history) >= OBSERVER_AUTO_V6_SLOW_WINDOW:
                break
        return list(reversed(history))'''

    text = replace_function(
        text,
        r"^    def _research_observer_auto_v6_history\(",
        r"^    def _research_observer_auto_v6_state\(",
        reset_and_history,
        "replace AUTO_V6 history method",
    )

    state_method = '''    def _research_observer_auto_v6_state(
        self, strategy: str
    ) -> dict[str, Any]:
        source_strategy = OBSERVER_AUTO_V6_STRATEGY_RULES[strategy]
        reset = self._research_observer_auto_v6_reset_state(strategy)
        history = self._research_observer_auto_v6_history(
            source_strategy,
            before_market_id=2**63 - 1,
            after_trade_id=int(reset["cutoffTradeId"]),
        )
        return {
            **observer_v6_auto_decision(history, None),
            "strategy": strategy,
            "sourceStrategy": source_strategy,
            "historyResetAt": reset["resetAt"],
            "historyCutoffTradeId": (
                int(reset["cutoffTradeId"])
                if reset["resetAt"] is not None
                else None
            ),
            "postResetSamples": len(history),
            "historyScope": "source trades opened after the latest reset marker",
        }'''

    text = replace_function(
        text,
        r"^    def _research_observer_auto_v6_state\(",
        r"^    @staticmethod\n    def _research_experiment_segment\(",
        state_method,
        "replace AUTO_V6 state method",
    )

    old_live = '''                    auto_history = self._research_observer_auto_v6_history(
                        source_strategy,
                        before_market_id=market_id,
                    )
                    observer_auto_v6_decision = observer_v6_auto_decision(
                        auto_history,
                        observer_gate,
                        expected_market_id=market_id,
                    )'''

    new_live = '''                    auto_reset = self._research_observer_auto_v6_reset_state(
                        strategy
                    )
                    auto_history = self._research_observer_auto_v6_history(
                        source_strategy,
                        before_market_id=market_id,
                        after_trade_id=int(auto_reset["cutoffTradeId"]),
                    )
                    observer_auto_v6_decision = observer_v6_auto_decision(
                        auto_history,
                        observer_gate,
                        expected_market_id=market_id,
                    )
                    observer_auto_v6_decision.update(
                        {
                            "historyResetAt": auto_reset["resetAt"],
                            "historyCutoffTradeId": (
                                int(auto_reset["cutoffTradeId"])
                                if auto_reset["resetAt"] is not None
                                else None
                            ),
                            "postResetSamples": len(auto_history),
                            "historyScope": (
                                "source trades opened after the latest reset marker"
                            ),
                        }
                    )'''

    return replace_once(
        text,
        old_live,
        new_live,
        "make live AUTO_V6 history reset-aware",
    )


def patch_tests(text: str) -> str:
    replacements = {
        'assert applied["mode"] == "APPLY_V6"': 'assert applied["mode"] == "APPLY_V6_PROVEN"',
        'assert blocked_current["mode"] == "APPLY_V6"': 'assert blocked_current["mode"] == "APPLY_V6_PROVEN"',
        'assert bypassed["mode"] == "BYPASS_V6"': 'assert bypassed["mode"] == "BYPASS_V6_PROVEN"',
        'assert bypassed["reason"] == "V6_NOT_PROVEN_BETTER"': 'assert bypassed["reason"] == "BLOCKED_COHORT_PROFITABLE_FAST_AND_SLOW"',
    }
    for old, new in replacements.items():
        if old not in text:
            raise RuntimeError(f"test anchor missing: {old}")
        text = text.replace(old, new, 1)

    warmup_tests = '''def test_auto_v6_shadow_uses_static_v6_during_official_history_warmup(
    tmp_path,
) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_microprice_enabled"] = True
    values["strategy_r_microprice_observer_auto_v6_enabled"] = True
    store.update_config(values)
    store.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=190.2, current=False),
        200,
        realtime_context=prediction_context(
            1, observer_gate(currentEffectiveCrossovers=0)
        ),
    )
    opened = store.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=180.0, current=True),
        200,
        realtime_context=prediction_context(
            2, observer_gate(currentEffectiveCrossovers=0)
        ),
    )

    assert [item["strategy"] for item in opened] == ["R_MICROPRICE"]
    assert set(OBSERVER_AUTO_V6_STRATEGIES).issubset(RESEARCH_STRATEGIES)
    decision = observer_v6_auto_decision(
        [],
        observer_gate(currentMarketId=11, currentEffectiveCrossovers=0),
        expected_market_id=11,
    )
    assert decision["mode"] == "APPLY_V6_WARMUP"
    assert decision["modeReason"] == "OFFICIAL_HISTORY_WARMUP_USE_V6"
    assert decision["currentV6Decision"]["allowed"] is False
    assert decision["allowed"] is False


def test_auto_v6_cohort_shortage_uses_v6_instead_of_bypass() -> None:
    history = [
        {
            "market_id": index,
            "v6_allowed": index >= 96,
            "unit_pnl": 0.25 if index >= 96 else -0.25,
        }
        for index in range(100)
    ]
    decision = observer_v6_auto_decision(
        history,
        observer_gate(currentMarketId=200, currentEffectiveCrossovers=1),
        expected_market_id=200,
    )
    assert decision["fastWindow"]["allowedSamples"] == 4
    assert decision["slowWindow"]["allowedSamples"] == 4
    assert decision["mode"] == "APPLY_V6_COHORT_WARMUP"
    assert decision["modeReason"] == "INSUFFICIENT_ALLOW_BLOCK_COHORTS_USE_V6"
    assert decision["allowed"] is False


def test_auto_v6_reset_marker_excludes_pre_reset_source_history(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")

    def insert_source(*, market_id: int, pnl: float, v6_allowed: bool) -> int:
        gate = observer_gate(
            currentMarketId=market_id,
            currentEffectiveCrossovers=2 if v6_allowed else 1,
            currentBothSidesTouched=False,
        )
        diagnostics = json.dumps(
            {
                "signal": 0.25,
                "realtime_context": {
                    "m01o_observer_gates": {"F1": gate}
                },
            },
            sort_keys=True,
        )
        cursor = store.db.execute(
            """INSERT INTO trades(
                   strategy, topic_id, market_id, side, status,
                   entry_price, target_price, exit_price,
                   stake, shares, fees, fee_rate_bps, pnl,
                   opened_at, closed_at, note, strategy_version,
                   model_probability, model_edge, model_sigma,
                   diagnostics_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                         ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "R_MICROPRICE", market_id, market_id, "UP",
                "SETTLED_WIN" if pnl > 0 else "SETTLED_LOSS",
                0.40, None, None, 5.0, 12.5, 0.0, 200, pnl,
                "2026-08-03T00:00:00+00:00",
                "2026-08-03T00:05:00+00:00",
                "AUTO_V6 reset fixture", "test",
                None, None, None, diagnostics,
            ),
        )
        store.db.execute(
            """INSERT INTO market_settlements(
                   market_id, topic_id, start_price, proxy_winner,
                   official_winner, official_end_price, status,
                   first_settled_at, official_settled_at,
                   check_attempts, last_checked_at, h_processed
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_id, market_id, 100.0, "UP", "UP", 101.0,
                "OFFICIAL",
                "2026-08-03T00:05:00+00:00",
                "2026-08-03T00:05:00+00:00",
                1, "2026-08-03T00:05:00+00:00", 0,
            ),
        )
        store.db.commit()
        return int(cursor.lastrowid)

    old_id = insert_source(market_id=100, pnl=-5.0, v6_allowed=False)
    reset = store.reset_strategy_measurement(
        "R_MICROPRICE_OBSERVER_AUTO_V6"
    )
    assert reset["cutoffTradeId"] >= old_id
    new_id = insert_source(market_id=101, pnl=5.0, v6_allowed=True)
    assert new_id > reset["cutoffTradeId"]

    state = store._research_observer_auto_v6_state(
        "R_MICROPRICE_OBSERVER_AUTO_V6"
    )
    assert state["historyCutoffTradeId"] == reset["cutoffTradeId"]
    assert state["historyResetAt"] == reset["resetAt"]
    assert state["postResetSamples"] == 1
    assert state["fastWindow"]["samples"] == 1
    assert state["fastWindow"]["allowedSamples"] == 1
    assert state["fastWindow"]["blockedSamples"] == 0'''

    return replace_function(
        text,
        r"^def test_auto_v6_shadow_bypasses_during_official_history_warmup\(",
        r"^@pytest\.mark\.parametrize\(",
        warmup_tests,
        "replace AUTO_V6 warmup tests",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()

    root = Path(args.project).resolve()
    files = {
        "research": root / "src/predict_bot/research_forward.py",
        "server": root / "src/predict_bot/server.py",
        "tests": root / "tests/test_research_forward.py",
    }
    for path in files.values():
        if not path.exists():
            print(f"Missing: {path}", file=sys.stderr)
            return 2

    originals = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
    try:
        updated = {
            "research": patch_research_forward(originals["research"]),
            "server": patch_server(originals["server"]),
            "tests": patch_tests(originals["tests"]),
        }
        for name, content in updated.items():
            ast.parse(content, filename=str(files[name]))
    except Exception as exc:
        print(f"Patch preparation failed: {exc}", file=sys.stderr)
        return 3

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / f"autov6-backup-{stamp}"
    backup.mkdir(parents=True)
    for path in files.values():
        dest = backup / path.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    try:
        for name, path in files.items():
            path.write_text(updated[name], encoding="utf-8", newline="\n")
        commands = [
            [sys.executable, "-m", "py_compile", *(str(path) for path in files.values())],
            ["git", "diff", "--check"],
        ]
        if args.run_tests:
            commands.append(
                [sys.executable, "-m", "pytest", "tests/test_research_forward.py", "-q"]
            )
        for command in commands:
            result = subprocess.run(command, cwd=root)
            if result.returncode != 0:
                raise RuntimeError(
                    f"command failed ({result.returncode}): {' '.join(command)}"
                )
    except Exception as exc:
        for path in files.values():
            shutil.copy2(backup / path.relative_to(root), path)
        print(f"Validation failed; original files restored: {exc}", file=sys.stderr)
        print(f"Backup kept at: {backup}", file=sys.stderr)
        return 4

    print("AUTO_V6 conservative fallback and reset-aware history installed.")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
