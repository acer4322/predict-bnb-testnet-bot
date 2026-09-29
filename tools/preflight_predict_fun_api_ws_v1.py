from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx
import websocket


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
API = "https://api.predict.fun"
WS = "wss://ws.predict.fun/ws"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="predict_fun_official_api_ws_preflight_v1.json")
    args = parser.parse_args()
    api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("PREDICT_FUN_API_KEY is missing")

    with httpx.Client(
        timeout=15,
        trust_env=False,
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "BTC-5M-Lab-ReadOnly-Preflight/1.0",
        },
    ) as client:
        response = client.get(
            f"{API}/v1/search",
            params={"query": "Bitcoin", "includeResolved": "false", "limit": 20},
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    markets = data.get("markets") if isinstance(data, dict) else None
    if not isinstance(markets, list) or not markets:
        raise RuntimeError("Predict REST search returned no Bitcoin markets")
    candidates = [
        market
        for market in markets
        if isinstance(market, dict)
        and "Bitcoin Up or Down" in str(market.get("title") or market.get("question") or "")
    ]
    selected = candidates[0] if candidates else markets[0]
    market_id = int(selected.get("id") or selected.get("marketId"))

    ws = websocket.create_connection(
        WS,
        header=[f"x-api-key: {api_key}"],
        timeout=15,
        http_proxy_host=None,
        http_proxy_port=None,
    )
    topic = f"predictOrderbook/{market_id}"
    messages: list[dict[str, Any]] = []
    orderbook: dict[str, Any] | None = None
    try:
        ws.send(
            json.dumps(
                {"method": "subscribe", "requestId": 1, "params": [topic]},
                separators=(",", ":"),
            )
        )
        for _ in range(8):
            raw = ws.recv()
            message = json.loads(raw)
            messages.append(
                {
                    "topic": message.get("topic") if isinstance(message, dict) else None,
                    "keys": sorted(message.keys()) if isinstance(message, dict) else [],
                    "bytes": len(raw),
                }
            )
            if isinstance(message, dict) and message.get("topic") == topic:
                value = message.get("data")
                if isinstance(value, dict):
                    orderbook = value
                break
    finally:
        ws.close()

    checks = {
        "apiKeyPresent": True,
        "restAuthenticated": response.status_code == 200,
        "bitcoinMarketsReturned": len(markets) > 0,
        "websocketConnected": len(messages) > 0,
        "subscriptionAcknowledged": any("requestId" in message["keys"] for message in messages),
        "orderbookSnapshotReceived": orderbook is not None,
        "orderbookSchemaComplete": orderbook is not None
        and all(
            key in orderbook
            for key in (
                "marketId",
                "bids",
                "asks",
                "orderCount",
                "lastOrderSettled",
                "settlementsPending",
                "updateTimestampMs",
                "version",
            )
        ),
    }
    report = {
        "version": "PREDICT_FUN_OFFICIAL_API_WS_PREFLIGHT_V1",
        "researchOnly": True,
        "readOnly": True,
        "credential": "PREDICT_FUN_API_KEY_PRESENT_NOT_EXPORTED",
        "rest": {
            "endpoint": "/v1/search",
            "status": response.status_code,
            "bitcoinMarkets": len(markets),
            "selectedMarketId": market_id,
            "selectedStatus": selected.get("status"),
            "selectedTitle": str(selected.get("title") or selected.get("question") or ""),
        },
        "websocket": {
            "endpoint": WS,
            "topic": topic,
            "messagesSeen": len(messages),
            "snapshotBytes": next((message["bytes"] for message in messages if message["topic"] == topic), None),
            "payloadKeys": sorted(orderbook.keys()) if orderbook is not None else [],
            "bidLevels": len(orderbook.get("bids") or []) if orderbook is not None else 0,
            "askLevels": len(orderbook.get("asks") or []) if orderbook is not None else 0,
            "orderCount": orderbook.get("orderCount") if orderbook is not None else None,
            "updateTimestampMs": orderbook.get("updateTimestampMs") if orderbook is not None else None,
            "version": orderbook.get("version") if orderbook is not None else None,
        },
        "checks": checks,
        "decision": "READY_USE_EXISTING_OFFICIAL_COLLECTOR" if all(checks.values()) else "BLOCKED",
        "boundary": "This preflight does not create orders, start services, or write any live collector database. Existing Predict Execution Tape V1 remains the only performance data path.",
    }
    output = OUT / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": all(checks.values()), "output": str(output), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
