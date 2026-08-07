from __future__ import annotations

from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "tests" / "test_live_trading.py"
OLD = '''    )\n\n    live.process_signal(signal())\n\n    assert len(client.quote_calls) == 1\n    assert len(client.place_calls) == 1\n    state = live.state()\n    assert "hourlyGuard" not in state\n    assert state["strategyLifecycle"]["advisoryOnly"] is True\n'''
NEW = '''    )\n    # Avoid M01O_F1's independent >=30s entry gate: this test isolates only\n    # removal of the obsolete M0 hourly blocker.\n    force_legacy_m0w_rules_for_test(live)\n\n    live.process_signal(signal())\n\n    assert len(client.quote_calls) == 1\n    assert len(client.place_calls) == 1\n    state = live.state()\n    assert "hourlyGuard" not in state\n    assert state["strategyLifecycle"]["advisoryOnly"] is True\n'''

text = PATH.read_text(encoding="utf-8")
count = text.count(OLD)
if count != 1:
    raise SystemExit(f"expected one generated lifecycle test anchor, found {count}")
PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("Adjusted Lifecycle hourly-removal test to isolate the intended gate.")
