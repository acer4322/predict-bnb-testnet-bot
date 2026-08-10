from __future__ import annotations

import math
import os
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v23 import DelayedEntryPolyGapLiveEngine


DEFAULT_REDUCE_LOSS_ENABLED = os.environ.get(
    "PREDICT_POLY_GAP_LIVE_REDUCE_LOSS_ENABLED", "false"
).strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_REDUCE_LOSS_USDT = max(
    0.01,
    float(
        os.environ.get(
            "PREDICT_POLY_GAP_LIVE_REDUCE_LOSS_USDT",
            str(max(0.01, base.DEFAULT_MAX_LOSS_USDT / 2.0)),
        )
    ),
)
DEFAULT_REDUCED_STAKE_USDT = max(
    0.01,
    min(
        base.DEFAULT_STAKE_USDT,
        float(
            os.environ.get(
                "PREDICT_POLY_GAP_LIVE_REDUCED_STAKE_USDT",
                str(max(0.01, base.DEFAULT_STAKE_USDT / 2.0)),
            )
        ),
    ),
)

_FORWARD_SETTING_KEYS = {
    "runtimeEnabled",
    "stakeUsdt",
    "maximumLossEnabled",
    "maximumLossUsdt",
    "entryDelaySeconds",
    "resetLoss",
}


