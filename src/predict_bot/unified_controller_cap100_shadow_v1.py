from __future__ import annotations

import bisect
import json
import math
import os
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import joblib
import numpy as np

from .core import taker_fee
from .strategy_target_compare_recorder_v1 import StrategyTargetCompareRecorder

ROOT = Path(__file__).resolve().parents[2]
VERSION = "UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER"
HOST = os.environ.get("UNIFIED_CONTROLLER_CAP100_HOST", "127.0.0.1")
PORT = int(os.environ.get("UNIFIED_CONTROLLER_CAP100_PORT", "8786"))
SOURCE_URL = os.environ.get("UNIFIED_CONTROLLER_PUBLIC_SOURCE_URL", "http://127.0.0.1:8783/state")
BOOK_DB = Path(os.environ.get("UNIFIED_CONTROLLER_BOOK_DB", ROOT / "data" / "wallet_maker_book_inference.db"))
MODEL_DIR = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
POLL_SECONDS = max(0.20, float(os.environ.get("UNIFIED_CONTROLLER_POLL_SECONDS", "0.40")))
SOURCE_MAX_AGE_MS = max(1_000, int(os.environ.get("UNIFIED_CONTROLLER_SOURCE_MAX_AGE_MS", "3000")))
BOOK_MAX_AGE_MS = max(500, int(os.environ.get("UNIFIED_CONTROLLER_BOOK_MAX_AGE_MS", "2000")))
DECISION_MIN_INTERVAL_MS = max(750, int(os.environ.get("UNIFIED_CONTROLLER_DECISION_MIN_INTERVAL_MS", "900")))
SHARES = 18.0
FEE_BPS = 200
GRID = 0.01
MIN_PRICE = 0.06
MAX_PAIR_PRICE_SUM = 0.99
REFILL_COOLDOWN_MS = 1000
RNG_SEED = 20260820
EPS = 1e-9
CAP_TOTAL_USDT = 100.0
CAP_TAKER_RESERVE_USDT = 20.0
CAP_MAKER_BUDGET_USDT = CAP_TOTAL_USDT - CAP_TAKER_RESERVE_USDT

