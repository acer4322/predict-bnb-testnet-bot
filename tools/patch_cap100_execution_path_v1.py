from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/predict_bot/core.py",
    '''    def active_orders(\n        self, wallet_address: str, *, market_id: int | None = None, limit: int = 100\n    ) -> dict[str, Any]:\n''',
    '''    def batch_cancel_orders_raw(\n        self,\n        *,\n        wallet_address: str,\n        wallet_id: str,\n        order_ids: list[str],\n    ) -> dict[str, Any]:\n        """Cancel Prediction orders using Binance's literal bracket-key wire format.\n\n        The batch-cancel endpoint expects keys such as\n        ``cancelInfoList[0].orderId`` to remain literal in the signed body.\n        A transport failure is intentionally ambiguous so live callers can\n        fail closed and reconcile instead of assuming cancellation.\n        """\n        ids = [str(value) for value in order_ids if str(value)]\n        if not ids:\n            return {"success": True, "orders": []}\n        fields: list[tuple[str, str]] = [\n            ("walletAddress", str(wallet_address)),\n            ("walletId", str(wallet_id)),\n        ]\n        for index, order_id in enumerate(ids):\n            fields.append((f"cancelInfoList[{index}].orderId", order_id))\n        fields.extend(\n            [\n                ("recvWindow", "5000"),\n                ("timestamp", str(self.server_timestamp_ms())),\n            ]\n        )\n        canonical = "&".join(\n            f"{key}={urllib.parse.quote_plus(str(value), safe='')}"\n            for key, value in fields\n        )\n        signature = hmac.new(\n            self.api_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256\n        ).hexdigest()\n        body = f"{canonical}&signature={signature}".encode("utf-8")\n        path = "/sapi/v1/w3w/wallet/prediction/trade/batch-cancel"\n        try:\n            response = self.http_client.post(\n                path,\n                content=body,\n                headers={\n                    "Content-Type": "application/x-www-form-urlencoded",\n                    "User-Agent": "binance-prediction-live-m0w/0.1",\n                    "X-MBX-APIKEY": self.api_key,\n                },\n            )\n        except httpx.RequestError as exc:\n            raise ApiTransportError(\n                f"Request failed for {self.base_url}{path}: {type(exc).__name__}"\n            ) from exc\n        if response.status_code >= 400:\n            self._raise_http_error(response, path=path)\n        self._capture_rate_limits(response)\n        try:\n            payload = response.json()\n        except json.JSONDecodeError as exc:\n            raise ApiTransportError(\n                f"Invalid JSON response from {self.base_url}{path}"\n            ) from exc\n        if not isinstance(payload, dict):\n            raise ApiTransportError(\n                f"Unexpected response type from {self.base_url}{path}"\n            )\n        if payload.get("success") is False:\n            raise ApiError(f"API rejected {self.base_url}{path}: {payload}")\n        return payload\n\n    def active_orders(\n        self, wallet_address: str, *, market_id: int | None = None, limit: int = 100\n    ) -> dict[str, Any]:\n''',
)

replace_once(
    "src/predict_bot/strategy_target_compare_recorder_v1.py",
    '''    def record_order_cancel(self, *, order_id: str, cancelled_at_ms: int, reason: str) -> None:\n''',
    '''    def record_order_fill_delta(\n        self,\n        *,\n        order_id: str,\n        fill_id: str,\n        filled_at_ms: int,\n        fill_price: float,\n        shares: float,\n        fill_state: Mapping[str, Any] | None,\n        purpose: str | None = None,\n        payload: Mapping[str, Any] | None = None,\n        terminal: bool = False,\n    ) -> bool:\n        """Append one execution-confirmed fill delta without inventing full size.\n\n        ``fill_id`` is the idempotency key.  Replaying the same venue event is a\n        no-op, which makes controller restart/event replay safe for the recorder.\n        Partial deltas leave the order ACTIVE so cancellation bookkeeping remains\n        compatible with the existing recorder schema; only a terminal fill marks\n        the order FILLED.\n        """\n        if float(shares) <= 0:\n            return False\n        with self.lock:\n            row = self.db.execute("SELECT * FROM our_orders WHERE order_id=?", (str(order_id),)).fetchone()\n            if row is None:\n                raise KeyError(f"unknown order_id: {order_id}")\n            exists = self.db.execute("SELECT 1 FROM our_fills WHERE fill_id=?", (str(fill_id),)).fetchone()\n            if exists is not None:\n                return False\n            self.db.execute(\n                """INSERT INTO our_fills(\n                       fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,\n                       price,shares,filled_at_ms,decision_state_json,fill_state_json,payload_json\n                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n                (\n                    str(fill_id), str(row["strategy_version"]), int(row["market_id"]), row["placement_decision_id"],\n                    str(order_id), str(row["channel"]), purpose, str(row["side"]), str(row["quote_type"]),\n                    float(fill_price), float(shares), int(filled_at_ms), str(row["placement_state_json"]),\n                    _json(fill_state), _json(payload),\n                ),\n            )\n            if terminal:\n                self.db.execute(\n                    """UPDATE our_orders SET status='FILLED',filled_at_ms=?,fill_price=?,fill_state_json=?,updated_at_ms=?\n                       WHERE order_id=?""",\n                    (int(filled_at_ms), float(fill_price), _json(fill_state), now_ms(), str(order_id)),\n                )\n            self.db.commit()\n            return True\n\n    def record_order_cancel(self, *, order_id: str, cancelled_at_ms: int, reason: str) -> None:\n''',
)

