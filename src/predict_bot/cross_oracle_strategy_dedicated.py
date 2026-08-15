from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_DB = Path(
    os.environ.get(
        "PREDICT_CROSS_ORACLE_STRATEGY_DB",
        ROOT / "data" / "cross_oracle_strategy.db",
    )
)

# The V7 strategy chain historically resolves its DB path from
# PREDICT_CROSS_ORACLE_DB at import time. Redirect that legacy name before any
# strategy module is imported. This affects only the 8768 process.
os.environ["PREDICT_CROSS_ORACLE_STRATEGY_DB"] = str(STRATEGY_DB)
os.environ["PREDICT_CROSS_ORACLE_DB"] = str(STRATEGY_DB)

from . import cross_oracle_strategy_chop_guard_v7 as v7  # noqa: E402


def main() -> int:
    return v7.main()


if __name__ == "__main__":
    raise SystemExit(main())