# R2 keeps the frozen R1 Maker/Taker stack and adds only the final-holdout-validated residual arbitration wiring.
# No quote-zone/execution-hazard experiment is action-driving here.
ARTIFACTS = {
    "maker_up": MODEL_DIR / "target_general_maker_up_core_book_v1.joblib",
    "maker_down": MODEL_DIR / "target_general_maker_down_core_book_v1.joblib",
    "corrective_up": MODEL_DIR / "target_maker_student_state_corrective_up_v1.joblib",
    "corrective_down": MODEL_DIR / "target_maker_student_state_corrective_down_v1.joblib",
    "burst_up": MODEL_DIR / "target_maker_up_burst_multi_v1.joblib",
    "burst_down": MODEL_DIR / "target_maker_down_burst_multi_v1.joblib",
    "passive_repair": MODEL_DIR / "post_excursion_passive_repair_1s_ebm_v1.joblib",
    "residual_passive_repair": MODEL_DIR / "residual_passive_repair_1s_hgb_fast_v0.joblib",
    "taker_1s": MODEL_DIR / "frozen_hazard_1s_full.joblib",
    "taker_3s": MODEL_DIR / "frozen_hazard_3s_full.joblib",
    "side": MODEL_DIR / "frozen_side_full.joblib",
    "effect": MODEL_DIR / "frozen_effect_full.joblib",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def number(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def phase_from_seconds(seconds_left: float | None) -> str:
    if seconds_left is None:
        return "UNKNOWN"
    if seconds_left > 240:
        return "OPEN"
    if seconds_left > 60:
        return "MID"
    return "TAIL"


def simple3(snapshot: dict[str, Any]) -> dict[str, Any]:
    up_mid = number(snapshot.get("predictUpMid"))
    spot_bps = number(snapshot.get("spotMinusStrikeBps"))
    chain_bps = number(snapshot.get("chainlinkMinusStrikeBps"))
    votes: list[str] = []
    components: dict[str, Any] = {}
    for name, value, split in (("predict", up_mid, 0.5), ("spot", spot_bps, 0.0), ("chainlink", chain_bps, 0.0)):
        side = None if value is None or abs(value - split) <= 1e-15 else ("UP" if value > split else "DOWN")
        components[name] = {"value": value, "side": side}
        if side:
            votes.append(side)
    up = votes.count("UP")
    down = votes.count("DOWN")
    side = "UP" if up >= 2 else "DOWN" if down >= 2 else "NEUTRAL"
    return {"proxy": "SIMPLE3", "side": side, "strength": max(up, down) if side != "NEUTRAL" else 0, "components": components}


def _dec(blob: bytes | None) -> Any:
    return json.loads(zlib.decompress(blob).decode("utf-8")) if blob else None


def _top3(side: dict[float, float], *, reverse: bool) -> float:
    return float(sum(v for _, v in sorted(side.items(), key=lambda kv: kv[0], reverse=reverse)[:3]))


def _apply_changes(book: dict[str, dict[float, float]], changes: Any) -> None:
    if not isinstance(changes, dict):
        return
    for key in ("bids", "asks"):
        for ch in changes.get(key, []) or []:
            p = float(ch["price"])
            after = float(ch["after"])
            if after <= 1e-12:
                book[key].pop(p, None)
            else:
                book[key][p] = after


def outcome_book(book: dict[str, dict[float, float]], dominant: str | None) -> dict[str, float] | None:
    bids, asks = book.get("bids", {}), book.get("asks", {})
    if not bids or not asks:
        return None
    bb, ba = max(bids), min(asks)
    bbd, bad = float(bids[bb]), float(asks[ba])
    up_bid, up_ask = float(bb), float(ba)
    down_bid, down_ask = 1.0 - float(ba), 1.0 - float(bb)
    out = {
        "up_bid": up_bid,
        "up_ask": up_ask,
        "up_spread_ticks": (up_ask - up_bid) / GRID,
        "up_bid_depth": bbd,
        "up_ask_depth": bad,
        "up_top3_bid_depth": _top3(bids, reverse=True),
        "down_bid": down_bid,
        "down_ask": down_ask,
        "down_spread_ticks": (down_ask - down_bid) / GRID,
        "down_bid_depth": bad,
        "down_ask_depth": bbd,
        "down_top3_bid_depth": _top3(asks, reverse=False),
        "pair_bid_edge": 1.0 - up_bid - down_bid,
        "pair_ask_edge": 1.0 - up_ask - down_ask,
    }
    if dominant == "UP":
        db, da, ob, oa = up_bid, up_ask, down_bid, down_ask
    elif dominant == "DOWN":
        db, da, ob, oa = down_bid, down_ask, up_bid, up_ask
    else:
        db = da = ob = oa = math.nan
    out.update({
        "dominant_bid": db,
        "dominant_ask": da,
        "opposite_bid": ob,
        "opposite_ask": oa,
        "dominant_opp_bid_pair_edge": (1.0 - db - ob) if math.isfinite(db) and math.isfinite(ob) else math.nan,
    })
    return out


def _ratio(abs_net: float, gross: float) -> float:
    return abs_net / gross if gross > 1e-12 else 0.0


def _coverage(up: float, down: float) -> float:
    gross = up + down
    return 2.0 * min(up, down) / gross if gross > 1e-12 else 0.0


def _avg(cost: float, shares: float) -> float:
    return cost / shares if shares > 1e-12 else math.nan


class Inventory:
    def __init__(self) -> None:
        self.maker_up = self.maker_down = self.taker_up = self.taker_down = 0.0
        self.maker_up_cost = self.maker_down_cost = 0.0
        self.taker_up_cost = self.taker_down_cost = 0.0
        self.cash = 0.0
        self.events: list[dict[str, Any]] = []
        self.times: list[int] = []

    def reset(self) -> None:
        self.__init__()

    def apply(self, e: dict[str, Any]) -> None:
        role, side = str(e["role"]), str(e["side"])
        shares, price = float(e["shares"]), float(e["price"])
        if role == "MAKER":
            if side == "UP":
                self.maker_up += shares; self.maker_up_cost += price * shares
            else:
                self.maker_down += shares; self.maker_down_cost += price * shares
        else:
            if side == "UP":
                self.taker_up += shares; self.taker_up_cost += price * shares
            else:
                self.taker_down += shares; self.taker_down_cost += price * shares
        self.cash -= price * shares
        self.events.append(dict(e))
        self.times.append(int(e["event_ms"]))

    def _recent(self, now: int, role: str, window: int) -> list[dict[str, Any]]:
        lo = bisect.bisect_right(self.times, now - window)
        hi = bisect.bisect_right(self.times, now)
        return [e for e in self.events[lo:hi] if e["role"] == role]

    def _last_age(self, now: int, role: str, side: str | None = None) -> float:
        for e in reversed(self.events):
            if e["role"] == role and (side is None or e["side"] == side):
                return float(now - int(e["event_ms"]))
        return math.nan

    def _streak(self, role: str) -> float:
        side = None; n = 0
        for e in reversed(self.events):
            if e["role"] != role:
                continue
            if side is None:
                side = e["side"]
            if e["side"] != side:
                break
            n += 1
        return float(n)

    def _absnet_at(self, when: int, role: str | None = None) -> float:
        up = down = 0.0
        for e in self.events:
            if int(e["event_ms"]) > when:
                break
            if role is not None and e["role"] != role:
                continue
            if e["side"] == "UP": up += float(e["shares"])
            else: down += float(e["shares"])
        return abs(up - down)

    def features(self, now: int) -> dict[str, float]:
        mu, md, tu, td = self.maker_up, self.maker_down, self.taker_up, self.taker_down
        cu, cd = mu + tu, md + td
        mn, tn, cn = mu-md, tu-td, cu-cd
        mg, tg, cg = mu+md, tu+td, cu+cd
        ma, ta, ca = abs(mn), abs(tn), abs(cn)
        floor = min(self.cash + cu, self.cash + cd)
        best = max(self.cash + cu, self.cash + cd)
        r_m1 = self._recent(now, "MAKER", 1000); r_m5 = self._recent(now, "MAKER", 5000); r_m10 = self._recent(now, "MAKER", 10000)
        r_t1 = self._recent(now, "TAKER", 1000); r_t5 = self._recent(now, "TAKER", 5000); r_t10 = self._recent(now, "TAKER", 10000)
        mau, mad = _avg(self.maker_up_cost, mu), _avg(self.maker_down_cost, md)
        tau, tad = _avg(self.taker_up_cost, tu), _avg(self.taker_down_cost, td)
        cau, cad = _avg(self.maker_up_cost+self.taker_up_cost, cu), _avg(self.maker_down_cost+self.taker_down_cost, cd)
        return {
            "maker_gross": mg, "maker_net": mn, "maker_abs_net": ma, "maker_imbalance_ratio": _ratio(ma, mg), "maker_paired_coverage": _coverage(mu, md),
            "taker_gross": tg, "taker_net": tn, "taker_abs_net": ta, "taker_imbalance_ratio": _ratio(ta, tg), "taker_paired_coverage": _coverage(tu, td),
            "combined_gross": cg, "combined_net": cn, "combined_abs_net": ca, "combined_imbalance_ratio": _ratio(ca, cg), "combined_paired_coverage": _coverage(cu, cd),
            "worst_case_floor": floor, "best_case_pnl": best, "abs_payoff_gap": abs(cu-cd),
            "maker_taker_net_same_sign": float(1 if mn*tn>0 else 0 if mn*tn==0 else -1),
            "last_maker_age_ms": self._last_age(now,"MAKER"), "last_taker_age_ms": self._last_age(now,"TAKER"),
            "last_maker_up_age_ms": self._last_age(now,"MAKER","UP"), "last_maker_down_age_ms": self._last_age(now,"MAKER","DOWN"),
            "last_taker_up_age_ms": self._last_age(now,"TAKER","UP"), "last_taker_down_age_ms": self._last_age(now,"TAKER","DOWN"),
            "maker_fills_1s": float(len(r_m1)), "maker_fills_5s": float(len(r_m5)), "maker_fills_10s": float(len(r_m10)),
            "taker_fills_1s": float(len(r_t1)), "taker_fills_5s": float(len(r_t5)), "taker_fills_10s": float(len(r_t10)),
            "maker_shares_5s": sum(float(e['shares']) for e in r_m5), "maker_shares_10s": sum(float(e['shares']) for e in r_m10),
            "taker_shares_5s": sum(float(e['shares']) for e in r_t5), "taker_shares_10s": sum(float(e['shares']) for e in r_t10),
            "maker_side_streak": self._streak("MAKER"), "taker_side_streak": self._streak("TAKER"),
            "combined_absnet_change_10s": ca - self._absnet_at(now-10000, None),
            "maker_absnet_change_10s": ma - self._absnet_at(now-10000, "MAKER"),
            "maker_up_avg_price": mau, "maker_down_avg_price": mad, "maker_avg_pair_edge": 1-mau-mad if math.isfinite(mau) and math.isfinite(mad) else math.nan,
            "taker_up_avg_price": tau, "taker_down_avg_price": tad, "taker_avg_pair_edge": 1-tau-tad if math.isfinite(tau) and math.isfinite(tad) else math.nan,
            "combined_up_avg_price": cau, "combined_down_avg_price": cad, "combined_avg_pair_edge": 1-cau-cad if math.isfinite(cau) and math.isfinite(cad) else math.nan,
            "_combined_net": cn,
        }


class PublicBookTailer:
    """Read only maker_book_inference_updates. Never queries Target inference/wallet tables."""
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma query_only=on")
        self.market_id: int | None = None
        self.last_id = 0
        self.last_source_ms: int | None = None
        self.book: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}

    def close(self) -> None:
        self.db.close()

    def reset(self, market_id: int, up_to_ms: int) -> bool:
        self.market_id = int(market_id)
        self.last_id = 0
        self.last_source_ms = None
        self.book = {"bids": {}, "asks": {}}
        row = self.db.execute(
            """select id,source_timestamp_ms,native_bids_z,native_asks_z
               from maker_book_inference_updates
               where market_id=? and is_checkpoint=1 and source_timestamp_ms<=?
               order by id desc limit 1""",
            (int(market_id), int(up_to_ms)),
        ).fetchone()
        if row is None:
            return False
        self.book = {
            "bids": {float(k): float(v) for k, v in (_dec(row["native_bids_z"]) or {}).items()},
            "asks": {float(k): float(v) for k, v in (_dec(row["native_asks_z"]) or {}).items()},
        }
        self.last_id = int(row["id"])
        self.last_source_ms = int(row["source_timestamp_ms"])
        return True

    def advance(self, market_id: int, up_to_ms: int, on_changes: Any = None) -> bool:
        if self.market_id != int(market_id) or not self.book["bids"] or not self.book["asks"]:
            if not self.reset(market_id, up_to_ms):
                return False
        rows = self.db.execute(
            """select id,source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z
               from maker_book_inference_updates
               where market_id=? and id>? and source_timestamp_ms<=?
               order by id""",
            (int(market_id), int(self.last_id), int(up_to_ms)),
        ).fetchall()
        for row in rows:
            if int(row["is_checkpoint"]):
                self.book = {
                    "bids": {float(k): float(v) for k, v in (_dec(row["native_bids_z"]) or {}).items()},
                    "asks": {float(k): float(v) for k, v in (_dec(row["native_asks_z"]) or {}).items()},
                }
            else:
                changes = _dec(row["changes_z"]) or {}
                if on_changes is not None:
                    on_changes(changes, int(row["source_timestamp_ms"]))
                _apply_changes(self.book, changes)
            self.last_id = int(row["id"])
            self.last_source_ms = int(row["source_timestamp_ms"])
        return bool(self.book["bids"] and self.book["asks"])


@dataclass
class PaperOrder:
    id: str
    side: str
    price_tick: int
    price: float
    shares: float
    placed_at_ms: int
    placed_snapshot_ns: int
    initial_depth: float
    native_side: str
    native_price: float
    occupied_before: bool
    cum_depletion: float = 0.0
    any_depletion: bool = False


def _load_artifacts() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, path in ARTIFACTS.items():
        if not path.exists():
            raise FileNotFoundError(f"missing promoted-controller artifact: {path}")
        bundle = joblib.load(path)
        if not isinstance(bundle, dict) or bundle.get("model") is None or not bundle.get("features"):
            raise RuntimeError(f"invalid promoted-controller artifact: {path}")
        out[key] = bundle
    return out


def _fast_binary(bundle: dict[str, Any]):
    model = bundle["model"]
    features = list(bundle["features"])
    if not hasattr(model, "_bin_mapper"):
        return lambda raw: _binary_prob(bundle, raw)
    known, fmap = model._bin_mapper.make_known_categories_bitsets()
    trees = [item[0] for item in model._predictors]
    baseline = float(model._baseline_prediction[0, 0])
    def predict(raw: dict[str, Any]) -> float:
        x = np.asarray([[float(raw.get(f, math.nan)) if raw.get(f) is not None else math.nan for f in features]], dtype=float)
        z = baseline
        for tree in trees:
            z += float(tree.predict(x, known_cat_bitsets=known, f_idx_map=fmap, n_threads=1)[0])
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)
    return predict


