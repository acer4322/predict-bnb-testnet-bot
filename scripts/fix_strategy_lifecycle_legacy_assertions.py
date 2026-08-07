from __future__ import annotations

from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "tests" / "test_live_trading.py"
text = PATH.read_text(encoding="utf-8")

old_block = '''    guard = live.state()["hourlyGuard"]\n    assert guard["status"] == "ALLOW"\n    assert guard["minWinRatePct"] == pytest.approx(49.0)\n    assert guard["maxWinThenLossRatePct"] == pytest.approx(60.0)\n'''
new_block = '''    state = live.state()\n    assert "hourlyGuard" not in state\n    assert state["strategyLifecycle"]["advisoryOnly"] is True\n    assert state["strategyLifecycle"]["automaticBlocking"] is False\n'''
count = text.count(old_block)
if count != 1:
    raise SystemExit(f"expected one final obsolete hourly guard block, found {count}")
text = text.replace(old_block, new_block, 1)

old_name = "def test_runtime_rule_updates_refresh_cached_hourly_thresholds(tmp_path: Path):"
new_name = "def test_runtime_rule_updates_do_not_reactivate_obsolete_hourly_gate(tmp_path: Path):"
count = text.count(old_name)
if count == 1:
    text = text.replace(old_name, new_name, 1)
elif count != 0:
    raise SystemExit(f"unexpected obsolete runtime threshold test name count: {count}")

PATH.write_text(text, encoding="utf-8")
print("Replaced the final obsolete hourly guard state block only.")
