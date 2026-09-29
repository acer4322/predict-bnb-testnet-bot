from __future__ import annotations

"""Source-only amended provenance export for OUR directional alpha V1.1.

Reads exact eligible market IDs from ResearchFastPath and winner labels only from
the canonical ETH frozen HFT bundle cohort.json. No feature/outcome mixing.
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_fast_path_v1 import ResearchFastPath

DEFAULT_BUNDLE = ROOT / "data" / "research" / "r4_v0" / "p0_provenance_v1" / "eth_v9_frozen_holdout100_v1" / "bundle.zip"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(DEFAULT_BUNDLE))
    ap.add_argument("--output", required=True)
    ns = ap.parse_args()

    with ResearchFastPath() as fp:
        df = fp.rows(
            "our-features",
            columns=["market_id", "decision_ms", "state_seconds_left"],
            where="state_seconds_left > 180",
            include_labels=False,
        ).df()

    ids = sorted(int(x) for x in df.market_id.unique())
    bundle = Path(ns.bundle)
    with zipfile.ZipFile(bundle) as z:
        cohort = json.loads(z.read("cohort.json"))["rows"]
    by = {int(r["marketId"]): str(r.get("winner", "")).upper() for r in cohort}
    labels = {mid: by[mid] for mid in ids if by.get(mid) in {"UP", "DOWN"}}
    missing = [mid for mid in ids if mid not in labels]

    out = {
        "version": "OUR_DIRECTIONAL_WINNER_LABELS_BUNDLE_PROVENANCE_V1_1",
        "date": "2026-09-09",
        "researchOnly": True,
        "runtimeAuthority": False,
        "featureSource": "ResearchFastPath:our-features state_seconds_left>180",
        "labelSource": str(bundle.resolve()),
        "labelMember": "cohort.json",
        "eligibleMarkets": len(ids),
        "winnerLabels": len(labels),
        "missingMarkets": missing,
        "labels": [{"market_id": mid, "winner": labels[mid]} for mid in ids if mid in labels],
    }
    Path(ns.output).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ["eligibleMarkets", "winnerLabels", "missingMarkets"]}, indent=2))
    return 0 if not missing else 3


if __name__ == "__main__":
    raise SystemExit(main())