class TieredLossGuardPolyGapLiveEngine(DelayedEntryPolyGapLiveEngine):
    """V24: latched reduced stake before the existing hard maximum-loss stop.

    Both stages use the same realized net-PnL counter and the same reset point.
    Stage 1 changes only the stake of NEW rounds. Stage 2 keeps the existing
    fail-closed behavior that pauses new entries. Existing positions, SELL exits,
    reconciliation and official settlement recovery are never blocked by either
    stage.
    """

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        defaults = {
            "reduce_loss_enabled": "1" if DEFAULT_REDUCE_LOSS_ENABLED else "0",
            "reduce_loss_usdt": f"{DEFAULT_REDUCE_LOSS_USDT:.8f}",
            "reduced_stake_usdt": f"{DEFAULT_REDUCED_STAKE_USDT:.8f}",
            "loss_reduced": "0",
        }
        now = base._now_ms()
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        settings.update(
            {
                "reduceLossEnabled": self._setting("reduce_loss_enabled", "0") == "1",
                "reduceLossUsdt": float(
                    self._setting("reduce_loss_usdt", str(DEFAULT_REDUCE_LOSS_USDT))
                ),
                "reducedStakeUsdt": float(
                    self._setting("reduced_stake_usdt", str(DEFAULT_REDUCED_STAKE_USDT))
                ),
                "lossReduced": self._setting("loss_reduced", "0") == "1",
            }
        )
        return settings

    def _loss_state(self) -> dict[str, Any]:
        state = super()._loss_state()
        settings = self._settings()
        current_loss = float(state["currentLossUsdt"])
        reduction_threshold = float(settings["reduceLossUsdt"])
        reduction_enabled = bool(settings["reduceLossEnabled"])
        reduction_tripped = bool(settings["lossReduced"])
        normal_stake = float(settings["stakeUsdt"])
        reduced_stake = float(settings["reducedStakeUsdt"])
        stop_tripped = bool(state["tripped"])
        phase = "STOPPED" if stop_tripped else "REDUCED" if reduction_tripped else "NORMAL"
        state.update(
            {
                "reductionEnabled": reduction_enabled,
                "reductionThresholdUsdt": reduction_threshold,
                "reducedStakeUsdt": reduced_stake,
                "reductionTripped": reduction_tripped,
                "remainingBeforeReductionUsdt": (
                    0.0 if reduction_tripped else max(0.0, reduction_threshold - current_loss)
                ),
                "normalStakeUsdt": normal_stake,
                "effectiveStakeUsdt": reduced_stake if reduction_tripped else normal_stake,
                "phase": phase,
                "reductionLatchUntilReset": True,
                "sharedLossCounter": True,
            }
        )
        return state

    def _validate_tiered_settings(self, values: dict[str, Any]) -> None:
        candidate = dict(self._settings())
        for key in ("maximumLossEnabled", "reduceLossEnabled"):
            if key in values:
                candidate[key] = bool(values[key])

        numeric_rules = {
            "stakeUsdt": (0.01, 100.0),
            "maximumLossUsdt": (0.01, 1_000_000.0),
            "reduceLossUsdt": (0.01, 1_000_000.0),
            "reducedStakeUsdt": (0.01, 100.0),
        }
        for key, (minimum, maximum) in numeric_rules.items():
            if key not in values:
                continue
            try:
                number = float(values[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number") from exc
            if not math.isfinite(number) or not minimum <= number <= maximum:
                raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}")
            candidate[key] = number

        if bool(candidate["reduceLossEnabled"]):
            if float(candidate["reducedStakeUsdt"]) > float(candidate["stakeUsdt"]) + 1e-12:
                raise ValueError(
                    "reducedStakeUsdt must not exceed stakeUsdt when reduction is enabled"
                )
            if (
                bool(candidate["maximumLossEnabled"])
                and float(candidate["reduceLossUsdt"])
                >= float(candidate["maximumLossUsdt"]) - 1e-12
            ):
                raise ValueError(
                    "reduceLossUsdt must be lower than maximumLossUsdt when both guards are enabled"
                )

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = _FORWARD_SETTING_KEYS | {
            "reduceLossEnabled",
            "reduceLossUsdt",
            "reducedStakeUsdt",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError("unsupported settings: " + ", ".join(unknown))

        self._validate_tiered_settings(values)

        forwarded = {
            key: value for key, value in values.items() if key in _FORWARD_SETTING_KEYS
        }
        if forwarded:
            super().update_settings(forwarded)

        if "reduceLossEnabled" in values:
            enabled = bool(values["reduceLossEnabled"])
            self._set_setting("reduce_loss_enabled", "1" if enabled else "0")
            if not enabled:
                self._set_setting("loss_reduced", "0")
        if "reduceLossUsdt" in values:
            self._set_setting("reduce_loss_usdt", f"{float(values['reduceLossUsdt']):.8f}")
        if "reducedStakeUsdt" in values:
            self._set_setting("reduced_stake_usdt", f"{float(values['reducedStakeUsdt']):.8f}")
        if values.get("resetLoss") is True:
            self._set_setting("loss_reduced", "0")
            self._event(
                "WARN",
                "LOSS_REDUCTION_RESET",
                None,
                None,
                "reduced-stake stage reset together with the shared loss counter",
            )

        # A newly lowered threshold should take effect immediately instead of
        # waiting for another completed round.
        self._check_max_loss()
        return self.snapshot()

    def _check_max_loss(self) -> None:
        state = self._loss_state()
        if (
            state["reductionEnabled"]
            and not state["reductionTripped"]
            and state["currentLossUsdt"] + 1e-12 >= state["reductionThresholdUsdt"]
        ):
            self._set_setting("loss_reduced", "1")
            self._event(
                "WARN",
                "LOSS_REDUCTION_TRIPPED",
                None,
                None,
                (
                    f"loss {state['currentLossUsdt']:.4f} reached reduction threshold "
                    f"{state['reductionThresholdUsdt']:.4f}; new rounds use "
                    f"{state['reducedStakeUsdt']:.4f} USDT until loss reset"
                ),
            )
        super()._check_max_loss()

    def _insert_round(
        self,
        *,
        market: dict[str, Any],
        side: str,
        token_id: str,
        stake: float,
        poly_selected: float,
        ask: float,
        edge: float,
    ) -> dict[str, Any]:
        # The caller passes the configured normal stake. Replace it only at the
        # point a real NEW round is created, so market polling and exit handling
        # never pay an extra risk-query cost.
        risk = self._loss_state()
        effective_stake = float(risk["effectiveStakeUsdt"])
        return super()._insert_round(
            market=market,
            side=side,
            token_id=token_id,
            stake=effective_stake,
            poly_selected=poly_selected,
            ask=ask,
            edge=edge,
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V24"
        payload["tieredLossGuard"] = {
            "sharedCounter": "realized net PnL since loss_reset_round_id",
            "stage1": "REDUCE_NEW_ROUND_STAKE",
            "stage2": "STOP_NEW_ROUNDS",
            "reductionIsLatchedUntilReset": True,
            "openPositionExitManagementUnaffected": True,
            "entryDelayUnaffected": True,
            "settlementRecoveryUnaffected": True,
            "transportAmbiguityFailClosedUnaffected": True,
        }
        return payload


base.PolyGapLiveEngine = TieredLossGuardPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
