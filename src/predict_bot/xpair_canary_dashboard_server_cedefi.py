from __future__ import annotations

from dataclasses import asdict
from typing import Any

from . import xpair_canary_dashboard_server as base


class CeDeFiLaunchRequest(base.LaunchRequest):
    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CeDeFiLaunchRequest":
        normalized = dict(payload)
        # Reuse the original validation for every field except account type.
        normalized["accountType"] = "SPOT"
        validated = base.LaunchRequest.from_payload(normalized)
        values = asdict(validated)
        values["account_type"] = "CeDeFi"
        return cls(**values)

    def command(self) -> list[str]:
        command = super().command()
        command[2] = "predict_bot.xpair_btc_eth_canary_cedefi"
        account_index = command.index("--account-type") + 1
        command[account_index] = "CeDeFi"
        return command


_original_state_payload = base.state_payload


def state_payload() -> dict[str, Any]:
    payload = _original_state_payload()
    payload["defaults"]["accountType"] = "CeDeFi"
    payload["policy"]["effectivePaymentAccount"] = "CeDeFi"
    return payload


def main() -> None:
    base.LaunchRequest = CeDeFiLaunchRequest
    base.state_payload = state_payload
    base.main()


if __name__ == "__main__":
    main()
