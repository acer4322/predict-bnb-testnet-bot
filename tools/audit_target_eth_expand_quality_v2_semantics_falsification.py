from __future__ import annotations
import argparse, collections, json, sqlite3, statistics, threading, time
from pathlib import Path

EPS = 1e-9

def role(side: str, up: float, down: float) -> int:
    if up + down <= EPS or abs(up - down) <= EPS:
        return 0
    weak = "UP" if up < down else "DOWN"
    return 1 if side == weak else -1

def summary(values):
    if not values:
        return {"n": 0, "median": None, "p10": None, "p90": None}
    xs = sorted(float(x) for x in values)
    return {
        "n": len(xs),
        "median": statistics.median(xs),
        "p10": xs[max(0, int(0.10 * len(xs)) - 1)],
        "p90": xs[min(len(xs) - 1, int(0.90 * len(xs)))],
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(15):
            print(json.dumps({"heartbeat": "V2_SEMANTICS_FALSIFICATION", "ts": time.time()}), flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "V2_SEMANTICS_FALSIFICATION_START"}), flush=True)
    try:
        con = sqlite3.connect(args.db)
        con.row_factory = sqlite3.Row
        market_cols = [r["name"] for r in con.execute("pragma table_info(target_markets)")]
        parent_cols = [r["name"] for r in con.execute("pragma table_info(target_parent_orders)")]
        market_rows = list(con.execute("select * from target_markets where asset='ETH' and window_end_ms is not null"))
        market_end = {int(r["market_id"]): int(r["window_end_ms"]) for r in market_rows}
        raw = list(con.execute(
            "select parent_id,market_id,role,side,first_event_ms,average_price,shares "
            "from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"
        ))
        one = lambda sql: dict(con.execute(sql).fetchone())
        base = {
            "marketColumns": market_cols,
            "parentColumns": parent_cols,
            "ethMarkets": {
                "rows": len(market_rows),
                "distinctMarketIds": len(market_end),
                "distinctEnds": len(set(market_end.values())),
            },
            "ethParents": one(
                "select count(*) rows,count(distinct market_id) markets,count(distinct parent_id) parentIds "
                "from target_parent_orders where asset='ETH'"
            ),
            "duplicateMarketIdRows": [
                dict(r) for r in con.execute(
                    "select market_id,count(*) n,count(distinct window_end_ms) ends "
                    "from target_markets where asset='ETH' group by market_id having count(*)>1 "
                    "order by n desc limit 50"
                )
            ],
            "duplicateExactParents": one(
                "select count(*) groupsN,coalesce(sum(n-1),0) extra from ("
                "select count(*) n from target_parent_orders where asset='ETH' "
                "group by market_id,parent_id,role,side,first_event_ms,average_price,shares having count(*)>1)"
            ),
            "sameClockGroups": one(
                "select count(*) groupsN,coalesce(sum(n-1),0) extra from ("
                "select count(*) n from target_parent_orders where asset='ETH' "
                "group by market_id,first_event_ms having count(*)>1)"
            ),
            "parentsMissingMarketEnd": one(
                "select count(*) parentRows,count(distinct p.market_id) markets "
                "from target_parent_orders p left join target_markets m "
                "on m.market_id=p.market_id and m.asset='ETH' "
                "where p.asset='ETH' and (m.market_id is null or m.window_end_ms is null)"
            ),
        }
        duration_col = next((x for x in ["window_start_ms", "start_ms", "windowStartMs", "window_start"] if x in market_cols), None)
        if duration_col:
            base["windowDurations"] = [
                dict(r) for r in con.execute(
                    f"select (window_end_ms-{duration_col}) durationMs,count(*) n "
                    f"from target_markets where asset='ETH' and {duration_col} is not null "
                    "group by 1 order by n desc limit 20"
                )
            ]
        con.close()

        by_market = collections.defaultdict(list)
        for r in raw:
            by_market[int(r["market_id"])].append(r)
        ends = sorted(set(market_end.values()))
        cut1 = ends[int(len(ends) * 0.60)]
        cut2 = ends[int(len(ends) * 0.80)]
        diag = collections.Counter()
        split = collections.defaultdict(lambda: {"rows": 0, "markets": set(), "ends": set(), "tmin": None, "tmax": None})
        pair_sums = {"same": [], "opposite": []}
        delays = {"same": [], "opposite": []}
        positives = collections.Counter()
        examples = []
        label_agree = collections.Counter()
        periodic = 0

        for market_id, events in by_market.items():
            up = down = 0.0
            history = []
            end = market_end.get(market_id)
            split_name = "train" if int(end or 0) < cut1 else ("validation" if int(end or 0) < cut2 else "test")
            for i, row in enumerate(events):
                side = str(row["side"]).upper()
                t = int(row["first_event_ms"])
                qty = float(row["shares"])
                px = float(row["average_price"])
                rel = role(side, up, down)
                gap = abs(up - down)
                paid = min(qty, gap) if rel == 1 else 0.0
                crossing = rel == 1 and gap > EPS and qty >= gap - EPS
                if side == "UP":
                    up += qty
                else:
                    down += qty
                history.append({"t": t, "rel": rel, "side": side, "qty": qty, "px": px, "paid": paid})
                if not crossing:
                    continue

                diag["crossingAnchors"] += 1
                z = split[split_name]
                z["rows"] += 1
                z["markets"].add(market_id)
                z["ends"].add(end)
                z["tmin"] = t if z["tmin"] is None else min(z["tmin"], t)
                z["tmax"] = t if z["tmax"] is None else max(z["tmax"], t)

                if up > down + EPS:
                    post_dom, post_weak = "UP", "DOWN"
                elif down > up + EPS:
                    post_dom, post_weak = "DOWN", "UP"
                else:
                    post_dom = side
                    post_weak = "DOWN" if post_dom == "UP" else "UP"

                prior_expand = [x for x in history[:-1] if x["rel"] == -1 and x["side"] == post_dom]
                pe = prior_expand[-1] if prior_expand else None
                pe_t = pe["t"] if pe else None
                block = [
                    x for x in history
                    if x["rel"] == 1 and x["side"] == post_weak
                    and (pe_t is None or x["t"] > pe_t) and x["t"] <= t and x["paid"] > EPS
                ]
                diag["anchorsIncludedInV2Block"] += int(any(x is history[-1] for x in block))
                diag["anchorsWithEmptyV2Block"] += int(not block)

                future_up, future_down = float(up), float(down)
                future_repairs_v2 = []
                future_repairs_v1 = []
                found = None
                same_clock_skipped = 0
                ignored_future_composites = 0
                for future in events[i + 1:]:
                    ft = int(future["first_event_ms"])
                    if ft <= t:
                        same_clock_skipped += 1
                        continue
                    if ft - t > 30000:
                        break
                    fs = str(future["side"]).upper()
                    fq = float(future["shares"])
                    fp = float(future["average_price"])
                    fr = role(fs, future_up, future_down)
                    fg = abs(future_up - future_down)
                    fpaid = min(fq, fg) if fr == 1 else 0.0
                    if fr == -1:
                        found = {"t": ft, "side": fs, "qty": fq, "px": fp}
                        break
                    if fr == 1 and fq > fg + EPS:
                        ignored_future_composites += 1
                        if len(examples) < 20:
                            examples.append({
                                "market": market_id, "anchorT": t, "futureT": ft,
                                "anchorSide": side, "futureCompositeSide": fs,
                                "physicalQty": fq, "preFillGap": fg, "ignoredExpandOverflow": fq - fg,
                            })
                    if fr == 1:
                        future_repairs_v1.append({"side": fs, "qty": fq, "px": fp})
                    if fr == 1 and fs == post_weak and fpaid > EPS:
                        future_repairs_v2.append({"side": fs, "qty": fpaid, "px": fp})
                    if fs == "UP":
                        future_up += fq
                    else:
                        future_down += fq

                if same_clock_skipped:
                    diag["anchorsWithSameClockSkip"] += 1
                    diag["sameClockSkippedEvents"] += same_clock_skipped
                if ignored_future_composites:
                    diag["anchorsWithIgnoredFutureComposite"] += 1
                    diag["ignoredFutureComposites"] += ignored_future_composites

                if found is None:
                    diag["noFutureExpandWithin30s"] += 1
                    continue
                diag["futureExpandFound"] += 1
                side_type = "same" if found["side"] == side else "opposite"
                diag["found_" + side_type] += 1
                diag["foundOnInitialPostDominant"] += int(found["side"] == post_dom)
                diag["foundOnInitialPostWeak"] += int(found["side"] == post_weak)
                diag["foundAfterV2FutureRepair"] += int(bool(future_repairs_v2))
                diag["foundWithoutV2FutureRepair"] += int(not future_repairs_v2)
                pair_sum = px + found["px"]
                pair_sums[side_type].append(pair_sum)
                delays[side_type].append(found["t"] - t)
                v2_label = int(pair_sum <= 1.0 + EPS)
                positives[side_type] += v2_label

                v1_repairs = []
                pay_side = "DOWN" if found["side"] == "UP" else "UP"
                if side == pay_side:
                    v1_repairs.append({"qty": paid, "px": px})
                v1_repairs.extend({"qty": x["qty"], "px": x["px"]} for x in future_repairs_v1 if x["side"] == pay_side)
                total_qty = sum(x["qty"] for x in v1_repairs)
                v1_label = None
                if total_qty > EPS and min(total_qty, found["qty"]) > EPS:
                    v1_px = sum(x["qty"] * x["px"] for x in v1_repairs) / total_qty
                    v1_label = int(v1_px + found["px"] <= 1.0 + EPS)
                label_agree["bothLabeled"] += int(v1_label is not None)
                label_agree["sameLabel"] += int(v1_label is not None and v1_label == v2_label)
                label_agree["changedLabel"] += int(v1_label is not None and v1_label != v2_label)
                label_agree["v1UnlabeledV2Labeled"] += int(v1_label is None)

            periodic += 1
            if periodic % 500 == 0:
                print(json.dumps({"progressMarkets": periodic, "of": len(by_market)}), flush=True)

        split_out = {
            k: {
                "rows": v["rows"], "markets": len(v["markets"]), "ends": len(v["ends"]),
                "tmin": v["tmin"], "tmax": v["tmax"],
            }
            for k, v in split.items()
        }
        side_out = {
            k: {
                "n": len(pair_sums[k]),
                "shareOfFound": len(pair_sums[k]) / max(1, diag["futureExpandFound"]),
                "positiveRate": positives[k] / max(1, len(pair_sums[k])),
                "pairSum": summary(pair_sums[k]),
                "delayMs": summary(delays[k]),
            }
            for k in pair_sums
        }
        output = {
            "version": "TARGET_ETH_EXPAND_QUALITY_V2_SEMANTICS_FALSIFICATION_AUDIT",
            "date": "2026-09-04",
            "researchOnly": True,
            "actionAuthority": False,
            "sourceDb": str(args.db),
            "base": base,
            "cutoffs": {"trainEndExclusiveMs": cut1, "validationEndExclusiveMs": cut2},
            "split": split_out,
            "marketOverlap": {
                a + "__" + b: len(split[a]["markets"] & split[b]["markets"])
                for a in split for b in split if a < b
            },
            "endOverlap": {
                a + "__" + b: len(split[a]["ends"] & split[b]["ends"])
                for a in split for b in split if a < b
            },
            "trace": dict(diag),
            "anchorFutureSide": side_out,
            "v1VsV2AnchorLabel": dict(label_agree),
            "ignoredFutureCompositeExamples": examples,
            "boundary": [
                "read-only reconstruction",
                "no model fitting",
                "future actions used only to audit offline labels",
                "no runtime mutation",
                "no 8781",
            ],
        }
        Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "output": args.output, "trace": output["trace"], "anchorFutureSide": side_out}, ensure_ascii=False), flush=True)
    finally:
        stop.set()

if __name__ == "__main__":
    main()
