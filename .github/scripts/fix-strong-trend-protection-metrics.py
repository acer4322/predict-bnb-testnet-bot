from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


backend = ROOT / "src/predict_bot/strong_trend_guard_shadows.py"
replace_once(
    backend,
    '''            "blockedSettled": 0,\n            "blockedWins": 0,\n            "blockedLosses": 0,\n            "blockedWinRate": None,\n''',
    '''            "blockedSettled": 0,\n            "blockedPending": 0,\n            "blockedFlat": 0,\n            "blockedWins": 0,\n            "blockedLosses": 0,\n            "blockedWinRate": None,\n''',
)
replace_once(
    backend,
    '''        if decision == "BLOCK_STRONG_OPPOSING_TREND":\n            normalized = _normalized_source_pnl(row)\n            source_status = str(row["source_status"] or "")\n            if source_status in {"SETTLED_WIN", "SETTLED_LOSS"} and normalized is not None:\n                stats["blockedSettled"] += 1\n                if normalized > 0:\n                    stats["blockedWins"] += 1\n                    stats["sacrificedProfitUsdt"] += normalized\n                else:\n                    stats["blockedLosses"] += 1\n                    stats["avoidedLossUsdt"] += -normalized\n''',
    '''        if decision == "BLOCK_STRONG_OPPOSING_TREND":\n            source_status = str(row["source_status"] or "").upper()\n            normalized = _normalized_source_pnl(row)\n            # Paper trades remain OPEN until an exit/official settlement writes a\n            # realized PnL.  Do not restrict counterfactual accounting to only\n            # SETTLED_WIN/SETTLED_LOSS: target fills, stop exits and timeout exits\n            # are also finalized source outcomes and must affect protection PnL.\n            source_finalized = source_status not in {"", "OPEN"} and normalized is not None\n            if source_finalized:\n                stats["blockedSettled"] += 1\n                if normalized > 0:\n                    stats["blockedWins"] += 1\n                    stats["sacrificedProfitUsdt"] += normalized\n                elif normalized < 0:\n                    stats["blockedLosses"] += 1\n                    stats["avoidedLossUsdt"] += -normalized\n                else:\n                    stats["blockedFlat"] += 1\n            else:\n                stats["blockedPending"] += 1\n''',
)
replace_once(
    backend,
    '''            "sourceStatus": row["source_status"],\n            "shadowStatus": row["shadow_status"],\n''',
    '''            "sourceStatus": row["source_status"],\n            "sourcePnl": row["source_pnl"],\n            "sourceStake": row["source_stake"],\n            "sourceOutcomeFinalized": (\n                str(row["source_status"] or "").upper() not in {"", "OPEN"}\n                and _finite(row["source_pnl"]) is not None\n            ),\n            "shadowStatus": row["shadow_status"],\n''',
)

panel = ROOT / "dashboard/app/strong-trend-guard-panel.tsx"
replace_once(
    panel,
    '''  blockedSettled?: number;\n  blockedWins?: number;\n  blockedLosses?: number;\n''',
    '''  blockedSettled?: number;\n  blockedPending?: number;\n  blockedFlat?: number;\n  blockedWins?: number;\n  blockedLosses?: number;\n''',
)
replace_once(
    panel,
    '''          <p>已阻擋結算 {stats.blockedSettled ?? 0} · 原本勝率 {pct(stats.blockedWinRate)} · 勝 {stats.blockedWins ?? 0}／敗 {stats.blockedLosses ?? 0}</p>\n          <small>Forward-only；資料缺失採 ALLOW_NOT_EVALUABLE，不把缺資料誤算成危險趨勢。</small>\n''',
    '''          <p>阻擋後已實現 {stats.blockedSettled ?? 0} · 等待結果 {stats.blockedPending ?? 0} · 原本勝率 {pct(stats.blockedWinRate)} · 勝 {stats.blockedWins ?? 0}／敗 {stats.blockedLosses ?? 0}／平 {stats.blockedFlat ?? 0}</p>\n          <small>避免虧損、犧牲獲利與淨保護只計已被 Guard 阻擋且來源交易已有實現 PnL 的反事實結果；允許交易只影響上方 Guard 後收益。</small>\n          <small>Forward-only；資料缺失採 ALLOW_NOT_EVALUABLE，不把缺資料誤算成危險趨勢。</small>\n''',
)

tests = ROOT / "tests/test_strong_trend_guard_shadows.py"
text = tests.read_text(encoding="utf-8")
append = r'''


def test_blocked_protection_uses_all_realized_source_exit_statuses(tmp_path):
    store = server.Store(tmp_path / "simulation.db")

    insert_observations(store, 201, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="M01",
        topic_id=1,
        market_id=201,
        side="UP",
        entry=.20,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="blocked loss source",
    )
    first = store.db.execute(
        "SELECT source_trade_id FROM strong_trend_guard_decisions WHERE market_id=201"
    ).fetchone()
    with store.lock:
        store.db.execute(
            "UPDATE trades SET status='STOP_LOSS_EXIT', pnl=-4.0 WHERE id=?",
            (int(first["source_trade_id"]),),
        )
        store.db.commit()

    insert_observations(store, 202, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="M01",
        topic_id=1,
        market_id=202,
        side="UP",
        entry=.20,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="blocked profit source",
    )
    second = store.db.execute(
        "SELECT source_trade_id FROM strong_trend_guard_decisions WHERE market_id=202"
    ).fetchone()
    with store.lock:
        store.db.execute(
            "UPDATE trades SET status='TARGET_FILLED', pnl=6.0 WHERE id=?",
            (int(second["source_trade_id"]),),
        )
        store.db.commit()

    payload = store.dashboard()["researchForward"]["strongTrendGuardExperiment"]
    stats = payload["strategies"]["R_STRONG_TREND_GUARD_M01"]
    assert stats["blockedSettled"] == 2
    assert stats["blockedPending"] == 0
    assert stats["blockedWins"] == 1
    assert stats["blockedLosses"] == 1
    assert stats["avoidedLossUsdt"] == 2.0
    assert stats["sacrificedProfitUsdt"] == 3.0
    assert stats["netProtectionUsdt"] == -1.0


def test_blocked_open_source_is_reported_pending(tmp_path):
    store = server.Store(tmp_path / "simulation.db")
    insert_observations(store, 203, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="M01",
        topic_id=1,
        market_id=203,
        side="UP",
        entry=.20,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="pending blocked source",
    )
    payload = store.dashboard()["researchForward"]["strongTrendGuardExperiment"]
    stats = payload["strategies"]["R_STRONG_TREND_GUARD_M01"]
    assert stats["blocked"] == 1
    assert stats["blockedSettled"] == 0
    assert stats["blockedPending"] == 1
    assert stats["netProtectionUsdt"] == 0.0
'''
if "test_blocked_protection_uses_all_realized_source_exit_statuses" in text:
    raise RuntimeError("tests already patched")
tests.write_text(text + append, encoding="utf-8")

print("strong trend protection metrics fix applied")
