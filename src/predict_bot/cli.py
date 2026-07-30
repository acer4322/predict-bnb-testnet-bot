from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core import BinancePredictionClient, run_bot, utc_now


def credentials() -> tuple[str, str]:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise SystemExit(
            "Missing BINANCE_API_KEY/BINANCE_API_SECRET. Create a read-only HMAC API key, "
            "set both as local environment variables, and never paste them into chat."
        )
    return api_key, api_secret


def discover(symbol: str) -> None:
    api_key, api_secret = credentials()
    market = BinancePredictionClient(api_key, api_secret).find_market(symbol, utc_now())
    if not market:
        raise SystemExit(f"No current or nearby 5-minute market for {symbol}.")
    variant = market.get("variantData", {})
    selected = market["_selectedMarket"]
    print(f"topicId={market['marketTopicId']}")
    print(f"marketId={selected['market']['marketId']}")
    print(f"title={market['title']}")
    print(f"window_ms={market['startDate']} -> {market['endDate']}")
    print(f"feed={variant.get('priceFeedProvider')} {variant.get('priceFeedSymbol')}")
    print(f"startPrice={variant.get('startPrice')}")
    print(f"upToken={selected['up']['tokenId']} downToken={selected['down']['tokenId']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance Prediction Trading BTC 5m paper bot")
    sub = parser.add_subparsers(dest="command", required=True)
    discover_parser = sub.add_parser("discover", help="show current or next 5-minute market")
    discover_parser.add_argument("--symbol", default="BTCUSDT")
    run_parser = sub.add_parser("run", help="run paper trading against Binance production market data")
    run_parser.add_argument("--symbol", default="BTCUSDT")
    run_parser.add_argument("--duration", type=int, default=60)
    run_parser.add_argument("--interval", type=float, default=10.0)
    run_parser.add_argument("--min-edge", type=float, default=0.05)
    run_parser.add_argument(
        "--output", type=Path, default=Path("data/binance_production_observations.csv")
    )
    args = parser.parse_args()
    if args.command == "discover":
        discover(args.symbol)
    elif args.command == "run":
        api_key, api_secret = credentials()
        run_bot(
            args.symbol, args.duration, max(args.interval, 5.0), args.min_edge,
            args.output, api_key, api_secret
        )


if __name__ == "__main__":
    main()
