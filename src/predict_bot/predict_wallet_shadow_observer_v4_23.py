from __future__ import annotations

"""Compatibility entrypoint for the retired Wallet Shadow v4.23 service.

Port 8776 has been rewritten as TARGET_WALLET_OFFICIAL_V1.  Keeping this module
name as a tiny launcher lets older Windows PID/process ownership checks safely
recognize and replace the process during the cutover without importing any of
the old v4.x observer inheritance chain.
"""

from .target_wallet_official_v1 import main


if __name__ == "__main__":
    raise SystemExit(main())
