from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected exactly one occurrence, found {count}: {old[:160]!r}"
        )
    write(path, content.replace(old, new, 1))


MODULE = r'''from __future__ import annotations

import json
import math
from functools import wraps
from typing import Any, Iterable

from . import microprice_variants as _variants


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION"
PATCH_VERSION = "MICROPRICE_CONFIRM_STABLE_DIRECTION_V1"
MIN_RAW_TOP_ASK = 0.60
MAX_RAW_TOP_ASK_EXCLUSIVE = 0.90
MIN_MIDPOINT_DELTA = 0.01


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def stable_direction_signal_decision(
    raw_top_ask: Any,
    midpoint_delta: Any,
) -> dict[str, Any]:
    raw = _finite(raw_top_ask)
    delta = _finite(midpoint_delta)
    price_passed = raw is not None and MIN_RAW_TOP_ASK <= raw < MAX_RAW_TOP_ASK_EXCLUSIVE
    midpoint_passed = delta is not None and delta >= MIN_MIDPOINT_DELTA
    allowed = bool(price_passed and midpoint_passed)
    reasons: list[str] = []
    if not price_passed:
        reasons.append(
            "raw_top_ask must be inside "
            f"[{MIN_RAW_TOP_ASK:.2f}, {MAX_RAW_TOP_ASK_EXCLUSIVE:.2f})"
        )
    if not midpoint_passed:
        reasons.append(
            f"selected-side midpoint_delta must be >= {MIN_MIDPOINT_DELTA:.3f}"
        )
    return {
        "version": PATCH_VERSION,
        "strategy": STRATEGY,
        "sourceStrategy": SOURCE_STRATEGY,
        "allowed": allowed,
        "status": "ALLOW" if allowed else "BLOCK",
        "reason": (
            "allowed: confirmed Microprice direction, 0.60-0.90 raw Ask, "
            "and selected-side midpoint advanced at least 0.01"
            if allowed
            else "; ".join(reasons)
        ),
        "rawTopAsk": raw,
        "midpointDelta": delta,
        "priceBandPassed": price_passed,
        "midpointAdvancePassed": midpoint_passed,
        "usesObserver": False,
        "usesF1RangeScore": False,
        "paperOnly": True,
    }


def _trade_exists(store: Any, market_id: int) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (STRATEGY, int(market_id)),
        ).fetchone() is not None
    except Exception:
        return False


def _source_context(store: Any, market_id: int) -> dict[str, Any]:
    try:
        row = store.db.execute(
            """SELECT id, strategy_version, diagnostics_json
                 FROM trades
                WHERE strategy=? AND market_id=?
                ORDER BY id DESC LIMIT 1""",
            (SOURCE_STRATEGY, int(market_id)),
        ).fetchone()
    except Exception:
        row = None
    if row is None:
        return {"sourceTradeId": None, "sourceStrategyVersion": None, "diagnostics": {}}
    return {
        "sourceTradeId": int(row["id"]),
        "sourceStrategyVersion": row["strategy_version"],
        "diagnostics": _decode_json(row["diagnostics_json"]),
    }


def open_stable_direction_shadow(
    store: Any,
    source: dict[str, Any],
    fee_bps: int,
) -> dict[str, Any] | None:
    if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
        return None
    try:
        market_id = int(source["market_id"])
        topic_id = int(source["topic_id"])
        side = str(source["side"]).upper()
        entry = float(source["entry_price"])
        stake = float(source["stake"])
    except (KeyError, TypeError, ValueError):
        return None
    if side not in {"UP", "DOWN"} or _trade_exists(store, market_id):
        return None

    source_context = _source_context(store, market_id)
    diagnostics = source_context["diagnostics"]
    raw_top_ask = source.get("raw_top_ask")
    if raw_top_ask is None:
        raw_top_ask = diagnostics.get("raw_top_ask")
    midpoint_delta = source.get("midpoint_delta")
    if midpoint_delta is None:
        midpoint_delta = diagnostics.get("midpoint_delta")
    decision = stable_direction_signal_decision(raw_top_ask, midpoint_delta)
    if decision["allowed"] is not True:
        return None

    shadow_diagnostics = {
        "paper_only": True,
        "live_orders_affected": False,
        "shadow_only": True,
        "forward_only": True,
        "source_strategy": SOURCE_STRATEGY,
        "source_trade_id": source_context["sourceTradeId"],
        "source_strategy_version": source_context["sourceStrategyVersion"],
        "source_already_microprice_confirmed": True,
        "variant_mode": "FOLLOW_CONFIRMED_IMBALANCE_STABLE_DIRECTION",
        "selected_side": side,
        "raw_top_ask": decision["rawTopAsk"],
        "midpoint_delta": decision["midpointDelta"],
        "stable_direction_decision": decision,
        "rule": {
            "minimumRawTopAskInclusive": MIN_RAW_TOP_ASK,
            "maximumRawTopAskExclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "minimumSelectedMidpointDeltaInclusive": MIN_MIDPOINT_DELTA,
            "sourceMustBeMicropriceConfirm": True,
            "observerRequired": False,
            "f1RangeScoreRequired": False,
            "missingDataFailsClosed": True,
        },
        "source_diagnostics": diagnostics,
    }
    store.open_trade(
        strategy=STRATEGY,
        topic_id=topic_id,
        market_id=market_id,
        side=side,
        entry=entry,
        target=None,
        stake=stake,
        fee_rate_bps=int(fee_bps),
        note=(
            f"{STRATEGY} mirrored from {SOURCE_STRATEGY} trade "
            f"#{source_context['sourceTradeId'] or '?'}; raw Ask 0.60-0.90 "
            "and selected-side midpoint_delta >=0.01; paper only"
        ),
        strategy_version=PATCH_VERSION,
        diagnostics=shadow_diagnostics,
    )
    return {
        **source,
        "strategy": STRATEGY,
        "paper_only": True,
        "shadow_only": True,
        "live_orders_affected": False,
        "live_forwardable_when_selected": False,
        "stable_direction_decision": decision,
        "strategy_version": PATCH_VERSION,
        "source_strategy": SOURCE_STRATEGY,
        "source_trade_id": source_context["sourceTradeId"],
    }


def _patch_tracker_process() -> None:
    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_stable_direction_shadow_v1", False):
        return

    @wraps(original)
    def process_with_stable_direction(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        opened = list(original(self, snapshot, int(fee_bps), context) or [])
        result = list(opened)
        for source in opened:
            shadow = open_stable_direction_shadow(self.store, source, int(fee_bps))
            if shadow is not None:
                result.append(shadow)
        return result

    process_with_stable_direction._stable_direction_shadow_v1 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_stable_direction


def _register_research_strategy() -> None:
    from . import research_forward as research

    research.SHADOW_RESEARCH_STRATEGIES = _append_unique(
        research.SHADOW_RESEARCH_STRATEGIES,
        STRATEGY,
    )
    research.RESEARCH_STRATEGIES = (
        *research.PRIMARY_RESEARCH_STRATEGIES,
        *research.SHADOW_RESEARCH_STRATEGIES,
    )
    research.RESEARCH_PARAMETERS = {
        **research.RESEARCH_PARAMETERS,
        STRATEGY: {
            "min_raw_top_ask": MIN_RAW_TOP_ASK,
            "max_raw_top_ask_exclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "min_midpoint_delta": MIN_MIDPOINT_DELTA,
        },
    }


def install_microprice_confirm_stable_direction_shadow() -> None:
    """Register one native research-page Paper Shadow; never register live use."""
    _register_research_strategy()
    _patch_tracker_process()
'''
write("src/predict_bot/microprice_confirm_stable_direction_shadow.py", MODULE)

