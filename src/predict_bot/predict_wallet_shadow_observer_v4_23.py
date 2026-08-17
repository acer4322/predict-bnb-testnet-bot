from __future__ import annotations

"""Compatibility entry point for the strategy-free Target Official service.

Port 8776 remains recognizable to existing Windows PID/process ownership checks
through this module name, while the implementation is the isolated Target
Official collector. V2 restores the old Target historical win/loss ledger as a
read-only display source without reviving the retired Wallet Shadow strategy
inheritance chain.
"""

from .target_wallet_official_v2 import VERSION, main


if __name__ == "__main__":
    raise SystemExit(main())
