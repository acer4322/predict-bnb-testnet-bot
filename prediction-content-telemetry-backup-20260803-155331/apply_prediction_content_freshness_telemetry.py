from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".html"}


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one anchor, found {count}"
        )
    return text.replace(old, new, 1)


def insert_before_once(
    text: str,
    anchor: str,
    insertion: str,
    label: str,
) -> str:
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one anchor, found {count}"
        )
    return text.replace(anchor, insertion + anchor, 1)


def patch_microstructure(text: str) -> str:
    marker = '"contentFreshnessClassification"'
    if marker in text:
        raise RuntimeError(
            "Prediction content-freshness telemetry already appears installed"
        )
    if "predictionSupervisorThreadAlive" not in text:
        raise RuntimeError(
            "Prediction rollover supervisor v4 is not installed. "
            "Apply and validate v4 before this telemetry patch."
        )

    text = insert_before_once(
        text,
        "PREDICTION_MAX_VERSION_AGE_MS = max(\n",
        "\n".join(
            [
                "PREDICTION_CONTENT_WARNING_AGE_MS = max(",
                "    250.0,",
                "    float(",
                "        os.environ.get(",
                '            "PREDICT_PREDICTION_CONTENT_WARNING_AGE_MS",',
                '            "2000",',
                "        )",
                "    ),",
                ")",
                "",
            ]
        ),
        "insert Prediction content warning threshold",
    )

    text = replace_once(
        text,
        "\n".join(
            [
                "        self.last_prediction_receipt_monotonic_ns = 0",
                "        self.last_prediction_book_version_ms: int | None = None",
                "        self.stale_prediction_events = 0",
            ]
        )
        + "\n",
        "\n".join(
            [
                "        self.last_prediction_receipt_monotonic_ns = 0",
                "        self.last_prediction_book_version_ms: int | None = None",
                "        # Transport receipt freshness and exchange content freshness",
                "        # are deliberately tracked separately. Repeated WSS frames",
                "        # can be locally fresh while carrying an unchanged book version.",
                "        self.last_prediction_frame_receipt_monotonic_ns = 0",
                "        self.last_prediction_unique_version_ms: int | None = None",
                "        self.last_prediction_unique_version_receipt_monotonic_ns = 0",
                "        self.last_prediction_unique_version_at: str | None = None",
                "        self.last_prediction_top_of_book_signature: tuple[Any, ...] | None = None",
                "        self.last_prediction_top_of_book_change_monotonic_ns = 0",
                "        self.last_prediction_top_of_book_change_at: str | None = None",
                "        self.prediction_content_frames = 0",
                "        self.prediction_unique_version_events = 0",
                "        self.prediction_same_version_events = 0",
                "        self.prediction_same_version_consecutive_events = 0",
                "        self.prediction_top_of_book_change_events = 0",
                "        self.prediction_top_of_book_unchanged_events = 0",
                "        self.prediction_top_of_book_unchanged_consecutive_events = 0",
                "        self.stale_prediction_events = 0",
            ]
        )
        + "\n",
        "insert Prediction content telemetry state",
    )

    reset_old = "\n".join(
        [
            "        self.last_prediction_receipt_monotonic_ns = 0",
            "        self.last_prediction_book_version_ms = None",
            "        if market_id is not None:",
        ]
    )
    reset_new = "\n".join(
        [
            "        self.last_prediction_receipt_monotonic_ns = 0",
            "        self.last_prediction_book_version_ms = None",
            "        self.last_prediction_frame_receipt_monotonic_ns = 0",
            "        self.last_prediction_unique_version_ms = None",
            "        self.last_prediction_unique_version_receipt_monotonic_ns = 0",
            "        self.last_prediction_unique_version_at = None",
            "        self.last_prediction_top_of_book_signature = None",
            "        self.last_prediction_top_of_book_change_monotonic_ns = 0",
            "        self.last_prediction_top_of_book_change_at = None",
            "        self.prediction_content_frames = 0",
            "        self.prediction_unique_version_events = 0",
            "        self.prediction_same_version_events = 0",
            "        self.prediction_same_version_consecutive_events = 0",
            "        self.prediction_top_of_book_change_events = 0",
            "        self.prediction_top_of_book_unchanged_events = 0",
            "        self.prediction_top_of_book_unchanged_consecutive_events = 0",
            "        if market_id is not None:",
        ]
    )
    text = replace_once(
        text,
        reset_old,
        reset_new,
        "reset Prediction content telemetry on rollover",
    )

    helper_lines = [
        "    def _observe_prediction_content(",
        "        self,",
        "        event: dict[str, Any],",
        "        version_ms: int | None,",
        "    ) -> None:",
        "        # Telemetry only: this method must never make a book eligible.",
        "        received_mono_ns = int(event.get(\"received_monotonic_ns\") or 0)",
        "        received_wall_ns = int(event.get(\"received_wall_ns\") or 0)",
        "        if received_mono_ns <= 0:",
        "            return",
        "        normalized_version = (",
        "            int(version_ms) if version_ms is not None else None",
        "        )",
        "        top_signature = (",
        "            event.get(\"best_bid\"),",
        "            event.get(\"best_bid_qty\"),",
        "            event.get(\"best_ask\"),",
        "            event.get(\"best_ask_qty\"),",
        "        )",
        "        with self.state_lock:",
        "            self.last_prediction_frame_receipt_monotonic_ns = received_mono_ns",
        "            self.prediction_content_frames += 1",
        "",
        "            if normalized_version is not None:",
        "                if normalized_version != self.last_prediction_unique_version_ms:",
        "                    self.last_prediction_unique_version_ms = normalized_version",
        "                    self.last_prediction_unique_version_receipt_monotonic_ns = (",
        "                        received_mono_ns",
        "                    )",
        "                    self.last_prediction_unique_version_at = (",
        "                        _utc_iso_from_ns(received_wall_ns)",
        "                        if received_wall_ns > 0",
        "                        else None",
        "                    )",
        "                    self.prediction_unique_version_events += 1",
        "                    self.prediction_same_version_consecutive_events = 0",
        "                else:",
        "                    self.prediction_same_version_events += 1",
        "                    self.prediction_same_version_consecutive_events += 1",
        "",
        "            if top_signature != self.last_prediction_top_of_book_signature:",
        "                self.last_prediction_top_of_book_signature = top_signature",
        "                self.last_prediction_top_of_book_change_monotonic_ns = (",
        "                    received_mono_ns",
        "                )",
        "                self.last_prediction_top_of_book_change_at = (",
        "                    _utc_iso_from_ns(received_wall_ns)",
        "                    if received_wall_ns > 0",
        "                    else None",
        "                )",
        "                self.prediction_top_of_book_change_events += 1",
        "                self.prediction_top_of_book_unchanged_consecutive_events = 0",
        "            else:",
        "                self.prediction_top_of_book_unchanged_events += 1",
        "                self.prediction_top_of_book_unchanged_consecutive_events += 1",
        "",
    ]
    helper = "\n".join(helper_lines)
    text = insert_before_once(
        text,
        "    def _set_stream(self, name: str, **values: Any) -> None:\n",
        helper,
        "insert Prediction content observer helper",
    )

    accept_old = "\n".join(
        [
            '            timestamp = event.get("prediction_book_version_ms")',
            "            if timestamp is None:",
            '                timestamp = event.get("exchange_event_ms")',
            "            previous = self.last_prediction_timestamp.get(int(market_id))",
        ]
    )
    accept_new = "\n".join(
        [
            '            timestamp = event.get("prediction_book_version_ms")',
            "            if timestamp is None:",
            '                timestamp = event.get("exchange_event_ms")',
            "            self._observe_prediction_content(",
            "                event,",
            "                int(timestamp) if timestamp is not None else None,",
            "            )",
            "            previous = self.last_prediction_timestamp.get(int(market_id))",
        ]
    )
    text = replace_once(
        text,
        accept_old,
        accept_new,
        "observe every matching Prediction frame before duplicate filtering",
    )

    health_anchor = "        version_healthy = bool(\n"
    health_logic = "\n".join(
        [
            "        transport_receipt_age_ms = (",
            "            max(",
            "                0.0,",
            "                (",
            "                    now_mono_ns",
            "                    - self.last_prediction_frame_receipt_monotonic_ns",
            "                )",
            "                / 1_000_000,",
            "            )",
            "            if self.last_prediction_frame_receipt_monotonic_ns",
            "            else None",
            "        )",
            "        unique_version_receipt_age_ms = (",
            "            max(",
            "                0.0,",
            "                (",
            "                    now_mono_ns",
            "                    - self.last_prediction_unique_version_receipt_monotonic_ns",
            "                )",
            "                / 1_000_000,",
            "            )",
            "            if self.last_prediction_unique_version_receipt_monotonic_ns",
            "            else None",
            "        )",
            "        top_of_book_unchanged_age_ms = (",
            "            max(",
            "                0.0,",
            "                (",
            "                    now_mono_ns",
            "                    - self.last_prediction_top_of_book_change_monotonic_ns",
            "                )",
            "                / 1_000_000,",
            "            )",
            "            if self.last_prediction_top_of_book_change_monotonic_ns",
            "            else None",
            "        )",
            "        same_version_receipt_ratio = (",
            "            self.prediction_same_version_events",
            "            / self.prediction_content_frames",
            "            if self.prediction_content_frames",
            "            else None",
            "        )",
            "        if transport_receipt_age_ms is None:",
            '            content_freshness_classification = "NO_CURRENT_MARKET_FRAME"',
            "        elif (",
            "            transport_receipt_age_ms",
            "            > PREDICTION_CONTENT_WARNING_AGE_MS",
            "        ):",
            '            content_freshness_classification = "TRANSPORT_STALE"',
            "        elif book_version_age_ms is None:",
            '            content_freshness_classification = "CONTENT_VERSION_UNAVAILABLE"',
            "        elif (",
            "            book_version_age_ms",
            "            > PREDICTION_CONTENT_WARNING_AGE_MS",
            "        ):",
            "            content_freshness_classification = (",
            '                "CONTENT_VERSION_OLD_TRANSPORT_LIVE"',
            "            )",
            "        else:",
            '            content_freshness_classification = "CONTENT_CURRENT"',
            "",
        ]
    )
    text = insert_before_once(
        text,
        health_anchor,
        health_logic,
        "calculate separate Prediction transport/content ages",
    )

    fields_anchor = (
        '                "bookVersionAgeMs": book_version_age_ms,\n'
    )
    fields_new = fields_anchor + "\n".join(
        [
            '                "contentVersionAgeMs": book_version_age_ms,',
            '                "transportReceiptAgeMs": transport_receipt_age_ms,',
            '                "uniqueVersionReceiptAgeMs": (',
            "                    unique_version_receipt_age_ms",
            "                ),",
            '                "topOfBookUnchangedAgeMs": (',
            "                    top_of_book_unchanged_age_ms",
            "                ),",
            '                "contentWarningAgeMs": (',
            "                    PREDICTION_CONTENT_WARNING_AGE_MS",
            "                ),",
            '                "contentFreshnessClassification": (',
            "                    content_freshness_classification",
            "                ),",
            '                "contentFreshnessHealthy": (',
            "                    content_freshness_classification",
            '                    == "CONTENT_CURRENT"',
            "                ),",
            '                "lastUniqueBookVersionMs": (',
            "                    self.last_prediction_unique_version_ms",
            "                ),",
            '                "lastUniqueBookVersionAt": (',
            "                    self.last_prediction_unique_version_at",
            "                ),",
            '                "lastTopOfBookChangeAt": (',
            "                    self.last_prediction_top_of_book_change_at",
            "                ),",
            '                "contentFrames": self.prediction_content_frames,',
            '                "uniqueVersionEvents": (',
            "                    self.prediction_unique_version_events",
            "                ),",
            '                "sameVersionEvents": (',
            "                    self.prediction_same_version_events",
            "                ),",
            '                "sameVersionConsecutiveEvents": (',
            "                    self.prediction_same_version_consecutive_events",
            "                ),",
            '                "sameVersionReceiptRatio": (',
            "                    same_version_receipt_ratio",
            "                ),",
            '                "topOfBookChangeEvents": (',
            "                    self.prediction_top_of_book_change_events",
            "                ),",
            '                "topOfBookUnchangedEvents": (',
            "                    self.prediction_top_of_book_unchanged_events",
            "                ),",
            '                "topOfBookUnchangedConsecutiveEvents": (',
            "                    self.prediction_top_of_book_unchanged_consecutive_events",
            "                ),",
        ]
    ) + "\n"
    text = replace_once(
        text,
        fields_anchor,
        fields_new,
        "expose Prediction content-freshness telemetry",
    )

    return text


