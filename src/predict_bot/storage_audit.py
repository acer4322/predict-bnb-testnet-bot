from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def _sqlite_stats(path: Path) -> tuple[int | None, int | None, int | None]:
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
            freelist = int(db.execute("PRAGMA freelist_count").fetchone()[0])
            return page_size, page_count, freelist
        finally:
            db.close()
    except Exception:
        return None, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast audit of BTC 5M Lab data files")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    rows: list[tuple[int, Path]] = []
    if DATA.exists():
        for path in DATA.iterdir():
            if not path.is_file():
                continue
            try:
                rows.append((path.stat().st_size, path))
            except OSError:
                pass
    rows.sort(reverse=True, key=lambda item: item[0])

    print(f"Data directory: {DATA}")
    print(f"Total files: {len(rows)}")
    print()
    print(f"{'SIZE':>12}  {'RECLAIMABLE*':>14}  FILE")
    print("-" * 78)
    for size, path in rows[: max(1, args.top)]:
        reclaim_text = "-"
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            page_size, _page_count, freelist = _sqlite_stats(path)
            if page_size is not None and freelist is not None:
                reclaim_text = _human(page_size * freelist)
        print(f"{_human(size):>12}  {reclaim_text:>14}  {path.name}")

    total = sum(size for size, _ in rows)
    print("-" * 78)
    print(f"TOTAL: {_human(total)}")
    print()
    print("* RECLAIMABLE is SQLite freelist space already deleted internally.")
    print("  SQLite normally reuses it but does not return it to Windows until a VACUUM/rebuild.")
    print("  Do not VACUUM a huge DB on a nearly-full disk; it can require substantial temporary space.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
