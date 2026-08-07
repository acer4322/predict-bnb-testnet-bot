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
    updated, count = re.subn(pattern, replacement, text)
    if count != 2:
        print(f"DIAGNOSTIC: expected 2 {label} lines, found {count}")
        positions = [match.start() for match in re.finditer("hourlyGuard", text)]
        print(f"DIAGNOSTIC: hourlyGuard occurrences={len(positions)}")
        for index, position in enumerate(positions, start=1):
            start = max(0, position - 180)
            end = min(len(text), position + 260)
            print(f"--- hourlyGuard context {index} ---")
            print(text[start:end])
        raise SystemExit(1)
    text = updated

old_name = "def test_runtime_rule_updates_refresh_cached_hourly_thresholds(tmp_path: Path):"
new_name = "def test_runtime_rule_updates_do_not_reactivate_obsolete_hourly_gate(tmp_path: Path):"
count = text.count(old_name)
if count != 1:
    raise SystemExit(f"expected one obsolete runtime threshold test name, found {count}")
text = text.replace(old_name, new_name, 1)

PATH.write_text(text, encoding="utf-8")
print("Replaced only the four obsolete hourly state assertions.")
