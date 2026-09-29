from __future__ import annotations

from typing import Any


def evaluate(context: dict[str, Any]) -> dict[str, Any] | None:
    """Template for one-concept Flash Sandbox experiments.

    Return only paper decisions. Supported keys include:
      desiredPortfolioAction, executionChoice, primaryReason,
      makerDecision, takerFill, cancelMakerOrders, collected, seedPolicy.

    A takerFill has: side, price, shares, purpose.
    Returning None leaves the sandbox's default Maker/WAIT behavior unchanged.
    """
    return None
