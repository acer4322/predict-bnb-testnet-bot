from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"anchor not found in {path}: {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1. Live allowlist and execution constraints.
live = ROOT / "src/predict_bot/live_trading.py"
replace_once(
    live,
    '''    "R_OFI_EVENT_CUM",\n)\nLIVE_OBSERVER_STRATEGIES = (\n''',
    '''    "R_OFI_EVENT_CUM",\n    "R_STRONG_TREND_GUARD_FUTURES_LEAD",\n    "R_STRONG_TREND_GUARD_CONSENSUS",\n)\nLIVE_OBSERVER_STRATEGIES = (\n''',
)
replace_once(
    live,
    '''    "R_OFI_EVENT_CUM": Decimal("0.05"),\n}\nLIVE_RESEARCH_PRICE_RANGES = {\n''',
    '''    "R_OFI_EVENT_CUM": Decimal("0.05"),\n    "R_STRONG_TREND_GUARD_FUTURES_LEAD": Decimal("0.05"),\n    "R_STRONG_TREND_GUARD_CONSENSUS": Decimal("0.05"),\n}\nLIVE_RESEARCH_PRICE_RANGES = {\n''',
)

# 2. Create a live candidate only from a recorded, allowed Guard decision whose
# corresponding Paper Shadow was actually opened.  The Paper Shadow itself
# remains paper-only; this is a separate queue candidate consumed only when the
# user explicitly selects the Guard strategy in live rules.
strong = ROOT / "src/predict_bot/strong_trend_guard_shadows.py"
replace_once(
    strong,
    '''STRATEGIES = tuple(SOURCE_TO_SHADOW.values())\n\n\ndef _finite(value: Any) -> float | None:\n''',
    '''STRATEGIES = tuple(SOURCE_TO_SHADOW.values())\nLIVE_SOURCE_TO_GUARD = {\n    "R_FUTURES_LEAD": "R_STRONG_TREND_GUARD_FUTURES_LEAD",\n    "R_CONSENSUS": "R_STRONG_TREND_GUARD_CONSENSUS",\n}\nLIVE_GUARD_STRATEGIES = tuple(LIVE_SOURCE_TO_GUARD.values())\n\n\ndef _finite(value: Any) -> float | None:\n''',
)
helper = r'''

def live_guard_candidates_for_opened(
    store: Any,
    opened: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Bridge selected source Paper fills into guarded live candidates.

    This never evaluates the rule a second time.  It trusts only the durable
    strong_trend_guard_decisions row produced synchronously by Store.open_trade,
    and requires the matching Paper Shadow to exist.  A blocked decision or a
    missing decision fails closed and produces no live candidate.
    """
    if not _table_exists(store):
        return []

    derived: list[dict[str, Any]] = []
    for raw_candidate in list(opened or []):
        if not isinstance(raw_candidate, dict):
            continue
        source_strategy = str(raw_candidate.get("strategy") or "").upper()
        guard_strategy = LIVE_SOURCE_TO_GUARD.get(source_strategy)
        if guard_strategy is None:
            continue
        try:
            market_id = int(raw_candidate["market_id"])
            topic_id = int(raw_candidate["topic_id"])
        except (KeyError, TypeError, ValueError):
            continue
        side = str(raw_candidate.get("side") or "").upper()
        if side not in {"UP", "DOWN"}:
            continue

        with store.lock:
            row = store.db.execute(
                """SELECT d.*,
                          shadow.strategy AS actual_shadow_strategy,
                          shadow.market_id AS actual_shadow_market_id,
                          shadow.side AS actual_shadow_side,
                          shadow.entry_price AS actual_shadow_entry_price
                     FROM strong_trend_guard_decisions AS d
                     LEFT JOIN trades AS shadow ON shadow.id=d.shadow_trade_id
                    WHERE d.source_strategy=?
                      AND d.shadow_strategy=?
                      AND d.market_id=?
                      AND d.topic_id=?
                      AND d.side=?
                    ORDER BY d.id DESC
                    LIMIT 1""",
                (
                    source_strategy,
                    guard_strategy,
                    market_id,
                    topic_id,
                    side,
                ),
            ).fetchone()

        if row is None:
            continue
        if str(row["decision"] or "") == "BLOCK_STRONG_OPPOSING_TREND":
            continue
        if not bool(row["shadow_opened"]) or row["shadow_trade_id"] is None:
            continue
        if str(row["actual_shadow_strategy"] or "") != guard_strategy:
            continue
        if int(row["actual_shadow_market_id"] or -1) != market_id:
            continue
        if str(row["actual_shadow_side"] or "").upper() != side:
            continue

        try:
            decision_diagnostics = json.loads(str(row["diagnostics_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            decision_diagnostics = {}

        candidate = dict(raw_candidate)
        candidate.update({
            "strategy": guard_strategy,
            "side": side,
            "entry_price": float(row["source_entry_price"]),
            "paper_only": True,
            "live_orders_affected": False,
            "shadow_only": True,
            "forward_only": True,
            "live_guard_bridge": True,
            "source_strategy": source_strategy,
            "source_trade_id": int(row["source_trade_id"]),
            "shadow_trade_id": int(row["shadow_trade_id"]),
            "strategy_version": VERSION,
            "strong_trend_guard_decision": decision_diagnostics,
            "note": (
                f"{guard_strategy} live candidate from allowed {source_strategy} "
                f"Guard decision #{int(row['id'])}"
            ),
        })
        derived.append(candidate)
    return derived
'''
replace_once(
    strong,
    '''\ndef _normalized_source_pnl(row: Any) -> float | None:\n''',
    helper + '''\n\ndef _normalized_source_pnl(row: Any) -> float | None:\n''',
)

