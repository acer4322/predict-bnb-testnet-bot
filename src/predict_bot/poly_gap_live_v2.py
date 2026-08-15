from __future__ import annotations

from typing import Any

from . import poly_gap_live as base


class RolloverSafePolyGapLiveEngine(base.PolyGapLiveEngine):
    """Prevent an old held round from reacting to the next market's Poly feed."""

    def _tick(self) -> None:
        active: dict[str, Any] | None = self._current_active_round()
        if active is not None and str(active.get("state")) == "OPEN":
            market = self._prime_market()
            if market is None or int(market.get("market_id") or 0) != int(active["market_id"]):
                # Once Binance has rolled to the next five-minute market, the
                # current Poly direction belongs to that new window. Never use
                # it as an exit trigger for the previous held position. The old
                # round can only close from its own official settlement here.
                self._settle_hold(active)
                still_active = self._current_active_round()
                if still_active is not None and int(still_active["id"]) == int(active["id"]):
                    self.status = "WAITING_SETTLEMENT"
                return
        return super()._tick()


base.PolyGapLiveEngine = RolloverSafePolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