def patch_m_realtime(text: str) -> str:
    if '"up_book_content_age_ms"' in text:
        raise RuntimeError(
            "Per-outcome Prediction content-age telemetry already installed"
        )

    calc_old = "\n".join(
        [
            "        book_skew_ms = abs(up_version - down_version)",
            "        book_age_ms = max(0.0, float(current_timestamp_ms - version_ms))",
            "    except (TypeError, ValueError):",
            "        up_version = down_version = version_ms = None",
            "        book_skew_ms = book_age_ms = None",
        ]
    )
    calc_new = "\n".join(
        [
            "        book_skew_ms = abs(up_version - down_version)",
            "        up_book_content_age_ms = max(",
            "            0.0, float(current_timestamp_ms - up_version)",
            "        )",
            "        down_book_content_age_ms = max(",
            "            0.0, float(current_timestamp_ms - down_version)",
            "        )",
            "        book_age_ms = max(",
            "            up_book_content_age_ms,",
            "            down_book_content_age_ms,",
            "        )",
            "    except (TypeError, ValueError):",
            "        up_version = down_version = version_ms = None",
            "        book_skew_ms = book_age_ms = None",
            "        up_book_content_age_ms = down_book_content_age_ms = None",
        ]
    )
    text = replace_once(
        text,
        calc_old,
        calc_new,
        "calculate direct REST per-outcome content ages",
    )

    event_anchor = '        "book_age_ms": book_age_ms,\n'
    event_new = event_anchor + "\n".join(
        [
            '        "content_version_age_ms": book_age_ms,',
            '        "up_book_content_age_ms": up_book_content_age_ms,',
            '        "down_book_content_age_ms": down_book_content_age_ms,',
            '        "transport_receipt_age_ms": 0.0,',
        ]
    ) + "\n"
    text = replace_once(
        text,
        event_anchor,
        event_new,
        "expose direct REST per-outcome ages",
    )

    values_old = "\n".join(
        [
            '        source_age_ms = _finite(',
            "            event.get(",
            '                "book_age_ms",',
            '                event.get("prediction_book_version_age_ms"),',
            "            )",
            "        )",
            "        book_age_ms = (",
            "            max(local_receipt_age_ms, source_age_ms + local_receipt_age_ms)",
            "            if source_age_ms is not None",
            "            else local_receipt_age_ms",
            "        )",
        ]
    )
    values_new = "\n".join(
        [
            '        source_age_ms = _finite(',
            "            event.get(",
            '                "book_age_ms",',
            '                event.get("prediction_book_version_age_ms"),',
            "            )",
            "        )",
            "        up_content_age_ms = _finite(",
            '            event.get("up_book_content_age_ms")',
            "        )",
            "        down_content_age_ms = _finite(",
            '            event.get("down_book_content_age_ms")',
            "        )",
            "        if up_content_age_ms is None:",
            "            up_content_age_ms = source_age_ms",
            "        if down_content_age_ms is None:",
            "            down_content_age_ms = source_age_ms",
            "        book_age_ms = (",
            "            max(local_receipt_age_ms, source_age_ms + local_receipt_age_ms)",
            "            if source_age_ms is not None",
            "            else local_receipt_age_ms",
            "        )",
        ]
    )
    text = replace_once(
        text,
        values_old,
        values_new,
        "separate Prediction transport and content ages in MRealtime",
    )

    values_return_anchor = '            "book_age_ms": book_age_ms,\n'
    values_return_new = values_return_anchor + "\n".join(
        [
            '            "effective_book_age_ms": book_age_ms,',
            '            "content_version_age_ms": source_age_ms,',
            '            "transport_receipt_age_ms": local_receipt_age_ms,',
            '            "up_book_content_age_ms": up_content_age_ms,',
            '            "down_book_content_age_ms": down_content_age_ms,',
        ]
    ) + "\n"
    text = replace_once(
        text,
        values_return_anchor,
        values_return_new,
        "return separate Prediction ages from MRealtime",
    )

    copy_anchor = '            "book_age_ms": values["book_age_ms"],\n'
    copy_new = copy_anchor + "\n".join(
        [
            '            "effective_book_age_ms": values["effective_book_age_ms"],',
            '            "content_version_age_ms": values["content_version_age_ms"],',
            '            "transport_receipt_age_ms": values["transport_receipt_age_ms"],',
            '            "up_book_content_age_ms": values["up_book_content_age_ms"],',
            '            "down_book_content_age_ms": values["down_book_content_age_ms"],',
        ]
    ) + "\n"
    text = replace_once(
        text,
        copy_anchor,
        copy_new,
        "copy separate Prediction ages for live execution telemetry",
    )

    return text


