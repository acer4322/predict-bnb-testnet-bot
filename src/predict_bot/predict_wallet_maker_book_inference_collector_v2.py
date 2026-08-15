from __future__ import annotations

import json, math, os, sqlite3, statistics
from collections import defaultdict
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_maker_book_inference_collector as base

VERSION = "TARGET_MAKER_BOOK_INFERENCE_V2_LIFECYCLE_FORWARD_ONLY"
SIGNAL_DB = Path(os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_DB", base.ROOT / "data" / "wallet_taker_signals.db"))
LOOKBACK_MS, POST_MS, CANCEL_SCAN_MS = 60_000, 5_000, 90_000
TARGET_UNIT, SNAPSHOT_CACHE_MS = 18.0, 5_000


def score_size(a: float, b: float) -> float:
    a, b = max(1e-9, abs(float(a))), max(1e-9, abs(float(b)))
    return min(a, b) / max(a, b)


def pctile(values: list[float], q: float) -> float | None:
    if not values: return None
    xs = sorted(float(x) for x in values); p = (len(xs) - 1) * q
    lo, hi = math.floor(p), math.ceil(p)
    return xs[lo] if lo == hi else xs[lo] * (hi - p) + xs[hi] * (p - lo)


class MakerBookLifecycleInferenceCollector(base.MakerBookInferenceCollector):
    def __init__(self, db_path: Path = base.DB_PATH, target_db_path: Path = base.TARGET_DB_PATH) -> None:
        self.lifecycle_enabled = base.ASSET == "BTC"
        self.last_lifecycle_ms = self.last_cancel_ms = self.last_lifecycle_cleanup_ms = 0
        self.lifecycle_inferred_run = self.cancel_inferred_run = 0
        self._life_cache_at = 0; self._life_cache: dict[str, Any] | None = None
        self._signal_cache: dict[tuple[int, int], dict[str, Any] | None] = {}
        super().__init__(db_path, target_db_path)
        if self.lifecycle_enabled: self.version = VERSION

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS maker_book_inference_lifecycles(
              target_leg_id TEXT PRIMARY KEY,market_id INTEGER,target_side TEXT,native_book_side TEXT,
              target_price REAL,native_price REAL,target_shares REAL,target_notional_usdt REAL,target_event_ms INTEGER,
              matched_source_ms INTEGER,placement_status TEXT,placement_source_ms INTEGER,placement_delta REAL,
              resting_ms INTEGER,completion_type TEXT,aggregate_before REAL,aggregate_after REAL,observed_decrease REAL,
              post_action TEXT,post_action_source_ms INTEGER,post_action_native_price REAL,post_action_delta REAL,
              post_action_delay_ms INTEGER,placement_confidence REAL,lifecycle_confidence REAL,evidence_json TEXT,
              inferred_at_ms INTEGER);
            CREATE INDEX IF NOT EXISTS idx_maker_book_lifecycle_market_time ON maker_book_inference_lifecycles(market_id,target_event_ms);
            CREATE TABLE IF NOT EXISTS maker_book_inference_cancel_candidates(
              candidate_id TEXT PRIMARY KEY,market_id INTEGER,target_side TEXT,native_book_side TEXT,target_price REAL,
              native_price REAL,placement_source_ms INTEGER,placement_delta REAL,cancel_source_ms INTEGER,
              cancel_decrease REAL,resting_ms INTEGER,post_action TEXT,post_action_source_ms INTEGER,
              post_action_native_price REAL,likely_reason TEXT,pressure_side TEXT,signal_age_ms INTEGER,seconds_left REAL,
              confidence REAL,confidence_label TEXT,evidence_json TEXT,inferred_at_ms INTEGER);
            CREATE INDEX IF NOT EXISTS idx_maker_book_cancel_market_time ON maker_book_inference_cancel_candidates(market_id,cancel_source_ms);
            """); self.db.commit()

    def _updates(self, market: int, start: int, end: int) -> list[dict[str, Any]]:
        with self.db_lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id,source_timestamp_ms,received_at_ms,is_checkpoint,changes_z FROM maker_book_inference_updates WHERE market_id=? AND source_timestamp_ms BETWEEN ? AND ? ORDER BY source_timestamp_ms,id",
                (market,start,end))]

    def _events(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out=[]
        for u in rows:
            ch=base.record(base.decode_json(u.get("changes_z") or b""))
            for k,side in (("bids","BID"),("asks","ASK")):
                for c in ch.get(k,[]) if isinstance(ch.get(k),list) else []:
                    p,d=base.finite(c.get("price")),base.finite(c.get("delta"))
                    if p is None or d is None or abs(d)<=1e-9: continue
                    out.append({"source":int(u["source_timestamp_ms"]),"side":side,"price":p,"delta":d,
                                "before":float(c.get("before") or 0),"after":float(c.get("after") or 0),"checkpoint":bool(u["is_checkpoint"])})
        return out

    def _infer_one(self, t: dict[str, Any]) -> bool:
        m, hit = int(t["market_id"]), int(t["matched_source_ms"] or 0)
        with self.db_lock: latest=self.db.execute("SELECT MAX(source_timestamp_ms) FROM maker_book_inference_updates WHERE market_id=?",(m,)).fetchone()[0]
        if not hit or latest is None or int(latest)<hit+POST_MS: return False
        ev=self._events(self._updates(m,hit-LOOKBACK_MS,hit+POST_MS)); side=str(t["native_book_side"]); price=float(t["native_price"]); shares=float(t["target_shares"])
        pre=[]
        for x in ev:
            if x["source"]>=hit or x["side"]!=side or abs(x["price"]-price)>1e-9 or x["delta"]<=0: continue
            age=hit-x["source"]; ss=score_size(x["delta"],shares); rec=max(0,1-age/LOOKBACK_MS)
            x={**x,"ss":ss,"conf":max(0,min(.95,.68*ss+.24*rec+.08-(.08 if x["checkpoint"] else 0)))}; pre.append(x)
        place=max(pre,key=lambda x:(x["conf"],x["source"])) if pre else None
        future=[x for x in ev if hit<x["source"]<=hit+POST_MS and x["side"]==side and x["delta"]>0]
        same=[x for x in future if abs(x["price"]-price)<=1e-9]; near=[x for x in future if 1e-9<abs(x["price"]-price)<=.030000001]
        post=min(same,key=lambda x:x["source"]) if same else min(near,key=lambda x:(x["source"],abs(x["price"]-price))) if near else None
        action="SAME_PRICE_REFILL" if same else "REPRICE_1_3_TICKS" if near else "NO_VISIBLE_REPLACEMENT_5S"
        pc=float(place["conf"]) if place else 0; mc=float(t.get("match_confidence") or 0); ac=.85 if post else .35
        conf=min(.95,.55*mc+.35*pc+.10*ac); after=float(t.get("after_size") or 0)
        evidence={"identityIsProbabilistic":True,"placementBasis":"prior same-price +depth" if place else "not observed in 60s lookback",
                  "postActionBasis":"public +depth after matched fill","aggregateLevelWarning":"level sizes include all anonymous participants"}
        with self.db_lock:
            self.db.execute("""INSERT OR REPLACE INTO maker_book_inference_lifecycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                str(t["leg_id"]),m,str(t["side"]),side,float(t["target_price"]),price,shares,float(t["target_price"])*shares,int(t["target_event_ms"]),hit,
                "INFERRED_PUBLIC_INCREASE" if place else "UNOBSERVED_OR_PREEXISTING",int(place["source"]) if place else None,float(place["delta"]) if place else None,
                hit-int(place["source"]) if place else None,"AGGREGATE_LEVEL_DEPLETED" if after<=1e-9 else "AGGREGATE_LEVEL_REMAINS",
                float(t.get("before_size") or 0),after,float(t.get("observed_decrease") or 0),action,int(post["source"]) if post else None,
                float(post["price"]) if post else None,float(post["delta"]) if post else None,int(post["source"])-hit if post else None,pc,conf,
                json.dumps(evidence,separators=(",",":")),base.now_ms())); self.db.commit()
        self.lifecycle_inferred_run+=1; self._life_cache_at=0; return True

    def _infer_lifecycles(self, limit: int = 8) -> int:
        with self.db_lock:
            rows=[dict(r) for r in self.db.execute("""SELECT t.* FROM maker_book_inference_target_events t LEFT JOIN maker_book_inference_lifecycles l ON l.target_leg_id=t.leg_id WHERE t.status='MATCHED' AND t.matched_source_ms IS NOT NULL AND l.target_leg_id IS NULL ORDER BY t.target_event_ms DESC LIMIT 96""")]
        n=0
        for r in rows:
            if n>=limit: break
            n+=int(self._infer_one(r))
        return n

    def _signal(self, market: int, ms: int) -> dict[str, Any] | None:
        key=(market,ms//1000)
        if key in self._signal_cache: return self._signal_cache[key]
        if len(self._signal_cache)>512: self._signal_cache.clear()
        row=None
        if SIGNAL_DB.exists():
            try:
                con=sqlite3.connect(f"file:{SIGNAL_DB.resolve()}?mode=ro",uri=True,timeout=.5); con.row_factory=sqlite3.Row
                row=con.execute("""SELECT sampled_at_ms,seconds_left,direction_score,spot_queue_imbalance,spot_taker_imbalance_1s,spot_return_3s_bps,futures_queue_imbalance,futures_taker_imbalance_1s,futures_return_3s_bps FROM wallet_taker_signal_snapshots WHERE market_id=? AND sampled_at_ms<=? ORDER BY sampled_at_ms DESC LIMIT 1""",(market,ms)).fetchone(); con.close()
            except sqlite3.Error: row=None
        out=dict(row) if row is not None and ms-int(row["sampled_at_ms"])<=2000 else None; self._signal_cache[key]=out; return out

    @staticmethod
    def _pressure(s: dict[str, Any] | None) -> dict[str, Any]:
        if not s: return {"side":None,"upVotes":0,"downVotes":0,"votes":{}}
        specs=(("direction_score",.35),("spot_return_3s_bps",1),("futures_return_3s_bps",1),("spot_taker_imbalance_1s",.35),("futures_taker_imbalance_1s",.35),("spot_queue_imbalance",.25),("futures_queue_imbalance",.25)); votes={}
        for k,t in specs:
            v=base.finite(s.get(k)); votes[k]=None if v is None or abs(v)<t else "UP" if v>0 else "DOWN"
        up=sum(v=="UP" for v in votes.values()); down=sum(v=="DOWN" for v in votes.values()); side="UP" if up>=3 and up-down>=2 else "DOWN" if down>=3 and down-up>=2 else None
        return {"side":side,"upVotes":up,"downVotes":down,"votes":votes}

    def _matched_near(self,m:int,side:str,p:float,ms:int)->bool:
        with self.db_lock: r=self.db.execute("SELECT 1 FROM maker_book_inference_target_events WHERE market_id=? AND status='MATCHED' AND native_book_side=? AND ABS(native_price-?)<=1e-9 AND matched_source_ms BETWEEN ? AND ? LIMIT 1",(m,side,p,ms-2000,ms+2000)).fetchone()
        return r is not None

    def _infer_cancels(self) -> int:
        m,latest=self.current_market_id,self.last_source_ms
        if m is None or latest is None: return 0
        ev=self._events(self._updates(int(m),int(latest)-CANCEL_SCAN_MS,int(latest))); pos=defaultdict(list); neg=defaultdict(list)
        for x in ev: (pos if x["delta"]>0 else neg)[(x["side"],x["price"])].append(x)
        n=0
        for (side,p),adds in pos.items():
            for a in adds:
                ps=score_size(a["delta"],TARGET_UNIT)
                if ps<.70: continue
                cuts=[x for x in neg.get((side,p),[]) if a["source"]+250<=x["source"]<=a["source"]+LOOKBACK_MS and x["source"]<=int(latest)-3000 and score_size(-x["delta"],a["delta"])>=.60]
                if not cuts: continue
                c=min(cuts,key=lambda x:x["source"])
                if self._matched_near(int(m),side,p,int(c["source"])): continue
                cid=f"{m}:{side}:{p:.12g}:{a['source']}:{c['source']}"
                with self.db_lock:
                    if self.db.execute("SELECT 1 FROM maker_book_inference_cancel_candidates WHERE candidate_id=?",(cid,)).fetchone(): continue
                target_side="UP" if side=="BID" else "DOWN"; target_price=p if target_side=="UP" else 1-p
                fut=[x for x in ev if c["source"]<x["source"]<=c["source"]+3000 and x["side"]==side and x["delta"]>0]; same=[x for x in fut if abs(x["price"]-p)<=1e-9]; near=[x for x in fut if 1e-9<abs(x["price"]-p)<=.030000001]; post=min(same,key=lambda x:x["source"]) if same else min(near,key=lambda x:x["source"]) if near else None
                action="SAME_PRICE_REFRESH" if same else "REPRICE_1_3_TICKS" if near else "NO_VISIBLE_REPLACEMENT_3S"; sig=self._signal(int(m),int(c["source"])); pressure=self._pressure(sig); pp=pressure["side"]; opposing=(target_side=="UP" and pp=="DOWN") or (target_side=="DOWN" and pp=="UP"); sl=base.finite(sig.get("seconds_left")) if sig else None
                bulk=sum(abs(x["source"]-c["source"])<=500 and -x["delta"]>=5 for (s,_),xs in neg.items() if s==side for x in xs)
                reason=action if action!="NO_VISIBLE_REPLACEMENT_3S" else "TOXIC_FLOW_PULL" if opposing else "LATE_RISK_REDUCTION" if sl is not None and sl<=60 else "BULK_SIDE_PULL" if bulk>=3 else "UNKNOWN_PUBLIC_DECREASE"
                cs=score_size(-c["delta"],a["delta"]); age=max(0,1-(c["source"]-a["source"])/LOOKBACK_MS); conf=min(.74,.35*ps+.25*cs+.15*age+.15*int(post is not None)+.10*int(reason not in {"UNKNOWN_PUBLIC_DECREASE","BULK_SIDE_PULL"})); label="MEDIUM_SPECULATIVE" if conf>=.58 else "LOW_SPECULATIVE"
                evidence={"identityIsProbabilistic":True,"identityBoundary":"target-like anonymous +depth; ownership not proven","targetLikeParentUnitShares":18,"bulkSameSideLevels500ms":bulk,"publicSignal":pressure}
                with self.db_lock:
                    cur=self.db.execute("INSERT OR IGNORE INTO maker_book_inference_cancel_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,int(m),target_side,side,target_price,p,int(a["source"]),float(a["delta"]),int(c["source"]),-float(c["delta"]),int(c["source"]-a["source"]),action,int(post["source"]) if post else None,float(post["price"]) if post else None,reason,pp,int(c["source"]-int(sig["sampled_at_ms"])) if sig else None,sl,conf,label,json.dumps(evidence,separators=(",",":")),base.now_ms())); self.db.commit()
                n+=int(cur.rowcount or 0)
        if n: self.cancel_inferred_run+=n; self._life_cache_at=0
        return n

    def _match_pending_events(self) -> None:
        super()._match_pending_events()
        if not self.lifecycle_enabled: return
        now=base.now_ms()
        if now-self.last_lifecycle_ms>=750: self.last_lifecycle_ms=now; self._infer_lifecycles()
        if now-self.last_cancel_ms>=3000: self.last_cancel_ms=now; self._infer_cancels()
        if now-self.last_lifecycle_cleanup_ms>=60_000:
            self.last_lifecycle_cleanup_ms=now; cutoff=now-int(base.RETENTION_HOURS*3_600_000)
            with self.db_lock:
                self.db.execute("DELETE FROM maker_book_inference_lifecycles WHERE target_event_ms<?",(cutoff,)); self.db.execute("DELETE FROM maker_book_inference_cancel_candidates WHERE cancel_source_ms<?",(cutoff,)); self.db.commit()

    @staticmethod
    def _capital(rows:list[dict[str,Any]])->dict[str,Any]:
        by=defaultdict(list)
        for r in rows:
            if r.get("placement_source_ms") is None: continue
            n=float(r.get("target_notional_usdt") or 0); m=int(r["market_id"]); by[m]+=[(int(r["placement_source_ms"]),n),(int(r["matched_source_ms"]),-n)]
        peaks=[]
        for es in by.values():
            cur=peak=0
            for _,d in sorted(es,key=lambda x:(x[0],-x[1])): cur+=d; peak=max(peak,cur)
            peaks.append(peak)
        return {"markets":len(peaks),"medianPeakUsdt":statistics.median(peaks) if peaks else None,"p90PeakUsdt":pctile(peaks,.9),"interpretation":"lower bound from inferred placement intervals of orders that later matched target fills; excludes unfilled/unattributed resting orders"}

    def _lifecycle_snapshot(self)->dict[str,Any]:
        now=base.now_ms()
        if self._life_cache is not None and now-self._life_cache_at<SNAPSHOT_CACHE_MS: return dict(self._life_cache)
        with self.db_lock:
            total=dict(self.db.execute("SELECT COUNT(*) n,COALESCE(SUM(placement_source_ms IS NOT NULL),0) placed,COALESCE(SUM(post_action='SAME_PRICE_REFILL'),0) refill,COALESCE(SUM(post_action='REPRICE_1_3_TICKS'),0) reprice,COALESCE(SUM(post_action='NO_VISIBLE_REPLACEMENT_5S'),0) none,COALESCE(SUM(completion_type='AGGREGATE_LEVEL_DEPLETED'),0) depleted,COALESCE(SUM(ABS(target_shares-18.0)<1e-6),0) unit18 FROM maker_book_inference_lifecycles").fetchone())
            rows=[dict(r) for r in self.db.execute("SELECT target_leg_id,market_id,target_side,target_price,target_shares,target_notional_usdt,target_event_ms,matched_source_ms,placement_status,placement_source_ms,placement_delta,resting_ms,completion_type,aggregate_before,aggregate_after,observed_decrease,post_action,post_action_source_ms,post_action_native_price,post_action_delay_ms,placement_confidence,lifecycle_confidence FROM maker_book_inference_lifecycles ORDER BY target_event_ms DESC LIMIT 2000")]
            cancels=int(self.db.execute("SELECT COUNT(*) FROM maker_book_inference_cancel_candidates").fetchone()[0]); reasons=[dict(r) for r in self.db.execute("SELECT likely_reason reason,COUNT(*) count,AVG(confidence) averageConfidence FROM maker_book_inference_cancel_candidates GROUP BY likely_reason ORDER BY count DESC")]; recent_c=[dict(r) for r in self.db.execute("SELECT candidate_id,market_id,target_side,target_price,native_book_side,native_price,placement_source_ms,placement_delta,cancel_source_ms,cancel_decrease,resting_ms,post_action,post_action_native_price,likely_reason,pressure_side,signal_age_ms,seconds_left,confidence,confidence_label FROM maker_book_inference_cancel_candidates ORDER BY cancel_source_ms DESC LIMIT 20")]
        n=int(total["n"]); rest=[float(r["resting_ms"]) for r in rows if r.get("resting_ms") is not None]; sh=[float(r["target_shares"]) for r in rows]; notion=[float(r["target_notional_usdt"]) for r in rows]
        out={"version":VERSION,"enabled":True,"readOnly":True,"forwardEvidenceOnly":True,"retainedForwardEvidenceBackfill":True,"identityBoundary":"target fills are known; placement/refill/reprice/cancel ownership is inferred from anonymous aggregate public-book deltas and is never treated as proven","lifecycles":n,"placementsInferred":int(total["placed"]),"placementInferenceRate":int(total["placed"])/n if n else None,"medianRestingMs":statistics.median(rest) if rest else None,"p90RestingMs":pctile(rest,.9),"samePriceRefillRate":int(total["refill"])/n if n else None,"repriceRate":int(total["reprice"])/n if n else None,"noVisibleReplacement5sRate":int(total["none"])/n if n else None,"aggregateLevelDepletedRate":int(total["depleted"])/n if n else None,"exact18ShareRate":int(total["unit18"])/n if n else None,"medianTargetShares":statistics.median(sh) if sh else None,"medianTargetFillNotionalUsdt":statistics.median(notion) if notion else None,"capitalLowerBound":self._capital(rows),"cancelCandidates":cancels,"cancelReasonBreakdown":reasons,"recent":rows[:20],"recentCancelCandidates":recent_c,"run":{"lifecyclesInferredThisRun":self.lifecycle_inferred_run,"cancelCandidatesThisRun":self.cancel_inferred_run,"signalDb":str(SIGNAL_DB)}}
        self._life_cache=out; self._life_cache_at=now; return dict(out)

    def snapshot(self)->dict[str,Any]:
        out=super().snapshot()
        if self.lifecycle_enabled: out["version"]=VERSION; out["lifecycleInference"]=self._lifecycle_snapshot()
        return out


class Handler(base.Handler): collector: MakerBookLifecycleInferenceCollector


def main()->int:
    if base.ASSET!="BTC": raise RuntimeError("8778 lifecycle V2 is BTC-only; 8779 ETH remains on V1")
    c=MakerBookLifecycleInferenceCollector(base.DB_PATH,base.TARGET_DB_PATH); c.start(); handler=type("MakerBookLifecycleInferenceHandler",(Handler,),{"collector":c}); server=ThreadingHTTPServer((base.HOST,base.PORT),handler)
    print(f"{VERSION} listening on http://{base.HOST}:{base.PORT}/state; target-fill anchored lifecycle + speculative cancellation inference; readOnly=true; liveOrdersAffected=false",flush=True)
    try: server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt: return 130
    finally: server.shutdown(); server.server_close(); c.stop()
    return 0

if __name__=="__main__": raise SystemExit(main())