# 3. Expand the list returned by the native paper store before the existing live
# forwardability gate.  The existing MRealtime code still freezes the exact
# decision book and the Live engine still revalidates freshness, depth, price,
# selected strategy, stake and one-attempt policies.
m_realtime = ROOT / "src/predict_bot/m_realtime.py"
replace_once(
    m_realtime,
    '''from .research_forward import (\n''',
    '''from .strong_trend_guard_shadows import live_guard_candidates_for_opened\nfrom .research_forward import (\n''',
)
replace_once(
    m_realtime,
    '''        opened = self.store.maybe_enter_m_series(\n            snapshot,\n            int(market.get("fee_bps") or 0),\n            realtime_context=context,\n        )\n        store_finished_ns = time.monotonic_ns()\n''',
    '''        opened = list(self.store.maybe_enter_m_series(\n            snapshot,\n            int(market.get("fee_bps") or 0),\n            realtime_context=context,\n        ) or [])\n        opened.extend(live_guard_candidates_for_opened(self.store, opened))\n        store_finished_ns = time.monotonic_ns()\n''',
)

# 4. Dashboard typing and labels.  R_FUTURES_LEAD_REGIME_REVERSE_3L already
# existed in both the backend allowlist and UI; keep it and add the two guards.
page = ROOT / "dashboard/app/page.tsx"
replace_once(
    page,
    '''| "R_CALIBRATED_VALUE" | "R_CALIBRATED_VALUE_REVERSE" | "R_CALIBRATED_VALUE_CONTINUOUS_V2" | "R_CONSENSUS" | "R_CONFIRM_ADD_10";''',
    '''| "R_CALIBRATED_VALUE" | "R_CALIBRATED_VALUE_REVERSE" | "R_CALIBRATED_VALUE_CONTINUOUS_V2" | "R_CONSENSUS" | "R_STRONG_TREND_GUARD_FUTURES_LEAD" | "R_STRONG_TREND_GUARD_CONSENSUS" | "R_CONFIRM_ADD_10";''',
)
replace_once(
    page,
    '''  R_OFI_EVENT_CUM: "研究實單 · 累積事件級 OFI",\n};\n''',
    '''  R_OFI_EVENT_CUM: "研究實單 · 累積事件級 OFI",\n  R_STRONG_TREND_GUARD_FUTURES_LEAD: "研究實單 · Futures Lead 逆強趨勢阻擋",\n  R_STRONG_TREND_GUARD_CONSENSUS: "研究實單 · Consensus 逆強趨勢阻擋",\n};\n''',
)