def patch_warning_copy(text: str) -> tuple[str, int]:
    replacements = (
        (
            "Prediction book content age",
            "Prediction book content version age",
        ),
        (
            "Prediction Ask 已延遲",
            "Prediction Ask 內容版本未更新",
        ),
    )
    changed = 0
    for old, new in replacements:
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            changed += count
    return text, changed


def patch_microstructure_tests(text: str) -> str:
    marker = "test_prediction_content_age_is_distinct_from_transport_receipt_age"
    if marker in text:
        raise RuntimeError("Prediction content telemetry tests already installed")

    tests = "\n".join(
        [
            "",
            "",
            "def test_prediction_content_age_is_distinct_from_transport_receipt_age(",
            "    tmp_path: Path,",
            "):",
            "    observer = MicrostructureObserver(",
            "        api_key=\"key\",",
            "        api_secret=\"secret\",",
            "        current_market_id=lambda: 42,",
            "        db_path=tmp_path / \"content-vs-transport.db\",",
            "    )",
            "    now_wall_ns = time.time_ns()",
            "    now_mono_ns = time.monotonic_ns()",
            "    old_version_ms = int(now_wall_ns / 1_000_000) - 3_500",
            "    event = {",
            "        \"received_wall_ns\": now_wall_ns,",
            "        \"received_monotonic_ns\": now_mono_ns,",
            "        \"best_bid\": 0.49,",
            "        \"best_bid_qty\": 10.0,",
            "        \"best_ask\": 0.50,",
            "        \"best_ask_qty\": 12.0,",
            "    }",
            "    observer.last_prediction_book_version_ms = old_version_ms",
            "    observer._observe_prediction_content(event, old_version_ms)",
            "",
            "    prediction = observer.state()[\"streams\"][\"prediction\"]",
            "",
            "    assert prediction[\"transportReceiptAgeMs\"] < 1_000",
            "    assert prediction[\"contentVersionAgeMs\"] >= 3_000",
            "    assert prediction[\"contentFreshnessClassification\"] == (",
            "        \"CONTENT_VERSION_OLD_TRANSPORT_LIVE\"",
            "    )",
            "    assert prediction[\"contentFreshnessHealthy\"] is False",
            "",
            "",
            "def test_prediction_same_version_and_top_of_book_repeats_are_counted(",
            "    tmp_path: Path,",
            "):",
            "    observer = MicrostructureObserver(",
            "        api_key=\"key\",",
            "        api_secret=\"secret\",",
            "        current_market_id=lambda: 42,",
            "        db_path=tmp_path / \"same-version.db\",",
            "    )",
            "    version_ms = int(time.time() * 1_000)",
            "    event = {",
            "        \"received_wall_ns\": time.time_ns(),",
            "        \"received_monotonic_ns\": time.monotonic_ns(),",
            "        \"best_bid\": 0.49,",
            "        \"best_bid_qty\": 10.0,",
            "        \"best_ask\": 0.50,",
            "        \"best_ask_qty\": 12.0,",
            "    }",
            "    observer._observe_prediction_content(event, version_ms)",
            "    second = dict(event)",
            "    second[\"received_wall_ns\"] = time.time_ns()",
            "    second[\"received_monotonic_ns\"] = time.monotonic_ns()",
            "    observer._observe_prediction_content(second, version_ms)",
            "",
            "    prediction = observer.state()[\"streams\"][\"prediction\"]",
            "",
            "    assert prediction[\"contentFrames\"] == 2",
            "    assert prediction[\"uniqueVersionEvents\"] == 1",
            "    assert prediction[\"sameVersionEvents\"] == 1",
            "    assert prediction[\"sameVersionConsecutiveEvents\"] == 1",
            "    assert prediction[\"topOfBookChangeEvents\"] == 1",
            "    assert prediction[\"topOfBookUnchangedEvents\"] == 1",
            "    assert prediction[\"sameVersionReceiptRatio\"] == pytest.approx(0.5)",
        ]
    )
    return text.rstrip() + tests + "\n"