TESTS = r'''from __future__ import annotations

import json
import sqlite3

from predict_bot import live_trading, research_forward
from predict_bot.microprice_confirm_stable_direction_shadow import (
    MAX_RAW_TOP_ASK_EXCLUSIVE,
    MIN_MIDPOINT_DELTA,
    MIN_RAW_TOP_ASK,
    STRATEGY,
    open_stable_direction_shadow,
    stable_direction_signal_decision,
)


class FakeStore:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE trades(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   strategy TEXT NOT NULL,
                   market_id INTEGER NOT NULL,
                   strategy_version TEXT,
                   diagnostics_json TEXT
               )"""
        )
        self.opened: list[dict] = []

    def open_trade(self, **values):
        self.opened.append(values)
        self.db.execute(
            "INSERT INTO trades(strategy, market_id, strategy_version, diagnostics_json) VALUES (?,?,?,?)",
            (
                values["strategy"],
                values["market_id"],
                values.get("strategy_version"),
                json.dumps(values.get("diagnostics") or {}),
            ),
        )
        self.db.commit()


def source(store: FakeStore, *, ask: float, delta: float) -> dict:
    diagnostics = {
        "raw_top_ask": ask,
        "midpoint_delta": delta,
        "confirmation_count": 3,
        "samples": [{"side": "UP"}, {"side": "UP"}, {"side": "UP"}],
    }
    store.db.execute(
        "INSERT INTO trades(strategy, market_id, strategy_version, diagnostics_json) VALUES (?,?,?,?)",
        ("R_MICROPRICE_CONFIRM", 7001, "SOURCE_V1", json.dumps(diagnostics)),
    )
    store.db.commit()
    return {
        "strategy": "R_MICROPRICE_CONFIRM",
        "topic_id": 11,
        "market_id": 7001,
        "side": "UP",
        "entry_price": ask * 1.005,
        "raw_top_ask": ask,
        "stake": 5.0,
    }


def test_decision_requires_price_band_and_midpoint_advance() -> None:
    assert stable_direction_signal_decision(MIN_RAW_TOP_ASK, MIN_MIDPOINT_DELTA)["allowed"] is True
    assert stable_direction_signal_decision(MAX_RAW_TOP_ASK_EXCLUSIVE - 1e-8, 0.02)["allowed"] is True
    assert stable_direction_signal_decision(MIN_RAW_TOP_ASK - 1e-8, 0.02)["allowed"] is False
    assert stable_direction_signal_decision(MAX_RAW_TOP_ASK_EXCLUSIVE, 0.02)["allowed"] is False
    assert stable_direction_signal_decision(0.70, MIN_MIDPOINT_DELTA - 1e-8)["allowed"] is False
    assert stable_direction_signal_decision(None, 0.02)["allowed"] is False


def test_shadow_opens_from_confirm_source_without_observer() -> None:
    store = FakeStore()
    opened = open_stable_direction_shadow(store, source(store, ask=0.70, delta=0.015), 200)
    assert opened is not None
    assert opened["strategy"] == STRATEGY
    assert opened["paper_only"] is True
    assert opened["live_forwardable_when_selected"] is False
    assert len(store.opened) == 1
    diagnostics = store.opened[0]["diagnostics"]
    assert diagnostics["rule"]["observerRequired"] is False
    assert diagnostics["rule"]["f1RangeScoreRequired"] is False


def test_shadow_fails_closed_when_midpoint_delta_is_missing() -> None:
    store = FakeStore()
    item = source(store, ask=0.70, delta=0.005)
    assert open_stable_direction_shadow(store, item, 200) is None
    assert store.opened == []


def test_strategy_is_native_research_shadow_and_never_live_selectable() -> None:
    assert STRATEGY in research_forward.SHADOW_RESEARCH_STRATEGIES
    assert STRATEGY in research_forward.RESEARCH_STRATEGIES
    assert STRATEGY not in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert STRATEGY not in live_trading.LIVE_RESEARCH_STRATEGIES
'''
write("tests/test_microprice_confirm_stable_direction_shadow.py", TESTS)

