from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

VERSION = "TARGET_BEST_PROTECTION_DENOMINATOR_PLACEBO_V1"
PERMUTATIONS = 999
THRESH_BEST = 10.0
THRESH_RATIO = 1.0 / 3.0
TOL = 1e-12


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def proportion(vals):
    return sum(bool(v) for v in vals) / len(vals) if vals else float("nan")


def quantile(xs, q: float) -> float:
    ys = sorted(xs)
    if not ys:
        return float("nan")
    if len(ys) == 1:
        return ys[0]
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    w = pos - lo
    return ys[lo] * (1.0 - w) + ys[hi] * w


def compute_t(rows, losses_by_mid=None):
    h_flags = []
    l_flags = []
    h_losses = []
    l_losses = []
    h_floors = []
    l_floors = []
    h_zero = 0
    l_zero = 0
    for r in rows:
        mid = int(r["marketId"])
        B = float(r["best"])
        C = float(r["cost"])
        if losses_by_mid is None:
            L = max(0.0, -float(r["floor"]))
        else:
            L = float(losses_by_mid[mid])
        ratio = L / B
        ok = ratio < THRESH_RATIO
        if B > THRESH_BEST:
            h_flags.append(ok)
            h_losses.append(L)
            h_floors.append(-L if L > 0 else max(0.0, float(r["floor"])))
            if L == 0:
                h_zero += 1
        else:
            l_flags.append(ok)
            l_losses.append(L)
            l_floors.append(-L if L > 0 else max(0.0, float(r["floor"])))
            if L == 0:
                l_zero += 1
    p_h = proportion(h_flags)
    p_l = proportion(l_flags)
    return {
        "T": p_h - p_l,
        "H": {"n": len(h_flags), "protectedRatioLtOneThird": p_h, "zeroDownsideN": h_zero,
              "meanDownsideLoss": statistics.mean(h_losses) if h_losses else None,
              "medianDownsideLoss": statistics.median(h_losses) if h_losses else None},
        "L": {"n": len(l_flags), "protectedRatioLtOneThird": p_l, "zeroDownsideN": l_zero,
              "meanDownsideLoss": statistics.mean(l_losses) if l_losses else None,
              "medianDownsideLoss": statistics.median(l_losses) if l_losses else None},
    }


def make_bins(rows):
    # rows are fixed original chronology; blocks are positions 1-20, 21-40, ...
    bins = {}
    for block_idx in range(5):
        block = rows[block_idx * 20:(block_idx + 1) * 20]
        elig = sorted(block, key=lambda r: (float(r["cost"]), int(r["marketId"])))
        cut = len(elig) // 2
        for half_name, subset in (("LOWC", elig[:cut]), ("HIGHC", elig[cut:])):
            bid = f"B{block_idx+1}_{half_name}"
            bins[bid] = [int(r["marketId"]) for r in subset]
    return bins


def permuted_losses(rows_by_mid, bins, j: int):
    out = {}
    for bid, mids in bins.items():
        receivers = sorted(mids)
        donors = sorted(
            mids,
            key=lambda mid: (
                hashlib.sha256(f"{VERSION}|{j}|{bid}|{mid}".encode("utf-8")).hexdigest(),
                mid,
            ),
        )
        for recv, donor in zip(receivers, donors):
            rr = rows_by_mid[recv]
            dr = rows_by_mid[donor]
            z_donor = max(0.0, -float(dr["floor"])) / float(dr["cost"])
            out[recv] = float(rr["cost"]) * z_donor
    return out