def patch_m_realtime_tests(text: str) -> str:
    marker = "test_direct_rest_event_exposes_per_outcome_content_ages"
    if marker in text:
        raise RuntimeError("MRealtime content-age tests already installed")

    tests = "\n".join(
        [
            "",
            "",
            "def test_direct_rest_event_exposes_per_outcome_content_ages():",
            "    from predict_bot.m_realtime import direct_rest_prediction_event",
            "",
            "    event = direct_rest_prediction_event(",
            "        market_id=42,",
            "        up_book={",
            "            \"updateTimestampMs\": 9_500,",
            "            \"bids\": [[\"0.49\", \"10\"]],",
            "            \"asks\": [[\"0.50\", \"12\"]],",
            "        },",
            "        down_book={",
            "            \"updateTimestampMs\": 7_000,",
            "            \"bids\": [[\"0.49\", \"11\"]],",
            "            \"asks\": [[\"0.50\", \"13\"]],",
            "        },",
            "        received_wall_ns=10_000_000_000,",
            "        received_monotonic_ns=20_000_000_000,",
            "        current_timestamp_ms=10_000,",
            "    )",
            "",
            "    assert event[\"up_book_content_age_ms\"] == pytest.approx(500)",
            "    assert event[\"down_book_content_age_ms\"] == pytest.approx(3_000)",
            "    assert event[\"content_version_age_ms\"] == pytest.approx(3_000)",
            "    assert event[\"book_age_ms\"] == pytest.approx(3_000)",
            "    assert event[\"feature_eligible\"] is False",
        ]
    )
    return text.rstrip() + tests + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Separate Prediction transport receipt age from exchange content "
            "version age without loosening the 2-second execution gate."
        )
    )
    parser.add_argument("--project", default=".")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()

    root = Path(args.project).resolve()
    microstructure = root / "src/predict_bot/microstructure.py"
    m_realtime = root / "src/predict_bot/m_realtime.py"
    micro_tests = root / "tests/test_microstructure.py"
    realtime_tests = root / "tests/test_m_realtime.py"
    required = [microstructure, m_realtime, micro_tests, realtime_tests]
    missing = [path for path in required if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing required file: {path}", file=sys.stderr)
        return 2

    originals: dict[Path, str] = {
        path: path.read_text(encoding="utf-8") for path in required
    }
    updated: dict[Path, str] = {}

    try:
        updated[microstructure] = patch_microstructure(
            originals[microstructure]
        )
        updated[m_realtime] = patch_m_realtime(originals[m_realtime])
        updated[micro_tests] = patch_microstructure_tests(
            originals[micro_tests]
        )
        updated[realtime_tests] = patch_m_realtime_tests(
            originals[realtime_tests]
        )

        warning_replacements = 0
        patch_script_path = Path(__file__).resolve()
        excluded_parts = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "data",
        }
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or path.suffix.lower() not in SOURCE_EXTENSIONS
                or path in updated
                or path.resolve() == patch_script_path
                or any(part in excluded_parts for part in path.parts)
                or any(
                    part.startswith(
                        (
                            "prediction-rollover-supervisor-backup-",
                            "prediction-content-telemetry-backup-",
                        )
                    )
                    for part in path.parts
                )
            ):
                continue
            original = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
            changed, count = patch_warning_copy(original)
            if count:
                originals[path] = original
                updated[path] = changed
                warning_replacements += count

        for path, content in updated.items():
            if path.suffix == ".py":
                ast.parse(content, filename=str(path))
    except Exception as exc:
        print(f"Patch preparation failed: {exc}", file=sys.stderr)
        return 3

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / f"prediction-content-telemetry-backup-{stamp}"
    for path in updated:
        destination = backup / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    try:
        for path, content in updated.items():
            path.write_text(
                content.rstrip() + "\n",
                encoding="utf-8",
                newline="\n",
            )

        py_files = [str(path) for path in updated if path.suffix == ".py"]
        commands = [
            [sys.executable, "-m", "py_compile", *py_files],
            ["git", "diff", "--check"],
        ]
        if args.run_tests:
            commands.append(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_microstructure.py",
                    "tests/test_m_realtime.py",
                    "-q",
                ]
            )

        for command in commands:
            print("+", " ".join(command))
            result = subprocess.run(command, cwd=root)
            if result.returncode != 0:
                raise RuntimeError(
                    f"command failed ({result.returncode}): "
                    + " ".join(command)
                )
    except Exception as exc:
        for path in updated:
            shutil.copy2(backup / path.relative_to(root), path)
        print(
            "Validation failed; original files restored.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        print(f"Backup kept at: {backup}", file=sys.stderr)
        return 4

    print()
    print("Prediction content-freshness telemetry patch installed.")
    print(f"Backup: {backup}")
    print("Safety gate: unchanged at 2000 ms.")
    print(f"Warning copy replacements: {warning_replacements}")
    print("Changed:")
    for path in sorted(updated):
        print(f"  {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