DASHBOARD_TEST = r'''import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pageUrl = new URL("../app/page.tsx", import.meta.url);
const layoutUrl = new URL("../app/layout.tsx", import.meta.url);

test("stable direction strategy is rendered by the native research card list", async () => {
  const page = await readFile(pageUrl, "utf8");
  assert.match(page, /R_MICROPRICE_CONFIRM_STABLE_DIRECTION/);
  assert.match(page, /Microprice Confirm · 穩定方向共識/);
  assert.match(page, /raw Ask 0\.60–<0\.90/);
  assert.match(page, /midpoint delta ≥0\.01/);
});

test("stable direction strategy is not mounted as a layout sidecar", async () => {
  const layout = await readFile(layoutUrl, "utf8");
  assert.doesNotMatch(layout, /StableDirection/);
  assert.doesNotMatch(layout, /STABLE_DIRECTION/);
});
'''
write("dashboard/tests/stable-direction-shadow.test.mjs", DASHBOARD_TEST)

replace_once(
    "src/predict_bot/__init__.py",
    "from .microprice_confirm_stable_consensus_guard import (\n    install_microprice_confirm_stable_consensus_guard as _install_microprice_confirm_stable_consensus_guard,\n)\n",
    "from .microprice_confirm_stable_consensus_guard import (\n    install_microprice_confirm_stable_consensus_guard as _install_microprice_confirm_stable_consensus_guard,\n)\nfrom .microprice_confirm_stable_direction_shadow import (\n    install_microprice_confirm_stable_direction_shadow as _install_microprice_confirm_stable_direction_shadow,\n)\n",
)
replace_once(
    "src/predict_bot/__init__.py",
    "_install_microprice_confirm_stable_consensus_guard()\n_install_microprice_confirm_stable_consensus_dashboard_patch()\n",
    "_install_microprice_confirm_stable_consensus_guard()\n_install_microprice_confirm_stable_direction_shadow()\n_install_microprice_confirm_stable_consensus_dashboard_patch()\n",
)
replace_once(
    "src/predict_bot/__init__.py",
    "del _install_microprice_confirm_stable_consensus_guard\n",
    "del _install_microprice_confirm_stable_consensus_guard\ndel _install_microprice_confirm_stable_direction_shadow\n",
)

