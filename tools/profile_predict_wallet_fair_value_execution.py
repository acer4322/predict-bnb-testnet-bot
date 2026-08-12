from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from profile_predict_wallet_fair_value_execution_v2_impl import *  # noqa: F401,F403,E402


if __name__ == "__main__":
    raise SystemExit(main())
