"""Opt-in collector launcher. Existing service commands are not changed."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys

ENTRYPOINTS = (
    "predict_bot.target_wallet_official_v1",
    "predict_bot.target_wallet_official_v2",
    "predict_bot.predict_wallet_shadow_observer_v4_23",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=ENTRYPOINTS,
                        default="predict_bot.predict_wallet_shadow_observer_v4_23")
    parser.add_argument("--check-import", action="store_true",
                        help="Import only; do not construct a collector, open its DB or start a service")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PREDICT_BOT_BOOTSTRAP_PROFILE"] = "collector"
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if args.check_import:
        code = ("import importlib,json,sys; importlib.import_module(sys.argv[1]); "
                "print(json.dumps({'status':'IMPORT_ONLY_OK','module':sys.argv[1],"
                "'predict_bot_modules':sorted(n for n in sys.modules if n.startswith('predict_bot'))}))")
        command = [sys.executable, "-c", code, args.module]
    else:
        # Keep the historical module name visible in the child process command line.
        command = [sys.executable, "-m", args.module]
    return subprocess.call(command, cwd=root, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
