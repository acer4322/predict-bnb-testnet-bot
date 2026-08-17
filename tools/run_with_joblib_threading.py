from __future__ import annotations

import runpy
import sys
from pathlib import Path

from joblib import parallel_config


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python tools/run_with_joblib_threading.py <script.py> [args ...]")

    script = Path(sys.argv[1]).expanduser().resolve()
    if not script.exists():
        raise SystemExit(f"script missing: {script}")

    forwarded = [str(script), *sys.argv[2:]]
    original_argv = sys.argv
    try:
        sys.argv = forwarded
        # The special-regime EBM suite runs many repeated fits. On Windows +
        # Python 3.13, loky's process resource tracker can emit cleanup errors
        # for temporary memmap folders. Force a shared-memory/thread backend for
        # this research runner only; model hyperparameters and n_jobs values are
        # otherwise unchanged.
        with parallel_config(backend="threading", require="sharedmem"):
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