def _array(bundle: dict[str, Any], raw: dict[str, Any]) -> np.ndarray:
    vals = []
    for feature in bundle["features"]:
        value = raw.get(str(feature), math.nan)
        try:
            vals.append(float(value) if value is not None else math.nan)
        except Exception:
            vals.append(math.nan)
    return np.asarray([vals], dtype=float)


def _binary_prob(bundle: dict[str, Any], raw: dict[str, Any]) -> float:
    probs = bundle["model"].predict_proba(_array(bundle, raw))[0]
    classes = list(bundle["model"].classes_)
    idx = classes.index(1) if 1 in classes else classes.index("1")
    return float(probs[idx])


def _class_probs(bundle: dict[str, Any], raw: dict[str, Any]) -> tuple[list[str], np.ndarray]:
    probs = np.asarray(bundle["model"].predict_proba(_array(bundle, raw))[0], dtype=float)
    return [str(x) for x in bundle["model"].classes_], probs


def placement_features(placements: list[dict[str, Any]], cp: int) -> dict[str, float]:
    xs = [x for x in placements if int(x["at_ms"]) <= cp]
    if not xs:
        return {"last_place_age_ms":math.nan,"last_up_place_age_ms":math.nan,"last_down_place_age_ms":math.nan,"placements_1s":0.,"placements_5s":0.,"placements_10s":0.,"up_placements_5s":0.,"down_placements_5s":0.,"up_placements_10s":0.,"down_placements_10s":0.,"placement_side_balance_5s":0.,"placement_side_balance_10s":0.,"placement_side_streak":0.}
    last = xs[-1]; lu = ld = None; streak = 0; ls = str(last["side"])
    for x in reversed(xs):
        if x["side"] == "UP" and lu is None: lu = int(x["at_ms"])
        if x["side"] == "DOWN" and ld is None: ld = int(x["at_ms"])
        if x["side"] == ls: streak += 1
        elif streak: break
    def cnt(window: int) -> tuple[int,int,int]:
        z = [x for x in xs if int(x["at_ms"]) > cp-window]
        u = sum(x["side"] == "UP" for x in z); d = len(z)-u
        return len(z), u, d
    n1,_,_ = cnt(1000); n5,u5,d5 = cnt(5000); n10,u10,d10 = cnt(10000)
    bal = lambda u,d: (u-d)/(u+d) if u+d else 0.0
    return {"last_place_age_ms":float(cp-int(last["at_ms"])),"last_up_place_age_ms":float(cp-lu) if lu is not None else math.nan,"last_down_place_age_ms":float(cp-ld) if ld is not None else math.nan,"placements_1s":float(n1),"placements_5s":float(n5),"placements_10s":float(n10),"up_placements_5s":float(u5),"down_placements_5s":float(d5),"up_placements_10s":float(u10),"down_placements_10s":float(d10),"placement_side_balance_5s":bal(u5,d5),"placement_side_balance_10s":bal(u10,d10),"placement_side_streak":float(streak)}


def _residual_raw(raw: dict[str, Any]) -> dict[str, Any]:
    # Must match the frozen offline V7 final-holdout candidate exactly.
    # Placement features not present in raw remain NaN through _array; do not silently change this in forward R2.
    out = dict(raw)
    mg = float(out.get("maker_gross", 0.0) or 0.0)
    cg = float(out.get("combined_gross", 0.0) or 0.0)
    out["maker_coverage_gap"] = 1.0 - float(out.get("maker_paired_coverage", 0.0) or 0.0)
    out["combined_coverage_gap"] = 1.0 - float(out.get("combined_paired_coverage", 0.0) or 0.0)
    out["floor_per_maker_gross"] = float(out.get("worst_case_floor", 0.0) or 0.0) / mg if abs(mg) > EPS else math.nan
    out["floor_per_combined_gross"] = float(out.get("worst_case_floor", 0.0) or 0.0) / cg if abs(cg) > EPS else math.nan
    out["absnet_per_maker_gross"] = float(out.get("maker_abs_net", 0.0) or 0.0) / mg if abs(mg) > EPS else math.nan
    out["maker_absnet_velocity_10s_per_gross"] = float(out.get("maker_absnet_change_10s", 0.0) or 0.0) / mg if abs(mg) > EPS else math.nan
    out["recent_maker_share_rate_10s"] = float(out.get("maker_shares_10s", 0.0) or 0.0) / 10.0
    return out


