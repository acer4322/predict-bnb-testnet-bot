from __future__ import annotations

from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "tests" / "test_live_trading.py"

text = PATH.read_text(encoding="utf-8")
replacements = (
    (
        '    assert state["hourlyGuard"]["minWinRatePct"] == 47.5\n',
        '    assert state["strategyLifecycle"]["advisoryOnly"] is True\n',
        2,
        "hourly min-win assertion",
    ),
    (
        '    assert state["hourlyGuard"]["maxWinThenLossRatePct"] == 61.0\n',
        '    assert state["strategyLifecycle"]["automaticBlocking"] is False\n',
        2,
        "hourly win-loss assertion",
    ),
)
for old, new, expected, label in replacements:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"expected {expected} {label} lines, found {count}")
    text = text.replace(old, new, expected)

old_name = "def test_runtime_rule_updates_refresh_cached_hourly_thresholds(tmp_path: Path):"
new_name = "def test_runtime_rule_updates_do_not_reactivate_obsolete_hourly_gate(tmp_path: Path):"
count = text.count(old_name)
if count != 1:
    raise SystemExit(f"expected one obsolete runtime threshold test name, found {count}")
text = text.replace(old_name, new_name, 1)

PATH.write_text(text, encoding="utf-8")
print("Replaced only the four obsolete hourly state assertions.")
