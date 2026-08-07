from __future__ import annotations

import re
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "tests" / "test_live_trading.py"
text = PATH.read_text(encoding="utf-8")

patterns = (
    (
        r'(?m)^(\s*)assert\s+.*hourlyGuard.*minWinRatePct.*$',
        r'\1assert state["strategyLifecycle"]["advisoryOnly"] is True',
        "hourly min-win assertion",
    ),
    (
        r'(?m)^(\s*)assert\s+.*hourlyGuard.*maxWinThenLossRatePct.*$',
        r'\1assert state["strategyLifecycle"]["automaticBlocking"] is False',
        "hourly win-loss assertion",
    ),
)
for pattern, replacement, label in patterns:
    text, count = re.subn(pattern, replacement, text)
    if count != 2:
        raise SystemExit(f"expected 2 {label} lines, found {count}")

old_name = "def test_runtime_rule_updates_refresh_cached_hourly_thresholds(tmp_path: Path):"
new_name = "def test_runtime_rule_updates_do_not_reactivate_obsolete_hourly_gate(tmp_path: Path):"
count = text.count(old_name)
if count != 1:
    raise SystemExit(f"expected one obsolete runtime threshold test name, found {count}")
text = text.replace(old_name, new_name, 1)

PATH.write_text(text, encoding="utf-8")
print("Replaced only the four obsolete hourly state assertions.")