class UnifiedControllerCap100ShadowV1:
    """Frozen R2 residual-arbitration forward paper controller.

    Runtime inputs: 8783 public snapshot, 8778 public book updates, and this process' own paper orders/fills only.
    Target events, Target inventory, Target winner, and Target performance are never read by this runtime.
    """
    def __init__(self) -> None:
        if os.environ.get("PREDICT_LIVE_ENABLED", "false").lower() not in {"0","false","no","off",""}:
            raise RuntimeError("8786 CAP100 shadow refuses to start while PREDICT_LIVE_ENABLED is true")
        self.started_at_ms = now_ms()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.http = httpx.Client(timeout=httpx.Timeout(2.0, connect=0.5), trust_env=False)
        self.recorder = StrategyTargetCompareRecorder()
        self.book = PublicBookTailer(BOOK_DB)
        self.models = _load_artifacts()
        self.p_maker_up_base = _fast_binary(self.models["maker_up"])
        self.p_maker_down_base = _fast_binary(self.models["maker_down"])
        self.p_taker1 = _fast_binary(self.models["taker_1s"])
        self.p_taker3 = _fast_binary(self.models["taker_3s"])
        self.current_market_id: int | None = None
        self.excluded_deployment_market_id: int | None = None
        self.active = False
        self.source_ready = False
        self.book_ready = False
        self.source_wait_reason: str | None = "STARTING"
        self.source_health: dict[str, Any] = {}
        self.last_snapshot_ms: int | None = None
        self.last_eval_snapshot_ms: int | None = None
        self.last_loop_ms: int | None = None
        self.last_error: str | None = None
        self.last_decision: dict[str, Any] | None = None
        self.sequence = 0
        self.inventory = Inventory()
        self.orders: dict[tuple[str,int], PaperOrder] = {}
        self.last_closed: dict[tuple[str,int], int] = {}
        self.placements: list[dict[str, Any]] = []
        self.episode: dict[str, Any] | None = None
        self.readiness = False
        self.last_taker_ms = -10**18
        self.rng = np.random.default_rng(RNG_SEED)
        self.residual_rng = np.random.default_rng(RNG_SEED + 7717)
        self.current_metrics: dict[str, int] = {}
        self.maker_spent_notional = 0.0
        self.taker_spent_notional = 0.0
        self.taker_fee_spent = 0.0
        self.run_metrics = {"marketsStarted":0,"decisions":0,"makerPlacements":0,"makerFills":0,"takerFills":0,"excursions":0,"passivePrioritySteps":0,"unresolvedGuardEntries":0,"residualWakes":0,"residualRecoveries":0,"burstPlacements":0,"makerCapBlocks":0,"takerCapBlocks":0}
        self._reset_metric_counters()

    def _reset_metric_counters(self) -> None:
        self.current_metrics = {"decisions":0,"makerPlacements":0,"makerFills":0,"takerFills":0,"excursions":0,"passivePrioritySteps":0,"unresolvedGuardEntries":0,"residualWakes":0,"residualRecoveries":0,"burstPlacements":0,"makerCapBlocks":0,"takerCapBlocks":0}

    def start(self) -> None:
        threading.Thread(target=self._loop, name="unified-promoted-ownstate-v4-r2-cap100", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set(); self.http.close(); self.book.close(); self.recorder.close()

    def _fetch_snapshot(self) -> dict[str, Any] | None:
        response = self.http.get(SOURCE_URL); response.raise_for_status(); payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            self.source_ready = False; self.source_wait_reason = "SOURCE_STATE_UNAVAILABLE"; return None
        health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
        integrity = payload.get("dataIntegrity") if isinstance(payload.get("dataIntegrity"), dict) else {}
        self.source_health = {"processHealthy":health.get("processHealthy"),"strategyInputReady":health.get("strategyInputReady"),"strategyStatus":health.get("strategyStatus"),"missingFeatures":list(integrity.get("missingFeatures") or health.get("missingFeatures") or []),"lastLoopAgeMs":health.get("lastLoopAgeMs")}
        if health.get("targetEventsUsedForDecision") is True:
            raise RuntimeError("SOURCE_CONTAMINATION_TARGET_EVENTS_USED_FOR_DECISION")
        if health.get("processHealthy") is not True:
            self.source_ready = False; self.source_wait_reason = "SOURCE_PROCESS_DEGRADED"; return None
        if integrity.get("ready") is not True:
            missing = self.source_health["missingFeatures"]; suffix = ":"+",".join(str(x) for x in missing) if missing else ""
            self.source_ready = False; self.source_wait_reason = f"SOURCE_INPUT_INCOMPLETE{suffix}"; return None
        snap = payload.get("latestPublicSnapshot")
        if not isinstance(snap, dict):
            self.source_ready = False; self.source_wait_reason = "SOURCE_SNAPSHOT_MISSING"; return None
        sampled = int(number(snap.get("sampledAtMs")) or 0); age = now_ms()-sampled if sampled else SOURCE_MAX_AGE_MS+1
        if sampled <= 0 or age > SOURCE_MAX_AGE_MS or age < -1000:
            self.source_ready = False; self.source_wait_reason = f"SOURCE_SNAPSHOT_STALE:{age}"; return None
        self.source_ready = True; self.source_wait_reason = None
        return dict(snap)

    def _cancel_order(self, key: tuple[str,int], at_ms: int, reason: str) -> None:
        order = self.orders.pop(key, None)
        if order is None: return
        self.last_closed[key] = int(at_ms)
        self.recorder.record_order_cancel(order_id=order.id, cancelled_at_ms=at_ms, reason=reason)

    def _reset_market(self, market_id: int, sampled_at: int) -> None:
        at = now_ms()
        for key in list(self.orders): self._cancel_order(key, at, "MARKET_ROLLOVER")
        self.current_market_id = int(market_id); self.inventory.reset(); self.orders.clear(); self.last_closed.clear(); self.placements.clear(); self.episode=None; self.readiness=False; self.last_taker_ms=-10**18
        self.maker_spent_notional = 0.0; self.taker_spent_notional = 0.0; self.taker_fee_spent = 0.0
        self.last_snapshot_ms = None; self.last_eval_snapshot_ms = None; self.last_decision = None; self._reset_metric_counters()
        self.book_ready = self.book.reset(int(market_id), int(sampled_at))
        self.rng = np.random.default_rng((RNG_SEED * 1000003 + int(market_id)) % (2**63-1))
        self.residual_rng = np.random.default_rng((RNG_SEED * 3000017 + int(market_id) + 7717) % (2**63-1))
        if self.excluded_deployment_market_id is None:
            self.excluded_deployment_market_id = int(market_id); self.active = False
        else:
            self.active = True; self.run_metrics["marketsStarted"] += 1

    def _update_depletion(self, changes: dict[str, Any], _source_ms: int) -> None:
        for order in self.orders.values():
            for ch in changes.get(order.native_side, []) or []:
                try: px=float(ch.get("price")); delta=float(ch.get("delta",0.0))
                except Exception: continue
                if abs(px-order.native_price)<=1e-9 and delta<0:
                    order.any_depletion=True; order.cum_depletion += -delta

    def _maker_totals(self) -> tuple[float,float,float,float,float]:
        u,d = self.inventory.maker_up,self.inventory.maker_down; gross=u+d; net=u-d
        return u,d,gross,net,(2*min(u,d)/gross if gross>EPS else 1.0)

    def _fill_orders(self, snapshot: dict[str, Any], now: int) -> list[dict[str, Any]]:
        book = self.book.book; bids, asks = book.get("bids",{}), book.get("asks",{})
        if not bids or not asks: return []
        bb, ba = max(bids), min(asks); fills=[]
        for key, order in list(self.orders.items()):
            if now-order.placed_at_ms < 250: continue
            public_bid = float(bb) if order.side=="UP" else 1.0-float(ba)
            pass_through = public_bid < order.price-EPS
            queue_cleared = order.initial_depth>EPS and order.cum_depletion>=order.initial_depth-EPS
            if not (pass_through or queue_cleared): continue
            pre_net = self.inventory.maker_up-self.inventory.maker_down; pre_g=self.inventory.maker_up+self.inventory.maker_down; pre_pc=2*min(self.inventory.maker_up,self.inventory.maker_down)/pre_g if pre_g>EPS else 1.0
            self.orders.pop(key,None); self.last_closed[key]=now
            event={"event_ms":now,"role":"MAKER","side":order.side,"price":order.price,"shares":order.shares}; self.inventory.apply(event)
            self.maker_spent_notional += float(order.price) * float(order.shares)
            post_net=self.inventory.maker_up-self.inventory.maker_down
            self.recorder.record_order_fill(order_id=order.id,fill_id=f"{order.id}:FILL:{now}",filled_at_ms=now,fill_price=order.price,fill_state={"snapshot":snapshot,"fillProxy":"QUEUECLEAR_PASS","passThrough":pass_through,"queueCleared":queue_cleared,"initialDepth":order.initial_depth,"cumDepletion":order.cum_depletion,"publicBookSourceMs":self.book.last_source_ms},purpose="PASSIVE_MAKER",payload={"paperOnly":True,"queuePriorityClaim":False,"targetDataUsed":False})
            self.current_metrics["makerFills"]+=1; self.run_metrics["makerFills"]+=1
            if order.occupied_before:
                predom="UP" if pre_net>EPS else "DOWN" if pre_net<-EPS else None
                if predom==order.side and abs(pre_net)>=SHARES-EPS and abs(post_net)>abs(pre_net)+1:
                    if self.episode is None or str(self.episode.get("side"))!=order.side:
                        self.episode={"kind":"OVERLAP","side":order.side,"risk_start_ms":now,"risk_pre_abs":abs(pre_net),"start_ms":now,"pre_abs":abs(pre_net),"start_abs":abs(post_net),"expansion":abs(post_net)-abs(pre_net),"pre_pc":pre_pc,"unresolved":False}; self.readiness=False
                    else:
                        self.episode.update({"kind":"OVERLAP","risk_start_ms":now,"risk_pre_abs":abs(pre_net),"start_ms":now,"pre_abs":abs(pre_net),"start_abs":abs(post_net),"expansion":abs(post_net)-abs(pre_net),"pre_pc":pre_pc,"unresolved":False}); self.readiness=False
                    self.current_metrics["excursions"]+=1; self.run_metrics["excursions"]+=1
            fills.append({"side":order.side,"price":order.price,"shares":order.shares,"passThrough":pass_through,"queueCleared":queue_cleared})
        return fills

    def _active_geom(self, side: str, bf: dict[str,float], now: int) -> tuple[int,int,float,float,float,float]:
        opp="DOWN" if side=="UP" else "UP"; same=[o for o in self.orders.values() if o.side==side]; other=[o for o in self.orders.values() if o.side==opp]
        def one(xs:list[PaperOrder], s:str):
            if not xs: return 0,math.nan,math.nan
            o=max(xs,key=lambda z:z.placed_at_ms); bid=bf["up_bid"] if s=="UP" else bf["down_bid"]
            return len(xs),float(now-o.placed_at_ms),float((bid-o.price)/GRID)
        a,aa,ao=one(same,side); b,ba,bo=one(other,opp); return a,b,aa,ba,ao,bo

    def _align_num(self, snapshot: dict[str,Any], side:str) -> float:
        bias=str(snapshot.get("directionBias") or "NEUTRAL").upper()
        if bias not in {"UP","DOWN"}: return 0.0
        return 1.0 if bias==side else -1.0

    def _vol_level(self, snapshot: dict[str,Any]) -> float:
        return {"NORMAL":0.0,"WATCH":1.0,"HIGH":2.0}.get(str(snapshot.get("volatilityAlert") or "UNKNOWN").upper(),-1.0)

    def _quote(self, side:str, offset:int=1) -> tuple[int,float] | None:
        bf=outcome_book(self.book.book,None)
        if bf is None: return None
        bid=bf["up_bid"] if side=="UP" else bf["down_bid"]
        tick=int(math.floor((bid+1e-9)/GRID))-max(0,int(offset)); min_tick=int(round(MIN_PRICE/GRID)); tick=max(min_tick,tick); price=round(tick*GRID,2)
        opp="DOWN" if side=="UP" else "UP"; opp_orders=[o for o in self.orders.values() if o.side==opp]
        if opp_orders:
            mx=max(o.price for o in opp_orders)
            while price+mx>MAX_PAIR_PRICE_SUM+EPS:
                tick-=1
                if tick<min_tick: return None
                price=round(tick*GRID,2)
        return tick,price

    def _add_order(self, side:str, now:int, snapshot_ns:int, decision_id:str, reason:str, p:float, snapshot:dict[str,Any], allow_stack:bool=True, bypass_guard:bool=False) -> bool:
        same=[o for o in self.orders.values() if o.side==side]; occupied=bool(same)
        if same:
            if not allow_stack or len(same)>=2: return False
            latest=max(same,key=lambda o:o.placed_at_ms); sec=number(snapshot.get("secondsLeft")); vol=str(snapshot.get("volatilityAlert") or "NORMAL").upper()
            context=(now-latest.placed_at_ms)<1500 or (sec is not None and 15<float(sec)<=60) or vol in {"WATCH","HIGH"}
            if not context and not bypass_guard: return False
            if not bypass_guard:
                _,_,_,net,pc=self._maker_totals(); dom="UP" if net>EPS else "DOWN" if net<-EPS else None
                if dom==side and pc<0.80:
                    bias=str(snapshot.get("directionBias") or "NEUTRAL").upper()
                    if not (bias in {"UP","DOWN"} and bias!=side): return False
        qr=self._quote(side,1)
        if qr is None: return False
        tick,price=qr; min_tick=int(round(MIN_PRICE/GRID))
        while (side,tick) in self.orders:
            tick-=1
            if tick<min_tick: return False
            price=round(tick*GRID,2)
        opp="DOWN" if side=="UP" else "UP"; opp_orders=[o for o in self.orders.values() if o.side==opp]
        if opp_orders:
            mx=max(o.price for o in opp_orders)
            while price+mx>MAX_PAIR_PRICE_SUM+EPS:
                tick-=1
                if tick<min_tick:return False
                price=round(tick*GRID,2)
        key=(side,tick)
        if now-int(self.last_closed.get(key,0))<REFILL_COOLDOWN_MS:return False
        committed = sum(float(o.price) * float(o.shares) for o in self.orders.values())
        need = float(price) * SHARES
        if self.maker_spent_notional + committed + need > CAP_MAKER_BUDGET_USDT + 1e-9:
            self.current_metrics["makerCapBlocks"] += 1; self.run_metrics["makerCapBlocks"] += 1
            return False
        native_side="bids" if side=="UP" else "asks"; native_price=round(price if side=="UP" else 1.0-price,10); initial=float(self.book.book.get(native_side,{}).get(native_price,0.0))
        self.sequence+=1; oid=f"{VERSION}:{self.current_market_id}:MAKER:{side}:{tick}:{now}:{self.sequence}"
        order=PaperOrder(oid,side,tick,price,SHARES,now,snapshot_ns,initial,native_side,native_price,occupied); self.orders[key]=order
        self.placements.append({"at_ms":now,"side":side,"price":price,"reason":reason,"p":p,"occupied_before":int(occupied)})
        self.current_metrics["makerPlacements"]+=1; self.run_metrics["makerPlacements"]+=1
        self.recorder.record_order_placement(order_id=oid,strategy_version=VERSION,market_id=int(self.current_market_id),placement_decision_id=decision_id,channel="MAKER",side=side,quote_type="BID",price=price,shares=SHARES,placed_at_ms=now,placement_state={"version":VERSION,"decisionId":decision_id,"reason":reason,"pMaker":p,"offsetTicks":1,"occupiedBefore":occupied,"initialDepth":initial,"nativeSide":native_side,"nativePrice":native_price,"portfolio":self.inventory.features(now),"publicBookSourceMs":self.book.last_source_ms,"paperOnly":True,"targetDataUsed":False})
        return True

    def _record_taker(self, side:str, price:float, now:int, decision_id:str, snapshot:dict[str,Any], raw:dict[str,Any], p1:float, p3:float, ppass:float, pred_effect:str) -> bool:
        fee=taker_fee(SHARES,price,FEE_BPS); need=float(price)*SHARES+float(fee)
        spent=self.maker_spent_notional+self.taker_spent_notional+self.taker_fee_spent
        if spent+need>CAP_TOTAL_USDT+1e-9:
            self.current_metrics["takerCapBlocks"]+=1; self.run_metrics["takerCapBlocks"]+=1
            return False
        event={"event_ms":now,"role":"TAKER","side":side,"price":price,"shares":SHARES}; self.inventory.apply(event)
        self.taker_spent_notional += float(price)*SHARES; self.taker_fee_spent += float(fee)
        self.current_metrics["takerFills"]+=1; self.run_metrics["takerFills"]+=1; self.last_taker_ms=now
        self.recorder.record_taker_fill(fill_id=f"{decision_id}:TAKER_FILL",strategy_version=VERSION,market_id=int(self.current_market_id),decision_id=decision_id,purpose="PROMOTED_ACTIVE_INTERVENTION_CAP100",side=side,price=price,shares=SHARES,filled_at_ms=now,decision_state={"version":VERSION,"pTaker1s":p1,"pTaker3s":p3,"pPassiveRepair":ppass,"predEffect":pred_effect,"rawState":raw,"capital":self._capital_state(),"paperOnly":True,"targetDataUsed":False},fill_state={"snapshot":snapshot,"observedAsk":price,"feeUsdt":fee,"paperOnly":True},payload={"feeBps":FEE_BPS,"paperOnly":True,"liveOrdersAffected":False,"capitalCapUsdt":CAP_TOTAL_USDT})
        return True

    def _capital_state(self) -> dict[str, float]:
        committed=sum(float(o.price)*float(o.shares) for o in self.orders.values())
        spent=self.maker_spent_notional+self.taker_spent_notional+self.taker_fee_spent
        return {"totalCapUsdt":CAP_TOTAL_USDT,"makerBudgetUsdt":CAP_MAKER_BUDGET_USDT,"takerReserveUsdt":CAP_TAKER_RESERVE_USDT,"makerSpentUsdt":self.maker_spent_notional,"makerCommittedUsdt":committed,"takerSpentUsdt":self.taker_spent_notional,"takerFeesUsdt":self.taker_fee_spent,"spentUsdt":spent,"worstCaseCommittedUsdt":spent+committed,"totalRemainingUsdt":max(0.0,CAP_TOTAL_USDT-spent-committed),"makerRemainingUsdt":max(0.0,CAP_MAKER_BUDGET_USDT-self.maker_spent_notional-committed)}

    def _active_intervention_required(self) -> bool:
        # Frozen paper default: no architectural override. Live adapters may
        # promote an existing state transition to required execution semantics.
        return False

    def _on_active_intervention_required(self, reason: str, now: int) -> None:
        return

    def _on_active_intervention_satisfied(self) -> None:
        return

    def _step(self, snapshot: dict[str, Any]) -> None:
        market_id=int(number(snapshot.get("marketId")) or 0); sampled=int(number(snapshot.get("sampledAtMs")) or 0); ns=int(number(snapshot.get("timestampNs")) or 0)
        if market_id<=0 or sampled<=0 or ns<=0:return
        if self.current_market_id!=market_id:self._reset_market(market_id,sampled)
        if sampled==self.last_snapshot_ms:return
        self.last_snapshot_ms=sampled
        self.book_ready=self.book.advance(market_id,sampled,self._update_depletion)
        book_age=sampled-self.book.last_source_ms if self.book.last_source_ms is not None else None
        if not self.book_ready or book_age is None or book_age<0 or book_age>BOOK_MAX_AGE_MS:
            self.source_wait_reason=f"PUBLIC_BOOK_NOT_READY:{book_age}"; return
        if not self.active:return
        now=sampled
        maker_fills=self._fill_orders(snapshot,now)
        if self.last_eval_snapshot_ms is not None and sampled-self.last_eval_snapshot_ms<DECISION_MIN_INTERVAL_MS:
            return
        self.last_eval_snapshot_ms=sampled
        feat=self.inventory.features(now); cn=float(feat.pop("_combined_net")); dom="UP" if cn>EPS else "DOWN" if cn<-EPS else None; bf=outcome_book(self.book.book,dom)
        if bf is None:return
        sec=number(snapshot.get("secondsLeft")); raw={"seconds_left":float(sec) if sec is not None else math.nan,**feat,**bf}; pf=placement_features(self.placements,now)
        pu_base=float(self.p_maker_up_base(raw)); pd_base=float(self.p_maker_down_base(raw)); pt1=float(self.p_taker1(raw)); pt3=float(self.p_taker3(raw))
        corr={**raw,**pf,"pMakerUp":pu_base,"pMakerDown":pd_base,"pTaker1s":pt1,"pTaker3s":pt3}
        pu=_binary_prob(self.models["corrective_up"],corr); pd=_binary_prob(self.models["corrective_down"],corr)
        decision_id=f"{VERSION}:{market_id}:DECISION:{sampled}"; actions:list[dict[str,Any]]=[]; passive_draw=False; ppass=math.nan; taker_fill:dict[str,Any]|None=None
        placements_before = self.current_metrics["makerPlacements"]
        # R2 residual wake: raw frozen p3 may wake arbitration outside overlap episodes, but never sends Taker directly.
        p_residual_wake = pt3
        if self.episode is None:
            maker_net = float(raw.get("maker_net", 0.0) or 0.0)
            maker_abs = abs(maker_net)
            if maker_abs > 1.0 and self.residual_rng.random() < p_residual_wake:
                es = "UP" if maker_net > 0 else "DOWN"
                maker_pc = float(raw.get("maker_paired_coverage", 0.0) or 0.0)
                self.episode={"kind":"RESIDUAL","side":es,"risk_start_ms":now,"risk_pre_abs":maker_abs,"start_ms":now,"pre_abs":maker_abs,"start_abs":maker_abs,"expansion":0.0,"pre_pc":maker_pc,"unresolved":False}
                self.readiness=True
                self.current_metrics["residualWakes"]+=1; self.run_metrics["residualWakes"]+=1
                actions.append({"action":"RESIDUAL_ARBITRATION_WAKE","side":es,"p":p_residual_wake,"directTaker":False})
        if self.episode is not None:
            cur_abs=abs(self.inventory.maker_up-self.inventory.maker_down)
            kind=str(self.episode.get("kind","OVERLAP"))
            recovered=((now-int(self.episode["risk_start_ms"])>=3000) and cur_abs<float(self.episode["start_abs"])-1.0) if kind=="RESIDUAL" else (cur_abs<=float(self.episode.get("risk_pre_abs",self.episode["pre_abs"]))+1)
            if recovered:
                action_name="RESIDUAL_RECOVERED" if kind=="RESIDUAL" else "EXCURSION_RECOVERED"
                actions.append({"action":action_name,"side":self.episode["side"]})
                if kind=="RESIDUAL": self.current_metrics["residualRecoveries"]+=1; self.run_metrics["residualRecoveries"]+=1
                self.episode=None; self.readiness=False
            elif now-int(self.episode.get("risk_start_ms",self.episode["start_ms"]))>15000 and not bool(self.episode.get("unresolved")):
                self.episode["unresolved"]=True; self.readiness=True; self.current_metrics["unresolvedGuardEntries"]+=1; self.run_metrics["unresolvedGuardEntries"]+=1
                es=str(self.episode["side"])
                for key,o in list(self.orders.items()):
                    if o.side==es:self._cancel_order(key,now,"UNRESOLVED_15S_DOMINANT_GUARD")
                actions.append({"action":"UNRESOLVED_GUARD_ENTER","side":es,"kind":kind})
                self._on_active_intervention_required("UNRESOLVED_PASSIVE_REPAIR_15S", now)
        if self.episode is not None:
            es=str(self.episode["side"]); ac,oc,aa,oa,aoff,ooff=self._active_geom(es,bf,now)
            prow={**raw,"excursion_side_is_up":float(es=="UP"),"excursion_age_ms":float(now-int(self.episode["start_ms"])),"excursion_pre_maker_abs_net":float(self.episode["pre_abs"]),"excursion_start_maker_abs_net":float(self.episode["start_abs"]),"excursion_expansion_shares":float(self.episode["expansion"]),"excursion_pre_maker_paired_coverage":float(self.episode["pre_pc"]),"active_same_count":float(ac),"active_opp_count":float(oc),"active_same_age_ms":aa,"active_opp_age_ms":oa,"active_same_offset_ticks":aoff,"active_opp_offset_ticks":ooff,"direction_alignment":self._align_num(snapshot,es),"direction_score":float(snapshot.get("directionScore") or 0.0),"volatility_level":self._vol_level(snapshot),"maker_pressure_same":pu_base if es=="UP" else pd_base,"maker_pressure_opp":pd_base if es=="UP" else pu_base,"taker_pressure_1s":pt1,"maker_pressure_gap":(pd_base-pu_base) if es=="UP" else (pu_base-pd_base)}
            if str(self.episode.get("kind","OVERLAP"))=="RESIDUAL":
                ppass=_binary_prob(self.models["residual_passive_repair"],_residual_raw(raw)); passive_draw=bool(self.residual_rng.random()<ppass)
            else:
                ppass=_binary_prob(self.models["passive_repair"],prow); passive_draw=bool(self.rng.random()<ppass)
            if not self.readiness and self.rng.random()<pt3:
                self.readiness=True; actions.append({"action":"TAKER_READINESS_LATCH","p":pt3})
            active_required = bool(self._active_intervention_required())
            if active_required and now-self.last_taker_ms>=1000:
                classes,probs=_class_probs(self.models["side"],raw); probs=probs/probs.sum(); chosen=str(self.rng.choice(classes,p=probs)); ecls,eprobs=_class_probs(self.models["effect"],raw); pred_effect=str(ecls[int(np.argmax(eprobs))]); ask=bf["up_ask"] if chosen=="UP" else bf["down_ask"]
                if math.isfinite(float(ask)):
                    if self._record_taker(chosen,float(ask),now,decision_id,snapshot,raw,pt1,pt3,ppass,pred_effect):
                        taker_fill={"side":chosen,"price":float(ask),"predEffect":pred_effect}; actions.append({"action":"ACTIVE_INTERVENTION_REQUIRED_EXECUTE","side":chosen,"pTaker1sObservedNotGating":pt1,"predEffect":pred_effect}); self.episode=None; self.readiness=False; self._on_active_intervention_satisfied()
                    else:
                        actions.append({"action":"ACTIVE_INTERVENTION_REQUIRED_BLOCKED","side":chosen,"pTaker1sObservedNotGating":pt1,"capital":self._capital_state()})
            elif passive_draw:
                self.current_metrics["passivePrioritySteps"]+=1; self.run_metrics["passivePrioritySteps"]+=1; opp="DOWN" if es=="UP" else "UP"; existed=any(o.side==opp for o in self.orders.values())
                if not existed:self._add_order(opp,now,ns,decision_id,"PASSIVE_REPAIR_PRIORITY",pd if opp=="DOWN" else pu,snapshot,allow_stack=False,bypass_guard=True)
                actions.append({"action":"PASSIVE_REPAIR_PRIORITY","side":opp,"p":ppass,"detail":"KEEP" if existed else "CREATE_IF_POSSIBLE"})
            if self.episode is not None and (not active_required) and bool(self.episode.get("unresolved")):
                opp="DOWN" if es=="UP" else "UP"
                if not any(o.side==opp for o in self.orders.values()):self._add_order(opp,now,ns,decision_id,"UNRESOLVED_REPAIR_ONLY",pd if opp=="DOWN" else pu,snapshot,allow_stack=False,bypass_guard=True)
            if self.episode is not None and (not active_required) and self.readiness and (not passive_draw or bool(self.episode.get("unresolved"))) and self.rng.random()<pt1 and now-self.last_taker_ms>=1000:
                classes,probs=_class_probs(self.models["side"],raw); probs=probs/probs.sum(); chosen=str(self.rng.choice(classes,p=probs)); ecls,eprobs=_class_probs(self.models["effect"],raw); pred_effect=str(ecls[int(np.argmax(eprobs))]); ask=bf["up_ask"] if chosen=="UP" else bf["down_ask"]
                if math.isfinite(float(ask)):
                    if self._record_taker(chosen,float(ask),now,decision_id,snapshot,raw,pt1,pt3,ppass,pred_effect):
                        taker_fill={"side":chosen,"price":float(ask),"predEffect":pred_effect}; actions.append({"action":"TAKER_INTERVENE","side":chosen,"p":pt1,"predEffect":pred_effect}); self.episode=None; self.readiness=False
                    else:
                        actions.append({"action":"TAKER_CAP_BLOCK","side":chosen,"p":pt1,"capital":self._capital_state()})
        blocked_side=str(self.episode["side"]) if self.episode is not None and (passive_draw or bool(self.episode.get("unresolved"))) else None
        maker_suppressed=bool(self._active_intervention_required())
        if (not maker_suppressed) and self.rng.random()<pu and blocked_side!="UP":
            made=self._add_order("UP",now,ns,decision_id,"MAKER_HAZARD",pu,snapshot)
            if made:
                pb=_binary_prob(self.models["burst_up"],{**raw,**pf,"hazard_p":pu})
                if self.rng.random()<pb and self._add_order("UP",now,ns,decision_id,"MAKER_BURST",pb,snapshot):
                    self.current_metrics["burstPlacements"]+=1; self.run_metrics["burstPlacements"]+=1; actions.append({"action":"MAKER_BURST","side":"UP","p":pb})
        if (not maker_suppressed) and self.rng.random()<pd and blocked_side!="DOWN":
            made=self._add_order("DOWN",now,ns,decision_id,"MAKER_HAZARD",pd,snapshot)
            if made:
                pb=_binary_prob(self.models["burst_down"],{**raw,**pf,"hazard_p":pd})
                if self.rng.random()<pb and self._add_order("DOWN",now,ns,decision_id,"MAKER_BURST",pb,snapshot):
                    self.current_metrics["burstPlacements"]+=1; self.run_metrics["burstPlacements"]+=1; actions.append({"action":"MAKER_BURST","side":"DOWN","p":pb})
        portfolio=self.inventory.features(now); portfolio.pop("_combined_net",None); direction=simple3(snapshot)
        placed_this_step = self.current_metrics["makerPlacements"] > placements_before
        execution="TAKER" if taker_fill else "MAKER" if placed_this_step else "WAIT"
        if taker_fill: desired="ACTIVE_INTERVENTION_REQUIRED" if active_required else "ACTIVE_INTERVENTION"; reason="UNRESOLVED_PASSIVE_REPAIR_15S" if active_required else "PROMOTED_TAKER_ESCALATION"
        elif self._active_intervention_required(): desired="ACTIVE_INTERVENTION_REQUIRED"; reason="UNRESOLVED_PASSIVE_REPAIR_15S"
        elif self.episode is not None: desired="PASSIVE_REPAIR"; reason="PROMOTED_SEQUENTIAL_REPAIR"
        else: desired="PASSIVE_MAINTAIN"; reason="PROMOTED_MAKER_PRESSURE"
        trace={"version":VERSION,"decisionId":decision_id,"marketId":market_id,"decisionMs":now,"sourceSnapshotMs":sampled,"phase":phase_from_seconds(sec),"desiredPortfolioAction":desired,"executionChoice":execution,"primaryReason":reason,"direction":direction,"portfolio":portfolio,"models":{"pMakerUpBase":pu_base,"pMakerDownBase":pd_base,"pMakerUp":pu,"pMakerDown":pd,"pTaker1s":pt1,"pTaker3s":pt3,"pResidualWake":p_residual_wake,"pPassiveRepair":ppass},"episode":dict(self.episode) if self.episode else None,"readiness":self.readiness,"actions":actions,"makerFillsThisSnapshot":maker_fills,"activeMakerOrders":len(self.orders),"bookSourceMs":self.book.last_source_ms,"bookAgeMs":sampled-int(self.book.last_source_ms or sampled),"fillProxy":"QUEUECLEAR_PASS","quotePolicy":"FIXED_BEST_BID_MINUS_1_R2","paperOnly":True,"liveOrdersAffected":False,"targetEventsUsedForDecision":False,"targetDataRead":False,"capital":self._capital_state(),"capitalPolicy":"CAP100_KEEP18_MAKER80_TAKER20"}
        self.recorder.record_decision(decision_id=decision_id,strategy_version=VERSION,market_id=market_id,decision_ms=now,source_snapshot_ms=sampled,seconds_left=sec,phase=trace["phase"],desired_portfolio_action=desired,execution_choice=execution,side=taker_fill.get("side") if taker_fill else None,size=SHARES if taker_fill else 0.0,primary_reason=reason,supporting_reasons={"models":trace["models"],"actions":actions,"fillProxy":"QUEUECLEAR_PASS","quotePolicy":"FIXED_MINUS_1"},veto_reasons=[],direction_state=direction,portfolio_state=portfolio,economics_state={"pairBidEdge":bf["pair_bid_edge"],"pairAskEdge":bf["pair_ask_edge"],"makerAvgPairEdge":raw.get("maker_avg_pair_edge"),"worstCaseFloor":raw.get("worst_case_floor")},arbitration_state={"episode":trace["episode"],"readiness":self.readiness,"activeOrders":len(self.orders)},public_state=snapshot,payload=trace)
        self.current_metrics["decisions"]+=1; self.run_metrics["decisions"]+=1; self.last_decision=trace

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started=time.monotonic()
            try:
                snapshot=self._fetch_snapshot()
                if snapshot:
                    with self.lock:self._step(snapshot)
                self.last_error=None
            except Exception as exc:
                self.last_error=f"{type(exc).__name__}: {str(exc)[:700]}"
            self.last_loop_ms=now_ms(); elapsed=time.monotonic()-started; self.stop_event.wait(max(0.05,POLL_SECONDS-elapsed))

    def snapshot(self) -> dict[str, Any]:
        at=now_ms()
        with self.lock:
            portfolio=self.inventory.features(at); portfolio.pop("_combined_net",None)
            status="DEGRADED" if self.last_error else "WAITING_SOURCE" if not self.source_ready else "WAITING_BOOK" if not self.book_ready else "ACTIVE" if self.active else "WAITING_NEXT_COMPLETE_MARKET"
            return {"ok":self.last_error is None,"status":status,"version":VERSION,"candidate":"PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18","paperOnly":True,"forwardOnly":True,"liveOrdersAffected":False,"targetEventsUsedForDecision":False,"targetDataRead":False,"publicBookTableOnly":True,"sourceUrl":SOURCE_URL,"bookDb":str(BOOK_DB),"sourceReady":self.source_ready,"bookReady":self.book_ready,"sourceWaitReason":self.source_wait_reason,"sourceHealth":dict(self.source_health),"currentMarketId":self.current_market_id,"excludedDeploymentMarketId":self.excluded_deployment_market_id,"lastSnapshotMs":self.last_snapshot_ms,"lastSnapshotAgeMs":at-self.last_snapshot_ms if self.last_snapshot_ms else None,"bookSourceMs":self.book.last_source_ms,"bookAgeMs":self.last_snapshot_ms-self.book.last_source_ms if self.last_snapshot_ms and self.book.last_source_ms else None,"lastLoopAgeMs":at-self.last_loop_ms if self.last_loop_ms else None,"lastError":self.last_error,"portfolio":portfolio,"activeMakerOrders":[o.__dict__ for o in self.orders.values()],"episode":self.episode,"readiness":self.readiness,"lastDecision":self.last_decision,"capital":self._capital_state(),"currentMarketMetrics":dict(self.current_metrics),"run":dict(self.run_metrics),"recorderDb":str(self.recorder.path),"policy":{"maker":"R2 Maker stack; fixed 18 shares; new Maker blocked when spent+resting commitments would exceed $80","quote":"fixed best-bid minus 1 tick; V5 quote-zone not action-driving","makerFill":"QUEUECLEAR_PASS public-book proxy from 8778 maker_book_inference_updates only","passiveRepair":"overlap uses promoted Passive Repair EBM; residual uses residual-passive HGB; priority keeps/creates minority quote and suppresses dominant stacking","taker":"raw frozen p3 wakes arbitration; frozen p1 active intervention stays 18 shares and is allowed only while total spend+fee remains <=$100","side":"frozen SIDE probability sample","effect":"frozen EFFECT logged; does not force side","unresolved":"15s guard cancels dominant resting risk, keeps opposite passive repair, latches Taker readiness","decisionCadence":f">={DECISION_MIN_INTERVAL_MS}ms","deploymentBoundary":"current startup market excluded; next complete market only"},"researchStatus":{"r2Frozen":True,"capitalShadow":"CAP100_KEEP18_MAKER80_TAKER20","finalHoldout":"PASSED_25_MARKETS_56PCT_POSITIVE_176P35_USDT","start75to99":"REVEALED_DO_NOT_TUNE","v5QuoteZone":"OBSERVE_ONLY_NOT_PROMOTED","executionHazard":"RESEARCH_ONLY_NOT_PROMOTED"}}


class Handler(BaseHTTPRequestHandler):
    runtime: UnifiedControllerCap100ShadowV1
    def log_message(self,*_args:Any)->None:return
    def _write(self,payload:Any,code:int=200)->None:
        body=json.dumps(payload,ensure_ascii=False,separators=(",",":"),default=str).encode("utf-8"); self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body))); self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_GET(self)->None:
        path=self.path.split("?",1)[0]
        if path in {"/","/state","/health"}:self._write(self.runtime.snapshot())
        else:self._write({"ok":False,"error":"not found"},404)


def main()->int:
    runtime=UnifiedControllerCap100ShadowV1(); runtime.start(); handler=type("UnifiedPromotedOwnstateV4R2Cap100Handler",(Handler,),{"runtime":runtime}); server=ThreadingHTTPServer((HOST,PORT),handler)
    print(f"{VERSION} listening on http://{HOST}:{PORT}/state; public={SOURCE_URL}; book={BOOK_DB}; paperOnly=true; targetDataRead=false; liveOrdersAffected=false",flush=True)
    try:server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:return 130
    finally:server.shutdown(); server.server_close(); runtime.stop()
    return 0


if __name__=="__main__":raise SystemExit(main())
