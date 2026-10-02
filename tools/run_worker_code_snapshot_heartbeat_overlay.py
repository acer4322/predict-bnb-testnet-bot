from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code-bundle", required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--overlay", required=True)
    args, rest = parser.parse_known_args()

    started = time.time()
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(10):
            print(
                json.dumps(
                    {
                        "heartbeat": "CODE_SNAPSHOT_OVERLAY",
                        "elapsedSeconds": round(time.time() - started, 1),
                    }
                ),
                flush=True,
            )

    threading.Thread(target=heartbeat, daemon=True).start()
    print(
        json.dumps(
            {
                "heartbeat": "CODE_SNAPSHOT_OVERLAY_START",
                "entry": args.entry,
            }
        ),
        flush=True,
    )

    root = Path(tempfile.mkdtemp(prefix="btc5m_code_snapshot_overlay_"))
    try:
        with zipfile.ZipFile(args.code_bundle) as archive:
            archive.extractall(root)
        entry = root / args.entry
        entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.overlay, entry)
        os.chdir(root)
        sys.path.insert(0, str(root))
        sys.argv = [str(entry), *rest]
        runpy.run_path(str(entry), run_name="__main__")
    finally:
        stop.set()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
