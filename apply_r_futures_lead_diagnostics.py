from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected one anchor, found {count}. No files were written."
        )
    return text.replace(old, new, 1)


def insert_before_once(text: str, anchor: str, block: str, label: str) -> str:
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected one anchor, found {count}. No files were written."
        )
    return text.replace(anchor, block + anchor, 1)


def patch_research_forward(text: str) -> str:
    if "def futures_lead_diagnostics(" in text:
        raise RuntimeError("futures_lead_diagnostics already exists")

    helper = r'''
def futures_lead_diagnostics(
    current: dict[str, float],
    previous: dict[str, float] | None,
    *,
    params: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Expose the exact inputs and gates used by R_FUTURES_LEAD."""
    selected = params or RESEARCH_PARAMETERS["R_FUTURES_LEAD"]
    configured_lag_seconds = float(selected["lag"])
    min_lead_bps = float(selected["min_lead_bps"])
    max_source_age_ms = float(selected["max_source_age_ms"])

    def finite_value(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    def source_age_ms(row: dict[str, float] | None) -> float | None:
        if row is None:
            return None
        spot_age = finite_value(row.get("spot_age_ms"))
        futures_age = finite_value(row.get("futures_age_ms"))
        if spot_age is None or futures_age is None:
            return None
        return max(spot_age, futures_age)

    current_timestamp = finite_value(current.get("timestamp_ns"))
    previous_timestamp = (
        finite_value(previous.get("timestamp_ns"))
        if previous is not None
        else None
    )
    actual_lag_seconds = (
        (current_timestamp - previous_timestamp) / 1_000_000_000
        if current_timestamp is not None
        and previous_timestamp is not None
        else None
    )
    lag_valid = bool(
        actual_lag_seconds is not None
        and configured_lag_seconds
        <= actual_lag_seconds
        <= configured_lag_seconds + 2.5
    )

    current_spot_age = finite_value(current.get("spot_age_ms"))
    current_futures_age = finite_value(current.get("futures_age_ms"))
    previous_spot_age = (
        finite_value(previous.get("spot_age_ms"))
        if previous is not None
        else None
    )
    previous_futures_age = (
        finite_value(previous.get("futures_age_ms"))
        if previous is not None
        else None
    )

    current_fresh = _fresh_source_sample(current, max_source_age_ms)
    previous_fresh = bool(
        previous is not None
        and _fresh_source_sample(previous, max_source_age_ms)
    )

    current_spot = finite_value(current.get("spot_price"))
    current_futures = finite_value(current.get("futures_price"))
    previous_spot = (
        finite_value(previous.get("spot_price"))
        if previous is not None
        else None
    )
    previous_futures = (
        finite_value(previous.get("futures_price"))
        if previous is not None
        else None
    )

    spot_return = (
        _log_return(previous_spot, current_spot)
        if previous_spot is not None and current_spot is not None
        else None
    )
    futures_return = (
        _log_return(previous_futures, current_futures)
        if previous_futures is not None and current_futures is not None
        else None
    )
    spot_return_bps = (
        spot_return * 10_000 if spot_return is not None else None
    )
    futures_return_bps = (
        futures_return * 10_000 if futures_return is not None else None
    )
    lead_bps = (
        abs(futures_return_bps) - abs(spot_return_bps)
        if spot_return_bps is not None and futures_return_bps is not None
        else None
    )
    side = (
        "UP"
        if futures_return is not None and futures_return > 0
        else "DOWN"
        if futures_return is not None and futures_return < 0
        else None
    )
    signal_bps = (
        math.copysign(float(lead_bps), float(futures_return))
        if lead_bps is not None
        and futures_return is not None
        and abs(futures_return) >= 1e-12
        else None
    )

    if previous is None:
        reason = "NO_LAGGED_SAMPLE"
    elif not current_fresh:
        reason = "CURRENT_SOURCE_STALE"
    elif not previous_fresh:
        reason = "PREVIOUS_SOURCE_STALE"
    elif spot_return is None or futures_return is None:
        reason = "INVALID_PRICE"
    elif abs(futures_return) < 1e-12:
        reason = "FUTURES_NO_MOVE"
    elif lead_bps is None or lead_bps < min_lead_bps:
        reason = "LEAD_BELOW_MINIMUM"
    else:
        reason = "SIGNAL_READY"

    return {
        "configuredLagSeconds": configured_lag_seconds,
        "actualLagSeconds": actual_lag_seconds,
        "lagSampleFound": previous is not None,
        "lagValid": lag_valid,
        "currentTimestampNs": (
            int(current_timestamp) if current_timestamp is not None else None
        ),
        "previousTimestampNs": (
            int(previous_timestamp) if previous_timestamp is not None else None
        ),
        "currentSpot": current_spot,
        "previousSpot": previous_spot,
        "currentFutures": current_futures,
        "previousFutures": previous_futures,
        "currentSpotAgeMs": current_spot_age,
        "currentFuturesAgeMs": current_futures_age,
        "previousSpotAgeMs": previous_spot_age,
        "previousFuturesAgeMs": previous_futures_age,
        "currentSourceAgeMs": source_age_ms(current),
        "previousSourceAgeMs": source_age_ms(previous),
        "sourceAgeAggregation": "max(spot_age_ms, futures_age_ms)",
        "maxSourceAgeMs": max_source_age_ms,
        "currentSourceFresh": current_fresh,
        "previousSourceFresh": previous_fresh,
        "sourceFreshPassed": current_fresh and previous_fresh,
        "spotReturnBps": spot_return_bps,
        "futuresReturnBps": futures_return_bps,
        "leadBps": lead_bps,
        "signalBps": signal_bps,
        "minLeadBps": min_lead_bps,
        "leadPassed": bool(
            lead_bps is not None and lead_bps >= min_lead_bps
        ),
        "side": side,
        "decisionReason": reason,
    }


'''
    text = insert_before_once(
        text,
        "def _signed_residual_leg(\n",
        helper,
        "insert futures_lead_diagnostics",
    )

    old = r'''    if previous is None:
        return None
    if strategy in {"R_FUTURES_LEAD", "R_CONSENSUS"}:
        max_source_age_ms = float(params["max_source_age_ms"])
        if not (
            _fresh_source_sample(previous, max_source_age_ms)
            and _fresh_source_sample(current, max_source_age_ms)
        ):
            return None
    spot_return = _log_return(previous["spot_price"], current["spot_price"])
'''
    new = r'''    if previous is None:
        return None
    if strategy == "R_FUTURES_LEAD":
        diagnostics = futures_lead_diagnostics(
            current,
            previous,
            params=params,
        )
        if diagnostics["decisionReason"] != "SIGNAL_READY":
            return None
        return {
            "side": str(diagnostics["side"]),
            "signal": float(diagnostics["signalBps"]),
        }
    if strategy == "R_CONSENSUS":
        max_source_age_ms = float(params["max_source_age_ms"])
        if not (
            _fresh_source_sample(previous, max_source_age_ms)
            and _fresh_source_sample(current, max_source_age_ms)
        ):
            return None
    spot_return = _log_return(previous["spot_price"], current["spot_price"])
'''
    text = replace_once(text, old, new, "share diagnostics with signal")

    old = r'''    if strategy == "R_FUTURES_LEAD":
        lead = abs(futures_return) - abs(spot_return)
        if abs(futures_return) < 1e-12 or lead * 10_000 < params["min_lead_bps"]:
            return None
        source_side = "UP" if futures_return > 0 else "DOWN"
        source_signal = math.copysign(lead * 10_000, futures_return)
        return {
            "side": source_side,
            "signal": source_signal,
        }

'''
    text = replace_once(text, old, "", "remove old duplicate calculation")
    return text