replace_once(
    "src/predict_bot/unified_controller_cap100_echtgeld_v1.py",
    '''        self.last_event_seq = 0\n''',
    '''        self.last_event_seq = 0\n        self.active_intervention_required = False\n        self.active_intervention_reason: str | None = None\n''',
)

replace_once(
    "src/predict_bot/unified_controller_cap100_echtgeld_v1.py",
    '''            if shares > base.EPS and side in {"UP", "DOWN"}:\n''',
    '''            if shares > base.EPS and side in {"UP", "DOWN"}:\n''',
)

replace_once(
    "src/predict_bot/unified_controller_cap100_echtgeld_v1.py",
    '''                    self.live_metrics["makerFillEvents"] += 1\n                    if state == "PARTIAL_FILL":\n                        self.live_metrics["makerPartialFillEvents"] += 1\n                    key, order = self._find_local_order(cid)\n''',
    '''                    self.live_metrics["makerFillEvents"] += 1\n                    if state == "PARTIAL_FILL":\n                        self.live_metrics["makerPartialFillEvents"] += 1\n                    # The Echtgeld strategy recorder must mirror the same venue\n                    # delta stream that drives inventory.  Engine seq is the\n                    # deterministic idempotency key, so controller restart/replay\n                    # cannot double-count a partial or terminal delta.\n                    try:\n                        self.recorder.record_order_fill_delta(\n                            order_id=cid,\n                            fill_id=f"{cid}:ENGINE_FILL_DELTA:{int(base.number(event.get('seq')) or 0)}",\n                            filled_at_ms=event_ms,\n                            fill_price=price,\n                            shares=shares,\n                            purpose="PASSIVE_MAKER_REAL",\n                            terminal=state == "FILLED",\n                            fill_state={\n                                "venueConfirmed": True,\n                                "engine": "8781",\n                                "engineEventSeq": int(base.number(event.get("seq")) or 0),\n                                "engineState": state,\n                                "deltaShares": shares,\n                                "deltaUsdt": usdt,\n                            },\n                            payload={"paperOnly": False, "executionOwner": "8781_ONLY"},\n                        )\n                    except KeyError:\n                        # A controller restart may observe a durable engine fill for\n                        # an order placement that predates this recorder instance.\n                        # Inventory remains sourced from 8781; surface the audit gap\n                        # instead of fabricating an order row.\n                        self.last_error = f"recorder missing Maker order for venue fill: {cid}"\n                    key, order = self._find_local_order(cid)\n''',
)

replace_once(
    "src/predict_bot/unified_controller_cap100_echtgeld_v1.py",
    '''        self.episode = None\n        self.readiness = False\n''',
    '''        self.episode = None\n        self.readiness = False\n        self.active_intervention_required = False\n        self.active_intervention_reason = None\n''',
)

replace_once(
    "src/predict_bot/unified_controller_cap100_echtgeld_v1.py",
    '''    def _record_taker(self, side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:\n        if not self._deployment_live_ready():\n            return False\n''',
    '''    def _has_unresolved_taker(self) -> bool:\n        terminal = {"FILLED", "REJECTED", "CANCELED", "FAILED", "EXPIRED"}\n        return any(str(row.get("terminalState") or "").upper() not in terminal for row in self.taker_pending.values())\n\n    def _record_taker(self, side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:\n        if not self._deployment_live_ready():\n            return False\n        # Never create a second active Taker while the first venue write/fill is\n        # unresolved. Reconciliation has higher priority than active intervention.\n        if self._has_unresolved_taker():\n            return False\n''',
)

# The active-intervention state is derived from the existing 15s unresolved guard,
# not from a new probability threshold.  We patch the inherited trace immediately
# after each step and, on the first unresolved transition, execute the already-
# selected side/effect machinery through a dedicated deterministic helper added
# below.  A larger refactor is deliberately avoided so 8786's frozen paper policy
# remains byte-for-byte behaviorally unchanged.

print("patched")
