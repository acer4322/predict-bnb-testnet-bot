from __future__ import annotations

from http.server import ThreadingHTTPServer

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_21 as v4_21
from .target_taker_multi_entry_paper_v1 import COHORT, MultiEntryPaperMixin, VERSION as MULTI_ENTRY_VERSION


VERSION = "PREDICT_WALLET_SHADOW_V0_27_TARGET_TAKER_MULTI_ENTRY_PAPER_V1"


class WalletShadowObserver(MultiEntryPaperMixin, v4_21.WalletShadowObserver):
    """V4.21 plus an isolated paper-only every-signal multi-entry experiment."""

    def _target_taker_multi_entry_performance(self):
        performance = super()._target_taker_multi_entry_performance()
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT e.market_id,e.side,e.stake_usdt,e.shares,r.winner
                         FROM wallet_target_taker_multi_entry_v1_events e
                         JOIN wallet_target_taker_multi_entry_v1_results r
                           ON r.market_id=e.market_id
                        WHERE r.resolved_at_ms>=?
                        ORDER BY e.market_id,e.decision_at_ms,e.id""",
                    (cutoff,),
                )
            ]
        seen_markets: set[int] = set()
        later_entries = later_wins = later_losses = 0
        later_stake = later_payout = 0.0
        for row in rows:
            market_id = int(row["market_id"])
            if market_id not in seen_markets:
                seen_markets.add(market_id)
                continue
            later_entries += 1
            stake = float(row["stake_usdt"] or 0.0)
            shares = float(row["shares"] or 0.0)
            side = str(row["side"] or "").upper()
            winner = str(row["winner"] or "").upper()
            won = side in {"UP", "DOWN"} and side == winner
            later_wins += int(won)
            later_losses += int(not won)
            later_stake += stake
            if won:
                later_payout += shares
        later_pnl = later_payout - later_stake
        performance.update(
            {
                "laterEntries": later_entries,
                "laterWinningEntries": later_wins,
                "laterLosingEntries": later_losses,
                "laterEntryWinRate": later_wins / later_entries if later_entries else None,
                "laterStakeUsdt": later_stake,
                "laterPayoutUsdt": later_payout,
                "laterNetPnlUsdt": later_pnl,
                "laterNetRoi": later_pnl / later_stake if later_stake > 0 else None,
                "incrementalQuestion": (
                    "Performance of entries after the first entry in each market; this is the direct test of whether removing the one-entry lock adds value."
                ),
            }
        )
        return performance

    def snapshot(self):
        payload = super().snapshot()
        payload["version"] = VERSION
        return payload

    def health_snapshot(self):
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["targetTakerMultiEntryExperimentVersion"] = MULTI_ENTRY_VERSION
        payload["targetTakerMultiEntryCohort"] = COHORT
        return payload


class _Handler(v4_21._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_22Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    config = observer.target_taker_live_config
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"TargetTakerMode={config.mode}; venue={config.venue}; "
        f"multiEntry={COHORT}; paperOnly=true; everyNewTradeSnapshot=true; "
        "liveMultiEntry=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