def patch_server(text: str) -> str:
    if "research_runtime_diagnostics_state" in text:
        raise RuntimeError("server diagnostics already exist")

    text = replace_once(
        text,
        '''    filtered_futures_lead_signal,
    futures_lead_observer_decision,
''',
        '''    filtered_futures_lead_signal,
    futures_lead_diagnostics as research_futures_lead_diagnostics,
    futures_lead_observer_decision,
''',
        "import diagnostics",
    )

    text = replace_once(
        text,
        '''        self._research_samples = ResearchSampleBuffer()
        self._m_signal_row_cache: dict[tuple[str, int], sqlite3.Row | None] = {}
''',
        '''        self._research_samples = ResearchSampleBuffer()
        self._research_runtime_lock = threading.RLock()
        self._research_runtime_diagnostics: dict[str, Any] = {
            "preview": None,
            "lastEntryWindowEvaluation": None,
            "reasonCounters": self._futures_lead_reason_counters(),
            "currentMarketCounters": self._futures_lead_reason_counters(),
            "currentMarketId": None,
            "_lastCountedTimestampNs": None,
        }
        self._m_signal_row_cache: dict[tuple[str, int], sqlite3.Row | None] = {}
''',
        "initialize diagnostics",
    )

    methods = r'''    @staticmethod
    def _futures_lead_reason_counters() -> dict[str, int]:
        return {
            "NO_LAGGED_SAMPLE": 0,
            "INVALID_ACTUAL_LAG": 0,
            "CURRENT_SOURCE_STALE": 0,
            "PREVIOUS_SOURCE_STALE": 0,
            "INVALID_PRICE": 0,
            "FUTURES_NO_MOVE": 0,
            "LEAD_BELOW_MINIMUM": 0,
            "SIGNAL_READY": 0,
        }

    def _record_futures_lead_runtime_diagnostics(
        self,
        diagnostics: dict[str, Any],
        *,
        market_id: int,
        evaluated_at: str,
        seconds_left: float,
        entry_lower: float,
        entry_upper: float,
    ) -> None:
        inside_entry_window = entry_lower <= seconds_left <= entry_upper
        payload = {
            **diagnostics,
            "marketId": int(market_id),
            "evaluatedAt": str(evaluated_at),
            "secondsLeft": float(seconds_left),
            "entryWindowLowerSecondsLeft": float(entry_lower),
            "entryWindowUpperSecondsLeft": float(entry_upper),
            "insideEntryWindow": inside_entry_window,
        }
        payload = json.loads(
            json.dumps(payload, allow_nan=False, default=str)
        )

        with self._research_runtime_lock:
            state = self._research_runtime_diagnostics
            if state["currentMarketId"] != int(market_id):
                state["currentMarketId"] = int(market_id)
                state["currentMarketCounters"] = (
                    self._futures_lead_reason_counters()
                )
                state["lastEntryWindowEvaluation"] = None
                state["_lastCountedTimestampNs"] = None

            state["preview"] = payload

            if not inside_entry_window:
                return

            state["lastEntryWindowEvaluation"] = payload
            timestamp_ns = payload.get("currentTimestampNs")
            if timestamp_ns == state["_lastCountedTimestampNs"]:
                return

            reason = str(payload.get("decisionReason") or "")
            if reason in state["reasonCounters"]:
                state["reasonCounters"][reason] += 1
                state["currentMarketCounters"][reason] += 1
            state["_lastCountedTimestampNs"] = timestamp_ns

    def research_runtime_diagnostics_state(self) -> dict[str, Any]:
        with self._research_runtime_lock:
            state = self._research_runtime_diagnostics
            result = {
                "preview": state["preview"],
                "lastEntryWindowEvaluation": (
                    state["lastEntryWindowEvaluation"]
                ),
                "reasonCounters": dict(state["reasonCounters"]),
                "currentMarketCounters": dict(
                    state["currentMarketCounters"]
                ),
                "currentMarketId": state["currentMarketId"],
            }
        return json.loads(
            json.dumps(result, allow_nan=False, default=str)
        )

'''
    text = insert_before_once(
        text,
        "    def _research_open_exposure(self) -> float:\n",
        methods,
        "insert Store methods",
    )

    text = replace_once(
        text,
        '''        seconds_left = float(current["seconds_left"])
        exposure = self._research_open_exposure()
''',
        '''        seconds_left = float(current["seconds_left"])

        if bool(cfg.get("strategy_r_futures_lead_enabled")):
            lead_params = RESEARCH_PARAMETERS["R_FUTURES_LEAD"]
            lead_horizon = float(lead_params["horizon"])
            lead_entry_lower = lead_horizon - 3.0
            lead_entry_upper = lead_horizon
            lead_previous = self._research_samples.lagged(
                market_id,
                current,
                float(lead_params["lag"]),
            )
            lead_diagnostics = research_futures_lead_diagnostics(
                current,
                lead_previous,
                params=lead_params,
            )
            self._record_futures_lead_runtime_diagnostics(
                lead_diagnostics,
                market_id=market_id,
                evaluated_at=str(snapshot.get("timestamp") or utc_iso()),
                seconds_left=seconds_left,
                entry_lower=lead_entry_lower,
                entry_upper=lead_entry_upper,
            )

        exposure = self._research_open_exposure()
''',
        "record runtime evaluation",
    )

    text = replace_once(
        text,
        '''    m_realtime = M_REALTIME.state() if M_REALTIME else None
    min_observer_samples = int(
''',
        '''    m_realtime = M_REALTIME.state() if M_REALTIME else None
    if m_realtime is not None:
        m_realtime = {
            **m_realtime,
            "researchDiagnostics": {
                "R_FUTURES_LEAD": (
                    STORE.research_runtime_diagnostics_state()
                ),
            },
        }
    min_observer_samples = int(
''',
        "expose through /api/realtime",
    )
    return text


