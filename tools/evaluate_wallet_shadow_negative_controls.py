from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_wallet_shadow_sync import (  # noqa: E402
    DEFAULT_DB,
    Event,
    _parent_targets,
    eligible_markets,
    one_to_one_matches,
    score,
)
from tools.replay_wallet_shadow_causal_candidates import quote_change_candidate  # noqa: E402
from tools.replay_wallet_shadow_taker_fair_value import (  # noqa: E402
    DEFAULT_OBSERVER_DB,
    fair_value_candidate,
)


def transform(events: list[Event], *, shift_ms: int = 0, flip_side: bool = False) -> list[Event]:
    return [
        Event(
            at_ms=event.at_ms + shift_ms,
            side=("DOWN" if event.side == "UP" else "UP") if flip_side else event.side,
            price=(1.0 - event.price) if flip_side and event.price is not None else event.price,
            event_id=f"CONTROL:{shift_ms}:{int(flip_side)}:{event.event_id}",
        )
        for event in events
    ]


def evaluate_control(
    wallet: sqlite3.Connection,
    markets: list[tuple[int, int]],
    role: str,
    generator: Callable[[int, int], list[Event]],
    *,
    price_delta: float,
    shift_ms: int = 0,
    flip_side: bool = False,
) -> dict:
    targets: list[Event] = []
    shadows: list[Event] = []
    matches: list[dict] = []
    for market_id, start_ms in markets:
        target = _parent_targets(wallet, market_id, role, start_ms)
        shadow = transform(generator(market_id, start_ms), shift_ms=shift_ms, flip_side=flip_side)
        matched = one_to_one_matches(target, shadow, max_lag_ms=3_000, max_price_delta=price_delta)
        targets.extend(target)
        shadows.extend(shadow)
        matches.extend(matched)
    return score(targets, shadows, matches)


def main() -> int:
    parser = argparse.ArgumentParser(description="Temporal and side negative controls for locked Wallet Shadow candidates")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--observer-db", type=Path, default=DEFAULT_OBSERVER_DB)
    parser.add_argument("--skip-markets", type=int, default=9)
    parser.add_argument("--markets", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    wallet = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    observer = sqlite3.connect(f"file:{args.observer_db}?mode=ro", uri=True)
    wallet.row_factory = sqlite3.Row
    observer.row_factory = sqlite3.Row
    try:
        keys = eligible_markets(wallet, None)[args.skip_markets : args.skip_markets + args.markets]
        if len(keys) != args.markets:
            raise SystemExit(f"need {args.markets} control markets, found {len(keys)}")
        maker_generator = lambda mid, start: quote_change_candidate(  # noqa: E731
            wallet, mid, start, cooldown_ms=1_000, direction="ANY"
        )
        taker_generator = lambda mid, start: fair_value_candidate(  # noqa: E731
            observer, mid, start, minimum_edge=-0.05, cooldown_ms=5_000
        )
        controls = [
            {"name": "ACTUAL", "shiftMs": 0, "flipSide": False},
            {"name": "SHIFT_MINUS_30S", "shiftMs": -30_000, "flipSide": False},
            {"name": "SHIFT_MINUS_10S", "shiftMs": -10_000, "flipSide": False},
            {"name": "SHIFT_PLUS_10S", "shiftMs": 10_000, "flipSide": False},
            {"name": "SHIFT_PLUS_30S", "shiftMs": 30_000, "flipSide": False},
            {"name": "FLIPPED_SIDE", "shiftMs": 0, "flipSide": True},
        ]
        report = {
            "marketIds": [mid for mid, _ in keys],
            "controls": [
                {
                    **control,
                    "maker": evaluate_control(
                        wallet,
                        keys,
                        "MAKER",
                        maker_generator,
                        price_delta=0.011,
                        shift_ms=control["shiftMs"],
                        flip_side=control["flipSide"],
                    ),
                    "taker": evaluate_control(
                        wallet,
                        keys,
                        "TAKER",
                        taker_generator,
                        price_delta=0.021,
                        shift_ms=control["shiftMs"],
                        flip_side=control["flipSide"],
                    ),
                }
                for control in controls
            ],
            "interpretation": "A genuine timing/side trigger should materially outperform shifted and side-flipped controls on unseen markets.",
        }
    finally:
        wallet.close()
        observer.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