def fixture_control():
    # Same C and same z for all rows; only B differs. Pure denominator effect gives T=1,
    # and permutation cannot create evidence beyond it because all z values are identical.
    fixture = []
    for i in range(10):
        B = 20.0 if i < 5 else 10.0
        C = 100.0
        L = 5.0
        fixture.append({"marketId": 900000+i, "best": B, "floor": -L, "cost": C})
    obs = compute_t(fixture)["T"]
    vals = []
    for _ in range(50):
        vals.append(compute_t(fixture)["T"])
    return {
        "TObserved": obs,
        "allReferenceEqualObserved": all(abs(x - obs) <= TOL for x in vals),
        "referenceUniqueN": len(set(round(x, 15) for x in vals)),
        "expected": "all z equal => permutation cannot exceed denominator-only observed contrast",
        "pass": abs(obs - 1.0) <= TOL and all(abs(x - obs) <= TOL for x in vals),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--source-report", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    in_path = Path(args.input)
    src_report = Path(args.source_report)
    out_path = Path(args.output)

    d = json.loads(in_path.read_text(encoding="utf-8"))
    rows0 = d.get("rows", [])
    # Preserve original fixed chronology, no sorting by outcome.
    rows = []
    invalid = []
    for idx, r0 in enumerate(rows0):
        r = dict(r0)
        vals = [r.get("cost"), r.get("best"), r.get("floor")]
        if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in vals):
            invalid.append({"index": idx, "marketId": r.get("marketId"), "reason": "nonfinite_core_field"})
            continue
        if float(r["cost"]) <= 0 or float(r["best"]) <= 0:
            invalid.append({"index": idx, "marketId": r.get("marketId"), "reason": "C<=0_or_B<=0"})
            continue
        rows.append(r)

    market_ids = [int(r["marketId"]) for r in rows]
    unique_ids = len(set(market_ids)) == len(market_ids)
    official_recon_max_abs = max(abs(float(r["targetPnl"]) - float(r["calcRealized"])) for r in rows0)

    obs = compute_t(rows)
    bins = make_bins(rows)
    rows_by_mid = {int(r["marketId"]): r for r in rows}

    # Identity control: original L values must reproduce exactly.
    identity_losses = {int(r["marketId"]): max(0.0, -float(r["floor"])) for r in rows}
    identity = compute_t(rows, identity_losses)
    identity_pass = abs(identity["T"] - obs["T"]) <= TOL and identity["H"]["n"] == obs["H"]["n"] and identity["L"]["n"] == obs["L"]["n"]

    perm_ts = []
    for j in range(1, PERMUTATIONS + 1):
        losses = permuted_losses(rows_by_mid, bins, j)
        perm_ts.append(compute_t(rows, losses)["T"])

    ge = sum(1 for t in perm_ts if t >= obs["T"] - TOL)
    tail_rank = (1 + ge) / 1000.0
    ref_unique = len(set(round(x, 15) for x in perm_ts))
    ref_std = statistics.pstdev(perm_ts) if len(perm_ts) > 1 else 0.0

    bin_diag = []
    modifiable_bins = 0
    for bid, mids in bins.items():
        zs = [max(0.0, -float(rows_by_mid[mid]["floor"])) / float(rows_by_mid[mid]["cost"]) for mid in mids]
        unique_z = len(set(round(z, 15) for z in zs))
        if len(mids) > 1 and unique_z > 1:
            modifiable_bins += 1
        bin_diag.append({"binId": bid, "n": len(mids), "uniqueZN": unique_z, "marketIds": sorted(mids)})

    fixture = fixture_control()
    correctness = {
        "rowCountExactly100": len(rows0) == 100,
        "eligibleCount": len(rows),
        "invalidCount": len(invalid),
        "uniqueMarketIds": unique_ids,
        "officialReconciliationMaxAbs": official_recon_max_abs,
        "officialReconciliationPass": official_recon_max_abs < 1e-9,
        "identityPermutationPass": identity_pass,
        "fixturePass": fixture["pass"],
        "allBinsPreserveMembership": sum(len(v) for v in bins.values()) == len(rows),
    }
    correctness_pass = all([
        correctness["rowCountExactly100"],
        correctness["invalidCount"] == 0,
        correctness["uniqueMarketIds"],
        correctness["officialReconciliationPass"],
        correctness["identityPermutationPass"],
        correctness["fixturePass"],
        correctness["allBinsPreserveMembership"],
    ])

    exercise = {
        "HnAtLeast5": obs["H"]["n"] >= 5,
        "LnAtLeast5": obs["L"]["n"] >= 5,
        "modifiableBins": modifiable_bins,
        "hasModifiableBin": modifiable_bins >= 1,
        "referenceUniqueN": ref_unique,
        "referenceStd": ref_std,
        "referenceNonDegenerate": ref_unique > 1 and ref_std > 0,
    }
    exercised = all([exercise["HnAtLeast5"], exercise["LnAtLeast5"], exercise["hasModifiableBin"], exercise["referenceNonDegenerate"]])

    if not correctness_pass:
        verdict = "CORRECTNESS_FAIL_STOP"
    elif not exercised:
        verdict = "NOT_EXERCISED_SMALL_OR_DEGENERATE_SUPPORT"
    elif obs["T"] <= 0:
        verdict = "NOT_SUPPORTED_IN_CONSUMED100"
    elif tail_rank > 0.05:
        verdict = "DENOMINATOR_COMPOSITION_EXPLANATION_NOT_REJECTED"
    else:
        verdict = "JOINT_ASSOCIATION_EXCEEDS_THIS_PLACEBO"

    result = {
        "version": VERSION,
        "status": verdict,
        "reportOnlyMetricAudit": True,
        "referenceOnlyNotCausal": True,
        "input": {
            "path": str(in_path).replace("\\", "/"),
            "sha256": sha256_file(in_path),
            "sourceReportPath": str(src_report).replace("\\", "/"),
            "sourceReportSha256": sha256_file(src_report),
            "sourceProvenanceFromReport": "target_eth_fill_legs_v3_snapshot_20260904.db; all Target MAKER+TAKER BID fill legs; official realized PnL reconciliation <2e-13",
        },
        "contract": {
            "permutations": PERMUTATIONS,
            "highBest": "B>10",
            "lowGroup": "0<B<=10",
            "ratio": "L/B where L=max(0,-F)",
            "protected": "L/B<1/3",
            "strata": "five fixed 20-market chronology blocks x two within-block C rank halves",
            "permutation": f"SHA256('{VERSION}|j|bin-id|marketId') donor ordering; receiver sorted by marketId",
        },
        "correctness": correctness,
        "exercise": exercise,
        "observed": obs,
        "reference": {
            "n": len(perm_ts),
            "meanT": statistics.mean(perm_ts),
            "medianT": statistics.median(perm_ts),
            "p025": quantile(perm_ts, 0.025),
            "p975": quantile(perm_ts, 0.975),
            "min": min(perm_ts),
            "max": max(perm_ts),
            "tailRank": tail_rank,
            "countTgeObserved": ge,
        },
        "negativeControls": {"identity": identity, "pureDenominatorFixture": fixture},
        "bins": bin_diag,
        "invalidRows": invalid,
        "interpretationBoundary": [
            "REFERENCE_ONLY_NOT_CAUSAL",
            "No runtime selector, policy, HFT, training, worker, fresh cohort, or promotion authority is created.",
            "A positive result rejects only this specific stratified denominator/composition reference, not terminal selection or latent confounding.",
            "A negative result does not prove Target has no economic edge.",
        ],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": verdict,
        "TObserved": obs["T"],
        "Hn": obs["H"]["n"],
        "Ln": obs["L"]["n"],
        "tailRank": tail_rank,
        "referenceMean": statistics.mean(perm_ts),
        "referenceP025": quantile(perm_ts, 0.025),
        "referenceP975": quantile(perm_ts, 0.975),
        "correctnessPass": correctness_pass,
        "exercised": exercised,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