def patch_tests(text: str) -> str:
    if "test_futures_lead_diagnostics_exposes_exact_runtime_values" in text:
        raise RuntimeError("diagnostics tests already exist")

    text = replace_once(
        text,
        "import json\nfrom datetime import datetime, timezone\n",
        "import json\nimport math\nfrom datetime import datetime, timezone\n",
        "import math",
    )
    text = replace_once(
        text,
        '''    filtered_futures_lead_signal,
    futures_lead_observer_decision,
''',
        '''    filtered_futures_lead_signal,
    futures_lead_diagnostics,
    futures_lead_observer_decision,
''',
        "import test helper",
    )

    tests = r'''

def test_futures_lead_diagnostics_exposes_exact_runtime_values() -> None:
    previous = sample(
        timestamp_ns=17_000_000_000,
        seconds_left=183.0,
        current=False,
    )
    current = sample(
        timestamp_ns=20_000_000_000,
        seconds_left=180.0,
        current=True,
    )
    diagnostics = futures_lead_diagnostics(current, previous)

    expected_spot = math.log(100.1 / 100.0) * 10_000
    expected_futures = math.log(100.2 / 100.0) * 10_000
    expected_lead = abs(expected_futures) - abs(expected_spot)

    assert diagnostics["actualLagSeconds"] == pytest.approx(3.0)
    assert diagnostics["currentSourceAgeMs"] == pytest.approx(100.0)
    assert diagnostics["previousSourceAgeMs"] == pytest.approx(100.0)
    assert diagnostics["spotReturnBps"] == pytest.approx(expected_spot)
    assert diagnostics["futuresReturnBps"] == pytest.approx(expected_futures)
    assert diagnostics["leadBps"] == pytest.approx(expected_lead)
    assert diagnostics["decisionReason"] == "SIGNAL_READY"
    assert diagnostics["side"] == "UP"
    json.dumps(diagnostics, allow_nan=False)


def test_futures_lead_diagnostics_reports_source_staleness() -> None:
    previous = sample(
        timestamp_ns=17_000_000_000,
        seconds_left=183.0,
        current=False,
    )
    current = {
        **sample(
            timestamp_ns=20_000_000_000,
            seconds_left=180.0,
            current=True,
        ),
        "futures_age_ms": 501.0,
    }
    diagnostics = futures_lead_diagnostics(current, previous)
    assert diagnostics["currentSourceAgeMs"] == pytest.approx(501.0)
    assert diagnostics["decisionReason"] == "CURRENT_SOURCE_STALE"

    stale_previous = {**previous, "spot_age_ms": 600.0}
    diagnostics = futures_lead_diagnostics(
        sample(
            timestamp_ns=20_000_000_000,
            seconds_left=180.0,
            current=True,
        ),
        stale_previous,
    )
    assert diagnostics["previousSourceAgeMs"] == pytest.approx(600.0)
    assert diagnostics["decisionReason"] == "PREVIOUS_SOURCE_STALE"


def test_futures_lead_diagnostics_keeps_preview_without_lag_sample() -> None:
    current = sample(
        timestamp_ns=20_000_000_000,
        seconds_left=180.0,
        current=True,
    )
    diagnostics = futures_lead_diagnostics(current, None)
    assert diagnostics["lagSampleFound"] is False
    assert diagnostics["currentSpot"] == pytest.approx(100.1)
    assert diagnostics["currentFutures"] == pytest.approx(100.2)
    assert diagnostics["decisionReason"] == "NO_LAGGED_SAMPLE"

'''
    return insert_before_once(
        text,
        "\ndef test_confirmation_add_ladder_is_fixed_and_never_live_forwardable():\n",
        tests,
        "insert tests",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()

    root = Path(args.project).resolve()
    paths = {
        "research": root / "src/predict_bot/research_forward.py",
        "server": root / "src/predict_bot/server.py",
        "tests": root / "tests/test_research_forward.py",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        print("Missing required files:", *missing, sep="\n  ", file=sys.stderr)
        return 2

    originals = {
        key: path.read_text(encoding="utf-8")
        for key, path in paths.items()
    }
    try:
        updated = {
            "research": patch_research_forward(originals["research"]),
            "server": patch_server(originals["server"]),
            "tests": patch_tests(originals["tests"]),
        }
        for key, source in updated.items():
            ast.parse(source, filename=str(paths[key]))
    except Exception as exc:
        print(f"Patch preparation failed: {exc}", file=sys.stderr)
        return 3

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / f"diagnostics-backup-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in paths.values():
        destination = backup_dir / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    try:
        for key, path in paths.items():
            path.write_text(updated[key], encoding="utf-8", newline="\n")

        commands = [
            [
                sys.executable,
                "-m",
                "py_compile",
                str(paths["research"]),
                str(paths["server"]),
                str(paths["tests"]),
            ],
            ["git", "diff", "--check"],
        ]
        if args.run_tests:
            commands.append(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_research_forward.py",
                    "-q",
                ]
            )
        for command in commands:
            result = subprocess.run(command, cwd=root)
            if result.returncode != 0:
                raise RuntimeError(
                    f"validation failed: {' '.join(command)}"
                )
    except Exception as exc:
        for path in paths.values():
            backup = backup_dir / path.relative_to(root)
            shutil.copy2(backup, path)
        print(
            f"Validation failed; originals restored: {exc}",
            file=sys.stderr,
        )
        print(f"Backup kept at: {backup_dir}", file=sys.stderr)
        return 4

    print("R_FUTURES_LEAD diagnostics patch applied.")
    print(f"Backup: {backup_dir}")
    print(
        "API: /api/realtime -> "
        "mRealtime.researchDiagnostics.R_FUTURES_LEAD"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
