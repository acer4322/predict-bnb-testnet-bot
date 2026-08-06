from __future__ import annotations

from functools import wraps
from typing import Any

from . import decision_strategy_frozen_rules_patch as _frozen
from . import decision_strategy_shadows as _decision


HISTORY_QUERY_LIMIT = 2_000


def _closed_history(
    self: Any,
    source_strategy: str,
    as_of: str,
) -> list[dict[str, Any]]:
    version_row = self.store.db.execute(
        """SELECT COALESCE(MAX(closed_at), ''), COALESCE(MAX(id), 0)
             FROM trades
            WHERE strategy=? AND closed_at IS NOT NULL AND pnl IS NOT NULL
              AND closed_at<?""",
        (source_strategy, as_of),
    ).fetchone()
    latest_closed_at = str(version_row[0] or "") if version_row is not None else ""
    latest_id = int(version_row[1] or 0) if version_row is not None else 0

    cache = getattr(self, "_decision_frozen_history_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._decision_frozen_history_cache = cache
    key = (source_strategy, latest_closed_at, latest_id)
    if key in cache:
        return [dict(row) for row in cache[key]]

    rows = self.store.db.execute(
        """SELECT id, strategy, topic_id, market_id, side, entry_price,
                  stake, pnl, fee_rate_bps, opened_at, closed_at
             FROM trades
            WHERE strategy=? AND closed_at IS NOT NULL AND pnl IS NOT NULL
              AND closed_at<?
            ORDER BY closed_at DESC, id DESC LIMIT ?""",
        (source_strategy, as_of, HISTORY_QUERY_LIMIT),
    ).fetchall()
    history = [dict(row) for row in reversed(rows)]
    cache[key] = history
    return [dict(row) for row in history]


def _patch_summary_metadata() -> None:
    original = _decision._decision_summary
    if getattr(original, "_decision_runtime_metadata_v1", False):
        return

    @wraps(original)
    def corrected_summary(store: Any, tracker: Any) -> dict[str, Any]:
        payload = original(store, tracker)
        rules = dict(payload.get("rules") or {})
        rules.update(
            {
                "historyLimit": None,
                "historyHalfLife": None,
                "minimumHistory": None,
                "rank1MinimumFamilies": _frozen.RANK1_MIN_SUPPORTERS,
                "rank1AgreementWeight": _frozen.RANK1_DIRECTION_SHARE,
                "rank1FamilyCap": None,
                "rank2FamilyCap": _frozen.RANK2_FAMILY_CAP,
                "rank2CapWindow": _frozen.RANK2_HISTORY,
                "minimumNetEdge": _frozen.RANK2_EDGE_MARGIN,
                "historyOrdering": "closed_at_then_id",
                "sourceContextCapture": "nearest_observation_at_or_before_source_open",
            }
        )
        payload["rules"] = rules
        return payload

    corrected_summary._decision_runtime_metadata_v1 = True  # type: ignore[attr-defined]
    _decision._decision_summary = corrected_summary


def install_decision_strategy_runtime_corrections_patch() -> None:
    _decision.DecisionStrategyTracker._closed_history = _closed_history
    _patch_summary_metadata()