# 5. Backend regression tests.
strong_tests = ROOT / "tests/test_strong_trend_guard_shadows.py"
replace_once(
    strong_tests,
    '''    evaluate_strong_trend,\n    _experiment_state,\n)\n''',
    '''    evaluate_strong_trend,\n    live_guard_candidates_for_opened,\n    _experiment_state,\n)\n''',
)
strong_test_append = r'''


def test_live_guard_bridge_emits_only_recorded_allowed_shadow(tmp_path):
    store = server.Store(tmp_path / "simulation.db")
    insert_observations(store, 301, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="R_FUTURES_LEAD",
        topic_id=1,
        market_id=301,
        side="DOWN",
        entry=.40,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="allowed source",
    )
    candidates = live_guard_candidates_for_opened(store, [{
        "strategy": "R_FUTURES_LEAD",
        "topic_id": 1,
        "market_id": 301,
        "side": "DOWN",
        "entry_price": .40,
        "paper_only": True,
    }])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["strategy"] == "R_STRONG_TREND_GUARD_FUTURES_LEAD"
    assert candidate["side"] == "DOWN"
    assert candidate["entry_price"] == .40
    assert candidate["paper_only"] is True
    assert candidate["live_orders_affected"] is False
    assert candidate["live_guard_bridge"] is True
    assert candidate["shadow_trade_id"] > 0


def test_live_guard_bridge_fails_closed_for_blocked_source(tmp_path):
    store = server.Store(tmp_path / "simulation.db")
    insert_observations(store, 302, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="R_CONSENSUS",
        topic_id=1,
        market_id=302,
        side="UP",
        entry=.40,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="blocked source",
    )
    candidates = live_guard_candidates_for_opened(store, [{
        "strategy": "R_CONSENSUS",
        "topic_id": 1,
        "market_id": 302,
        "side": "UP",
        "entry_price": .40,
        "paper_only": True,
    }])
    assert candidates == []
'''
text = strong_tests.read_text(encoding="utf-8")
if "test_live_guard_bridge_emits_only_recorded_allowed_shadow" not in text:
    strong_tests.write_text(text + strong_test_append, encoding="utf-8")

live_tests = ROOT / "tests/test_live_trading.py"
text = live_tests.read_text(encoding="utf-8")
append = r'''


def test_requested_strong_trend_and_regime_reverse_strategies_are_live_selectable():
    requested = {
        "R_FUTURES_LEAD_REGIME_REVERSE_3L",
        "R_STRONG_TREND_GUARD_FUTURES_LEAD",
        "R_STRONG_TREND_GUARD_CONSENSUS",
    }
    assert requested <= set(LIVE_SUPPORTED_STRATEGIES)
    assert requested <= set(LIVE_RESEARCH_STRATEGIES)
    assert LIVE_SUPPORTED_STRATEGIES.count("R_FUTURES_LEAD_REGIME_REVERSE_3L") == 1
'''
if "test_requested_strong_trend_and_regime_reverse_strategies_are_live_selectable" not in text:
    live_tests.write_text(text + append, encoding="utf-8")

node_test = ROOT / "dashboard/tests/live-strong-trend-strategies.test.mjs"
node_test.write_text(r'''import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const page = fs.readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");

test("requested live strategies have explicit dashboard labels", () => {
  assert.match(page, /R_FUTURES_LEAD_REGIME_REVERSE_3L:\s*"研究實單 · Lead 連敗三筆正反切換"/);
  assert.match(page, /R_STRONG_TREND_GUARD_FUTURES_LEAD:\s*"研究實單 · Futures Lead 逆強趨勢阻擋"/);
  assert.match(page, /R_STRONG_TREND_GUARD_CONSENSUS:\s*"研究實單 · Consensus 逆強趨勢阻擋"/);
});
''', encoding="utf-8")

print("live strong trend strategy patch applied")