replace_once(
    "src/predict_bot/server.py",
    '    "strategy_r_microprice_stake": 5.0,\n',
    '    "strategy_r_microprice_stake": 5.0,\n    "strategy_r_microprice_confirm_stable_direction_enabled": True,\n    "strategy_r_microprice_confirm_stable_direction_stake": 5.0,\n',
)

replace_once(
    "dashboard/app/page.tsx",
    '  | "R_MICROPRICE" | "R_MICROPRICE_REVERSE" |',
    '  | "R_MICROPRICE" | "R_MICROPRICE_CONFIRM_STABLE_DIRECTION" | "R_MICROPRICE_REVERSE" |',
)
replace_once(
    "dashboard/app/page.tsx",
    '    R_MICROPRICE: { ...EMPTY_SUMMARY }, R_OFI: { ...EMPTY_SUMMARY },\n',
    '    R_MICROPRICE: { ...EMPTY_SUMMARY }, R_MICROPRICE_CONFIRM_STABLE_DIRECTION: { ...EMPTY_SUMMARY }, R_OFI: { ...EMPTY_SUMMARY },\n',
)
replace_once(
    "dashboard/app/page.tsx",
    '  { id: "R_MICROPRICE", title: "Microprice 深度失衡", rule: "剩餘 180 秒，以 UP／DOWN 第一檔數量失衡差決定方向。", tone: "cyan" },\n',
    '  { id: "R_MICROPRICE", title: "Microprice 深度失衡", rule: "剩餘 180 秒，以 UP／DOWN 第一檔數量失衡差決定方向。", tone: "cyan" },\n  { id: "R_MICROPRICE_CONFIRM_STABLE_DIRECTION", title: "Microprice Confirm · 穩定方向共識", rule: "依賴式 Shadow：只有 R_MICROPRICE_CONFIRM 已完成同方向多事件確認後才評估；訊號側 raw Ask 必須為 0.60–<0.90，且確認期間 selected-side midpoint delta ≥0.01。完全不使用 F1、震盪分或 Observer；paper only，不可轉送實單。", tone: "green", shadow: true },\n',
)

print("stable direction shadow patch applied")
