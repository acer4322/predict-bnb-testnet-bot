from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
WEI = 10**18
ET = ZoneInfo("America/New_York")
DEFAULT_WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"
DEFAULT_OFFSETS = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0)
UPDOWN_TITLE = re.compile(
    r"^(Bitcoin|BTC|Ethereum|ETH|BNB) Up or Down - (.+),\s*(\d{1,2})(?::(\d{2}))?(AM|PM)-"
    r"(\d{1,2})(?::(\d{2}))?(AM|PM) ET$", re.I,
)
ASSET_ALIASES = {"BITCOIN":"BTC","BTC":"BTC","ETHEREUM":"ETH","ETH":"ETH","BNB":"BNB"}
EDGE_BUCKETS = (("lt0", -math.inf, 0.0), ("0to1c",0.0,0.01), ("1to2c",0.01,0.02),
                ("2to5c",0.02,0.05), ("5to10c",0.05,0.10), ("10cPlus",0.10,math.inf))


def finite(value: Any) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return number if math.isfinite(number) else None


def dec_wei(value: Any) -> float | None:
    try: return float(value) / WEI
    except (TypeError, ValueError, OverflowError): return None


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text: return None
    try: return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError: return None


def dt_ms(value: Any) -> int | None:
    parsed = parse_iso(value)
    return int(parsed.timestamp() * 1000) if parsed is not None else None


def median(values: Iterable[float | None]) -> float | None:
    rows=[]
    for v in values:
        if v is None: continue
        x=finite(v)
        if x is not None: rows.append(x)
    return statistics.median(rows) if rows else None


def percentile(values: list[float], q: float) -> float | None:
    if not values: return None
    rows=sorted(float(v) for v in values)
    if len(rows)==1: return rows[0]
    x=max(0.0,min(1.0,q))*(len(rows)-1); lo=int(math.floor(x)); hi=min(len(rows)-1,lo+1); w=x-lo
    return rows[lo]*(1-w)+rows[hi]*w


def pearson(xs:list[float], ys:list[float]) -> float | None:
    if len(xs)!=len(ys) or len(xs)<2: return None
    mx=statistics.fmean(xs); my=statistics.fmean(ys)
    dx=[x-mx for x in xs]; dy=[y-my for y in ys]
    den=math.sqrt(sum(x*x for x in dx)*sum(y*y for y in dy))
    return sum(a*b for a,b in zip(dx,dy))/den if den>1e-15 else None


def norm_address(value:Any)->str:
    text=str(value or "").strip().lower()
    return text if text.startswith("0x") and len(text)==42 else ""


def signer(row:dict[str,Any])->str: return norm_address(row.get("signer") or row.get("maker"))

def outcome_name(row:dict[str,Any])->str:
    raw=row.get("outcome"); raw=raw.get("name") if isinstance(raw,dict) else raw
    return str(raw or "").upper().strip()

def quote_type(row:dict[str,Any])->str: return str(row.get("quoteType") or row.get("quote_type") or "").upper().strip()

def minute_of_day(hour:int, minute:int, ampm:str)->int:
    hour%=12
    if ampm.upper()=="PM": hour+=12
    return hour*60+minute


def market_descriptor(title:Any)->dict[str,Any]|None:
    m=UPDOWN_TITLE.match(str(title or "").strip())
    if not m: return None
    asset=ASSET_ALIASES.get(str(m.group(1)).upper())
    sh,sm,sap,eh,em,eap=int(m.group(3)),int(m.group(4) or 0),m.group(5),int(m.group(6)),int(m.group(7) or 0),m.group(8)
    start=minute_of_day(sh,sm,sap); end=minute_of_day(eh,em,eap)
    if end<start: end+=1440
    return {"asset":asset,"dateText":str(m.group(2)).strip(),"startHour":sh,"startMinute":sm,"startAmPm":sap,"durationMinutes":end-start}


def infer_market_bucket(title:Any,event_ms:int)->tuple[str,int,int]|None:
    d=market_descriptor(title)
    if not d: return None
    event_dt=datetime.fromtimestamp(event_ms/1000,tz=timezone.utc); local_year=event_dt.astimezone(ET).year
    candidates=[]
    for year in (local_year-1,local_year,local_year+1):
        parsed=None
        for fmt in ("%B %d %Y","%b %d %Y","%B %d, %Y","%b %d, %Y"):
            try: parsed=datetime.strptime(f"{d['dateText']} {year}",fmt); break
            except ValueError: pass
        if parsed:
            h=int(d["startHour"])%12+(12 if str(d["startAmPm"]).upper()=="PM" else 0)
            local=datetime(parsed.year,parsed.month,parsed.day,h,int(d["startMinute"]),tzinfo=ET)
            candidates.append(local.astimezone(timezone.utc))
    duration=max(1,int(d["durationMinutes"]))
    if candidates:
        chosen=min(candidates,key=lambda x:abs((x-event_dt).total_seconds()))
        return str(d["asset"]),duration,int(chosen.timestamp())
    return str(d["asset"]),duration,int(event_ms//(duration*60000))*duration*60


def parse_assets(value:str)->set[str]:
    out={ASSET_ALIASES.get(x.strip().upper(),x.strip().upper()) for x in str(value).split(",") if x.strip()}
    return {x for x in out if x in {"BTC","ETH","BNB"}}

def parse_durations(value:str)->set[int]:
    out=set()
    for p in str(value).split(","):
        t=p.strip().lower().removesuffix("m")
        if t: out.add(int(t))
    return {x for x in out if x>0}

def parse_offsets(value:str)->tuple[float,...]:
    rows=sorted({max(0.0,float(x.strip())) for x in str(value).split(",") if x.strip()})
    return tuple(rows) if rows else DEFAULT_OFFSETS


def api_get(path:str,params:dict[str,Any],api_key:str,*,timeout:float=20.0,retries:int=4)->dict[str,Any]:
    q=urlencode({k:v for k,v in params.items() if v is not None}); url=f"{API_BASE}{path}"+(f"?{q}" if q else "")
    headers={"x-api-key":api_key,"Accept":"application/json","User-Agent":"BTC-5M-Lab-FairValueExecutionV2/2.0"}
    last=None
    for attempt in range(max(1,retries)):
        try:
            with urlopen(Request(url,headers=headers),timeout=timeout) as r: payload=json.loads(r.read().decode("utf-8"))
            if not isinstance(payload,dict): raise RuntimeError("Unexpected Predict response shape")
            return payload
        except HTTPError as exc:
            try: detail=exc.read().decode("utf-8",errors="replace")
            except Exception: detail=""
            last=RuntimeError(f"Predict GET {path} HTTP {exc.code}: {detail[:500]}")
            if exc.code not in {429,500,502,503,504} or attempt+1>=retries: raise last
        except (URLError,TimeoutError,OSError) as exc:
            last=exc
            if attempt+1>=retries: raise RuntimeError(f"Predict GET {path} failed: {exc}") from exc
        time.sleep(min(5.0,.5*(2**attempt)))
    raise RuntimeError(f"Predict GET {path} failed: {last}")


def fetch_signer_matches(*,wallet:str,is_maker:bool,api_key:str,hours:float,pages:int,page_size:int)->tuple[list[dict[str,Any]],dict[str,Any]]:
    cutoff=datetime.now(timezone.utc)-timedelta(hours=max(.1,hours))
    return fetch_signer_matches_window(wallet=wallet,is_maker=is_maker,api_key=api_key,start=cutoff,end=datetime.now(timezone.utc)+timedelta(minutes=1),pages=pages,page_size=page_size)


def fetch_signer_matches_window(*,wallet:str,is_maker:bool,api_key:str,start:datetime,end:datetime,pages:int,page_size:int)->tuple[list[dict[str,Any]],dict[str,Any]]:
    start=start.astimezone(timezone.utc); end=end.astimezone(timezone.utc)
    kept=[]; after=None; seen=set(); oldest=None; newest=None; reached_start=False; pages_fetched=0; cursor_remaining=False
    for _ in range(max(1,pages)):
        payload=api_get("/v1/orders/matches",{"first":max(1,min(500,page_size)),"after":after,"signerAddress":wallet,"isSignerMaker":"true" if is_maker else "false"},api_key)
        pages_fetched+=1; rows=payload.get("data") if isinstance(payload.get("data"),list) else []
        if not rows: cursor_remaining=False; break
        old_seen=False
        for row in rows:
            if not isinstance(row,dict): continue
            dt=parse_iso(row.get("executedAt"))
            if dt:
                oldest=dt if oldest is None else min(oldest,dt); newest=dt if newest is None else max(newest,dt)
                if dt<start: old_seen=True; continue
                if dt>=end: continue
            kept.append(row)
        cursor=str(payload.get("cursor") or "").strip(); cursor_remaining=bool(cursor)
        if old_seen: reached_start=True; break
        if not cursor or cursor in seen: break
        seen.add(cursor); after=cursor
    return kept,{"pagesFetched":pages_fetched,"matchRowsKept":len(kept),"oldestFetchedUtc":oldest.isoformat() if oldest else None,"newestFetchedUtc":newest.isoformat() if newest else None,"reachedRequestedStart":reached_start,"truncatedByPageLimit":bool(pages_fetched>=max(1,pages) and cursor_remaining and not reached_start)}


def _scope_for_title(title:Any,event_ms:int,assets:set[str],durations:set[int])->tuple[str,int,int]|None:
    x=infer_market_bucket(title,event_ms)
    return x if x and x[0] in assets and x[1] in durations else None


def extract_execution_type(match:dict[str,Any],order:dict[str,Any]|None=None)->dict[str,Any]:
    candidates=[]
    for prefix,obj in (("match",match),("order",order or {})):
        if not isinstance(obj,dict): continue
        for key in ("matchType","executionType","settlementType","transactionType"):
            raw=obj.get(key)
            if raw is not None: candidates.append((f"{prefix}.{key}",str(raw).upper().strip()))
    for source,value in candidates:
        if value in {"MINT","NORMAL"}: return {"executionType":value,"executionTypeSource":source,"rawCandidates":candidates}
    return {"executionType":"UNKNOWN","executionTypeSource":None,"rawCandidates":candidates}


def load_maker_events(path:Path,assets:set[str],durations:set[int],cutoff_ms:int)->tuple[list[dict[str,Any]],Counter[str]]:
    payload=json.loads(path.read_text(encoding="utf-8")); rows=payload.get("parentOrdersByMakerHash") if isinstance(payload,dict) else None
    if not isinstance(rows,list): raise SystemExit("maker input does not contain parentOrdersByMakerHash")
    events=[]; recognized=Counter()
    for row in rows:
        if not isinstance(row,dict): continue
        event_ms=dt_ms(row.get("firstExecutedAt")); title=row.get("marketTitle")
        if event_ms is None: continue
        inf=infer_market_bucket(title,event_ms)
        if inf: recognized[f"{inf[0]}-{inf[1]}m"]+=1
        scoped=_scope_for_title(title,event_ms,assets,durations)
        if not scoped or event_ms<cutoff_ms: continue
        side=str(row.get("outcome") or "").upper().strip()
        if side not in {"UP","DOWN"} or str(row.get("quoteType") or "").upper().strip()!="BID": continue
        asset,duration,bucket=scoped
        events.append({"role":"MAKER","kind":"MAKER_PARENT","asset":asset,"durationMinutes":duration,"marketBucket":bucket,"marketId":int(row.get("marketId") or 0),"marketTitle":title,"side":side,"quoteType":"BID","price":finite(row.get("price")),"shares":finite(row.get("totalShares")),"costUsdtApprox":finite(row.get("totalCostUsdtApprox")),"eventMs":event_ms,"eventAt":row.get("firstExecutedAt"),"lastEventMs":dt_ms(row.get("lastExecutedAt")),"orderHash":row.get("makerHash"),"fillLegs":int(row.get("fillLegs") or 0),"executionType":"UNKNOWN"})
    return events,recognized


def extract_role_fill_events(matches:list[dict[str,Any]],wallet:str,assets:set[str],durations:set[int],role:str,*,bid_only:bool=True)->tuple[list[dict[str,Any]],dict[str,int]]:
    out=[]; counts=Counter(); role=role.upper()
    for mi,match in enumerate(matches):
        if not isinstance(match,dict): continue
        event_ms=dt_ms(match.get("executedAt")); market=match.get("market") if isinstance(match.get("market"),dict) else {}; title=market.get("title") or market.get("question")
        if event_ms is None: continue
        scoped=_scope_for_title(title,event_ms,assets,durations)
        if not scoped: counts["outOfScope"]+=1; continue
        if role=="TAKER":
            candidates=[]
            if isinstance(match.get("taker"),dict): candidates.append(match["taker"])
            if isinstance(match.get("takers"),list): candidates.extend(x for x in match["takers"] if isinstance(x,dict))
            if isinstance(match.get("order"),dict): candidates.append(match["order"])
        else:
            candidates=[x for x in (match.get("makers") or []) if isinstance(x,dict)]
            if isinstance(match.get("maker"),dict): candidates.append(match["maker"])
        asset,duration,bucket=scoped
        for ci,order in enumerate(candidates):
            if signer(order)!=wallet: continue
            counts["walletLegs"]+=1; qt=quote_type(order)
            if bid_only and qt!="BID": counts["nonBidIgnored"]+=1; continue
            side=outcome_name(order)
            if side not in {"UP","DOWN"}: counts["badOutcomeIgnored"]+=1; continue
            price=dec_wei(order.get("price")); shares=dec_wei(order.get("amount")); ex=extract_execution_type(match,order)
            out.append({"role":role,"kind":f"{role}_{qt or 'UNKNOWN'}_FILL","asset":asset,"durationMinutes":duration,"marketBucket":bucket,"marketId":int(market.get("id") or 0),"marketTitle":title,"side":side,"quoteType":qt,"price":price,"shares":shares,"costUsdtApprox":shares*price if shares is not None and price is not None else None,"eventMs":event_ms,"eventAt":match.get("executedAt"),"orderHash":order.get("hash") or order.get("orderHash"),"transactionHash":match.get("transactionHash"),"syntheticKey":f"{mi}:{ci}",**ex})
    dedup={}
    for row in out:
        key=(row["marketId"],row["side"],row["eventMs"],row.get("orderHash"),row.get("transactionHash"),row.get("price"),row.get("shares"),row.get("quoteType"))
        dedup[key]=row
    counts["dedupedEvents"]=len(dedup)
    return list(dedup.values()),dict(counts)


def extract_taker_buy_events(matches:list[dict[str,Any]],wallet:str,assets:set[str],durations:set[int]):
    return extract_role_fill_events(matches,wallet,assets,durations,"TAKER",bid_only=True)


def reconstruct_parents(fill_events:list[dict[str,Any]],role:str)->list[dict[str,Any]]:
    groups={}
    for i,e in enumerate(fill_events):
        h=str(e.get("orderHash") or "").lower().strip() or f"MISSING:{e.get('marketId')}:{e.get('side')}:{e.get('eventMs')}:{i}"
        key=(h,int(e.get("marketId") or 0),str(e.get("side")),str(e.get("quoteType") or ""))
        row=groups.setdefault(key,{"role":role,"kind":f"{role}_PARENT","asset":e.get("asset"),"durationMinutes":e.get("durationMinutes"),"marketBucket":e.get("marketBucket"),"marketId":e.get("marketId"),"marketTitle":e.get("marketTitle"),"side":e.get("side"),"quoteType":e.get("quoteType"),"orderHash":h,"shares":0.0,"costUsdtApprox":0.0,"fillLegs":0,"eventMs":int(e["eventMs"]),"lastEventMs":int(e["eventMs"]),"eventAt":e.get("eventAt"),"executionTypes":set()})
        qty=finite(e.get("shares")) or 0.0; price=finite(e.get("price")) or 0.0
        row["shares"]+=qty; row["costUsdtApprox"]+=qty*price; row["fillLegs"]+=1
        if int(e["eventMs"])<row["eventMs"]: row["eventMs"]=int(e["eventMs"]); row["eventAt"]=e.get("eventAt")
        row["lastEventMs"]=max(row["lastEventMs"],int(e["eventMs"])); row["executionTypes"].add(str(e.get("executionType") or "UNKNOWN"))
    result=[]
    for row in groups.values():
        row["price"]=row["costUsdtApprox"]/row["shares"] if row["shares"]>0 else None
        types=sorted(row.pop("executionTypes")); row["executionType"]=types[0] if len(types)==1 else ("MIXED" if types else "UNKNOWN")
        result.append(row)
    return sorted(result,key=lambda x:(int(x["eventMs"]),str(x["orderHash"])))


class TrajectoryIndex:
    CANDIDATE_COLUMNS=("seconds_left","poly_up_mid","poly_down_mid","binance_up_mid","binance_down_mid","binance_up_ask","binance_down_ask","mid_gap","executable_edge_up","executable_edge_down","poly_source_age_ms","poly_receipt_age_ms")
    def __init__(self,db_path:Path,assets:set[str])->None:
        if not db_path.exists(): raise SystemExit(f"observer DB not found: {db_path}")
        self.db=sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro",uri=True,timeout=5); self.db.row_factory=sqlite3.Row
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='multi_prediction_trajectory'").fetchone() is None: raise SystemExit(f"{db_path} does not contain multi_prediction_trajectory")
        available={str(r[1]) for r in self.db.execute("PRAGMA table_info(multi_prediction_trajectory)").fetchall()}; required={"asset","market_bucket","sampled_at_ms"}
        if required-available: raise SystemExit(f"multi_prediction_trajectory missing required columns: {sorted(required-available)}")
        self.available_columns=sorted(available); selected=["asset","market_bucket","sampled_at_ms"]+[c for c in self.CANDIDATE_COLUMNS if c in available]
        grouped=defaultdict(list); cov=defaultdict(list); self.min_ms=None; self.max_ms=None
        ph=",".join("?" for _ in sorted(assets)); sql=f"SELECT {','.join(selected)} FROM multi_prediction_trajectory WHERE UPPER(asset) IN ({ph}) ORDER BY asset,market_bucket,sampled_at_ms"
        for raw in self.db.execute(sql,tuple(sorted(assets))).fetchall():
            row=dict(raw); a=str(row["asset"]).upper(); b=int(row["market_bucket"]); ms=int(row["sampled_at_ms"]); grouped[(a,b)].append(row); cov[a].append(ms); self.min_ms=ms if self.min_ms is None else min(self.min_ms,ms); self.max_ms=ms if self.max_ms is None else max(self.max_ms,ms)
        self.by_market={k:([int(r["sampled_at_ms"]) for r in v],v) for k,v in grouped.items()}; self.coverage_by_asset={a:{"rows":len(t),"minMs":min(t),"maxMs":max(t),"markets":sum(1 for k in grouped if k[0]==a)} for a,t in cov.items()}
    def close(self): self.db.close()
    def at_or_before(self,asset:str,bucket:int,target_ms:int,max_age_ms:int):
        block=self.by_market.get((asset,int(bucket)))
        if not block: return None
        times,rows=block; pos=bisect.bisect_right(times,int(target_ms))-1
        if pos<0: return None
        age=int(target_ms)-times[pos]; return rows[pos] if 0<=age<=max_age_ms else None
    def at_or_after(self,asset:str,bucket:int,target_ms:int,max_age_ms:int):
        block=self.by_market.get((asset,int(bucket)))
        if not block: return None
        times,rows=block; pos=bisect.bisect_left(times,int(target_ms))
        if pos>=len(times): return None
        age=times[pos]-int(target_ms); return rows[pos] if 0<=age<=max_age_ms else None


def side_probability(sample:dict[str,Any],side:str,prefix:str)->float|None:
    direct=finite(sample.get(f"{prefix}_{'up' if side=='UP' else 'down'}_mid"))
    if direct is not None: return direct
    up=finite(sample.get(f"{prefix}_up_mid")); return up if side=="UP" else 1-up if up is not None else None

def side_ask(sample:dict[str,Any],side:str,prefix:str)->float|None: return finite(sample.get(f"{prefix}_{'up' if side=='UP' else 'down'}_ask"))

def snapshot_view(sample:dict[str,Any],event:dict[str,Any],requested_ms:int,direction:str)->dict[str,Any]:
    side=str(event["side"]); poly=side_probability(sample,side,"poly"); target=side_probability(sample,side,"binance"); ask=side_ask(sample,side,"binance"); price=finite(event.get("price")); ms=int(sample["sampled_at_ms"])
    return {"sampledAtMs":ms,"sampleDistanceFromRequestedMs":ms-requested_ms,"direction":direction,"secondsLeft":finite(sample.get("seconds_left")),"polyProbabilityForSide":poly,"binancePredictionProbabilityForSide":target,"binancePredictionAskForSide":ask,"polyMinusBinancePrediction":poly-target if poly is not None and target is not None else None,"polyEdgeVsExecutionPrice":poly-price if poly is not None and price is not None else None,"binancePredictionMidEdgeVsExecutionPrice":target-price if target is not None and price is not None else None,"binancePredictionAskEdgeVsExecutionPrice":ask-price if ask is not None and price is not None else None,"polySourceAgeMs":finite(sample.get("poly_source_age_ms")),"polyReceiptAgeMs":finite(sample.get("poly_receipt_age_ms"))}

def align_event(event:dict[str,Any],index:TrajectoryIndex,*,offsets:tuple[float,...],max_age_ms:int)->dict[str,Any]:
    before={}; after={}
    for off in offsets:
        key=f"{off:g}s"; pre_ms=int(event["eventMs"]-off*1000); post_ms=int(event["eventMs"]+off*1000); pre=index.at_or_before(event["asset"],event["marketBucket"],pre_ms,max_age_ms); post=index.at_or_after(event["asset"],event["marketBucket"],post_ms,max_age_ms)
        before[key]=snapshot_view(pre,event,pre_ms,"BEFORE") if pre else None; after[key]=snapshot_view(post,event,post_ms,"AFTER") if post else None
    out=dict(event); out["before"]=before; out["after"]=after; return out


def summarize_event_edges(events:list[dict[str,Any]],offsets:tuple[float,...])->dict[str,Any]:
    out={}
    for off in offsets:
        key=f"{off:g}s"; samples=[e.get("before",{}).get(key) for e in events]; samples=[s for s in samples if isinstance(s,dict)]; pe=[finite(s.get("polyEdgeVsExecutionPrice")) for s in samples]; pe=[x for x in pe if x is not None]; te=[finite(s.get("binancePredictionMidEdgeVsExecutionPrice")) for s in samples]; te=[x for x in te if x is not None]; gaps=[finite(s.get("polyMinusBinancePrediction")) for s in samples]; gaps=[x for x in gaps if x is not None]
        out[key]={"eventsWithObserverSample":len(samples),"polyEdgeVsExecutionPrice":{"count":len(pe),"median":median(pe),"positiveShare":sum(x>0 for x in pe)/len(pe) if pe else None,"atLeast1CentShare":sum(x>=.01 for x in pe)/len(pe) if pe else None,"atLeast2CentShare":sum(x>=.02 for x in pe)/len(pe) if pe else None,"atLeast5CentShare":sum(x>=.05 for x in pe)/len(pe) if pe else None},"binancePredictionMidEdgeVsExecutionPrice":{"count":len(te),"median":median(te),"positiveShare":sum(x>0 for x in te)/len(te) if te else None},"polyMinusBinancePrediction":{"count":len(gaps),"median":median(gaps),"medianAbs":median([abs(x) for x in gaps])}}
    return {"events":len(events),"byOffset":out}


def _maker_transition_rows(events:list[dict[str,Any]],reference_offset:float)->list[dict[str,Any]]:
    key=f"{reference_offset:g}s"; streams=defaultdict(list)
    for e in events: streams[(int(e["marketId"]),str(e["side"]))].append(e)
    rows=[]
    for stream in streams.values():
        stream.sort(key=lambda r:(int(r["eventMs"]),str(r.get("orderHash") or "")))
        for p,c in zip(stream,stream[1:]):
            p0=finite(p.get("price")); p1=finite(c.get("price")); s0=p.get("before",{}).get(key); s1=c.get("before",{}).get(key)
            if p0 is None or p1 is None or not isinstance(s0,dict) or not isinstance(s1,dict): continue
            po0=finite(s0.get("polyProbabilityForSide")); po1=finite(s1.get("polyProbabilityForSide")); b0=finite(s0.get("binancePredictionProbabilityForSide")); b1=finite(s1.get("binancePredictionProbabilityForSide")); pd=p1-p0; pod=po1-po0 if po0 is not None and po1 is not None else None; bd=b1-b0 if b0 is not None and b1 is not None else None
            def sm(a,b): return None if a is None or b is None or abs(a)<1e-12 or abs(b)<1e-12 else ((a>0)==(b>0))
            pm=sm(pd,pod); bm=sm(pd,bd); conflict=pod is not None and bd is not None and abs(pod)>1e-12 and abs(bd)>1e-12 and ((pod>0)!=(bd>0)); follows=None
            if conflict and abs(pd)>1e-12: follows="POLY" if (pd>0)==(pod>0) else "BINANCE_PREDICTION" if (pd>0)==(bd>0) else None
            rows.append({"asset":c["asset"],"durationMinutes":c["durationMinutes"],"marketId":c["marketId"],"side":c["side"],"previousHash":p.get("orderHash"),"nextHash":c.get("orderHash"),"gapSeconds":max(0,(int(c["eventMs"])-int(p["eventMs"]))/1000),"priceDelta":pd,"polyDelta":pod,"binancePredictionDelta":bd,"priceSameSignAsPoly":pm,"priceSameSignAsBinancePrediction":bm,"polyBinanceMovementConflict":conflict,"conflictFollows":follows})
    return rows


def maker_repricing(events:list[dict[str,Any]],reference_offset:float,min_gap_seconds:float=0.0,min_abs_price_delta:float=0.0)->dict[str,Any]:
    rows=[r for r in _maker_transition_rows(events,reference_offset) if r["gapSeconds"]>=min_gap_seconds and abs(float(r["priceDelta"]))>=min_abs_price_delta]
    def src(delta_field,match_field):
        usable=[r for r in rows if r[match_field] is not None and finite(r.get(delta_field)) is not None]; x=[float(r["priceDelta"]) for r in usable]; y=[float(r[delta_field]) for r in usable]
        return {"directionalTransitions":len(usable),"sameDirectionCount":sum(r[match_field] is True for r in usable),"sameDirectionShare":sum(r[match_field] is True for r in usable)/len(usable) if usable else None,"deltaCorrelation":pearson(x,y),"medianAbsDeltaDifference":median([abs(a-b) for a,b in zip(x,y)])}
    conflicts=[r for r in rows if r["polyBinanceMovementConflict"]]; fc=Counter(str(r.get("conflictFollows") or "NEITHER") for r in conflicts)
    return {"referenceOffsetSeconds":reference_offset,"minGapSeconds":min_gap_seconds,"minAbsTargetPriceDelta":min_abs_price_delta,"transitionsWithSnapshots":len(rows),"vsPoly":src("polyDelta","priceSameSignAsPoly"),"vsBinancePrediction":src("binancePredictionDelta","priceSameSignAsBinancePrediction"),"polyVsBinancePredictionMovementConflicts":{"count":len(conflicts),"followsPoly":fc["POLY"],"followsBinancePrediction":fc["BINANCE_PREDICTION"],"neitherOrFlat":fc["NEITHER"],"followsPolyShare":fc["POLY"]/len(conflicts) if conflicts else None,"followsBinancePredictionShare":fc["BINANCE_PREDICTION"]/len(conflicts) if conflicts else None},"examples":rows[:200]}


def taker_stale_quote_tests(events:list[dict[str,Any]],reference_offset:float,post_offsets:tuple[float,...])->dict[str,Any]:
    pre_key=f"{reference_offset:g}s"; out={}
    for off in post_offsets:
        post_key=f"{off:g}s"; rows=[]
        for e in events:
            pre=e.get("before",{}).get(pre_key); post=e.get("after",{}).get(post_key)
            if not isinstance(pre,dict) or not isinstance(post,dict): continue
            pp=finite(pre.get("polyProbabilityForSide")); bp=finite(pre.get("binancePredictionProbabilityForSide")); postp=finite(post.get("polyProbabilityForSide")); postb=finite(post.get("binancePredictionProbabilityForSide")); price=finite(e.get("price"))
            if None in (pp,bp,postb,price): continue
            gap=pp-bp; move=postb-bp; toward=None if abs(gap)<1e-12 or abs(move)<1e-12 else ((gap>0)==(move>0)); postgap=postp-postb if postp is not None else None
            rows.append({"asset":e["asset"],"durationMinutes":e["durationMinutes"],"marketId":e["marketId"],"side":e["side"],"eventAt":e.get("eventAt"),"price":price,"prePolyProbability":pp,"preBinancePredictionProbability":bp,"prePolyMinusBinancePrediction":gap,"prePolyEdgeVsExecutionPrice":pp-price,"postBinancePredictionProbability":postb,"postBinancePredictionMove":move,"postPolyProbability":postp,"movedTowardPrePoly":toward,"polyBinanceGapReduced":abs(postgap)<abs(gap) if postgap is not None else None})
        pos=[r for r in rows if r["prePolyEdgeVsExecutionPrice"]>0]; one=[r for r in rows if r["prePolyEdgeVsExecutionPrice"]>=.01]; toward=[r for r in rows if r["movedTowardPrePoly"] is not None]; red=[r for r in rows if r["polyBinanceGapReduced"] is not None]
        out[post_key]={"eventsWithPreAndPostSamples":len(rows),"prePolyPositiveEdgeShare":len(pos)/len(rows) if rows else None,"prePolyAtLeast1CentEdgeShare":len(one)/len(rows) if rows else None,"binancePredictionMovedTowardPrePolyShare":sum(r["movedTowardPrePoly"] is True for r in toward)/len(toward) if toward else None,"polyBinanceGapReducedShare":sum(r["polyBinanceGapReduced"] is True for r in red)/len(red) if red else None,"atLeast1CentPolyEdgeAndTargetMovedTowardPolyShareOfAll":sum(r["movedTowardPrePoly"] is True for r in one)/len(rows) if rows else None,"medianPostBinancePredictionMove":median([r["postBinancePredictionMove"] for r in rows]),"examples":sorted(rows,key=lambda r:r["prePolyEdgeVsExecutionPrice"],reverse=True)[:50]}
    return {"referencePreOffsetSeconds":reference_offset,"byPostOffset":out}


def fixed_share_profile(events:list[dict[str,Any]],tol:float=1e-6)->dict[str,Any]:
    vals=[finite(e.get("shares")) for e in events]; vals=[x for x in vals if x is not None and x>0]
    if not vals: return {"events":0,"modeShares":None}
    counts=Counter(round(x,6) for x in vals); mode,count=counts.most_common(1)[0]
    return {"events":len(vals),"modeShares":mode,"modeCount":count,"modeShare":count/len(vals),"belowModeCount":sum(x<mode-tol for x in vals),"aboveModeCount":sum(x>mode+tol for x in vals),"medianShares":median(vals),"p90Shares":percentile(vals,.9),"topQuantities":[{"shares":q,"count":c,"share":c/len(vals)} for q,c in counts.most_common(10)]}


def ladder_profile(events:list[dict[str,Any]],tick:float=.01)->dict[str,Any]:
    by_stream=defaultdict(list)
    for e in events:
        p=finite(e.get("price"))
        if p is not None: by_stream[(int(e["marketId"]),str(e["side"]))].append(e)
    streams=[]
    for (mid,side),rows in by_stream.items():
        prices=sorted(set(round(float(e["price"]),6) for e in rows)); diffs=[b-a for a,b in zip(prices,prices[1:])]
        streams.append({"marketId":mid,"side":side,"uniquePriceLevels":len(prices),"priceSpan":prices[-1]-prices[0] if prices else None,"oneTickAdjacentShare":sum(abs(d-tick)<=.0015 for d in diffs)/len(diffs) if diffs else None,"minPrice":prices[0] if prices else None,"maxPrice":prices[-1] if prices else None})
    clusters=defaultdict(list)
    for e in events: clusters[(int(e["marketId"]),str(e["side"]),int(e["eventMs"])//1000)].append(e)
    cluster_rows=[]
    for (mid,side,sec),rows in clusters.items():
        prices=sorted(set(round(float(e["price"]),6) for e in rows if finite(e.get("price")) is not None))
        if len(prices)<3: continue
        diffs=[b-a for a,b in zip(prices,prices[1:])]
        cluster_rows.append({"marketId":mid,"side":side,"epochSecond":sec,"distinctParents":len({str(e.get('orderHash')) for e in rows}),"distinctPriceLevels":len(prices),"priceSpan":prices[-1]-prices[0],"oneTickAdjacentShare":sum(abs(d-tick)<=.0015 for d in diffs)/len(diffs) if diffs else None,"prices":prices[:50]})
    levels=[r["uniquePriceLevels"] for r in streams]; spans=[r["priceSpan"] for r in streams if r["priceSpan"] is not None]; one=[r["oneTickAdjacentShare"] for r in streams if r["oneTickAdjacentShare"] is not None]; cl=[r["distinctPriceLevels"] for r in cluster_rows]
    return {"marketSideStreams":len(streams),"uniquePriceLevelsPerMarketSide":{"median":median(levels),"p90":percentile([float(x) for x in levels],.9),"max":max(levels) if levels else None},"priceSpan":{"median":median(spans),"p90":percentile(spans,.9)},"oneCentAdjacentShare":{"median":median(one)},"sameSecondLadderClusters":{"count":len(cluster_rows),"medianDistinctPriceLevels":median(cl),"p90":percentile([float(x) for x in cl],.9),"maxDistinctPriceLevels":max(cl) if cl else None,"examples":sorted(cluster_rows,key=lambda r:r["distinctPriceLevels"],reverse=True)[:50]}}


def edge_bucket_summary(events:list[dict[str,Any]],reference_offset:float,post_offsets:tuple[float,...])->dict[str,Any]:
    pre_key=f"{reference_offset:g}s"; result={}
    for name,lo,hi in EDGE_BUCKETS:
        selected=[]
        for e in events:
            pre=e.get("before",{}).get(pre_key)
            if not isinstance(pre,dict): continue
            edge=finite(pre.get("polyEdgeVsExecutionPrice"))
            if edge is not None and lo<=edge<hi: selected.append((e,edge,pre))
        block={"parents":len(selected),"shareOfParents":len(selected)/len(events) if events else None,"byPostOffset":{}}
        for off in post_offsets:
            key=f"{off:g}s"; usable=[]
            for e,edge,pre in selected:
                post=e.get("after",{}).get(key)
                if not isinstance(post,dict): continue
                pp=finite(pre.get("polyProbabilityForSide")); bp=finite(pre.get("binancePredictionProbabilityForSide")); postb=finite(post.get("binancePredictionProbabilityForSide")); postp=finite(post.get("polyProbabilityForSide"))
                if pp is None or bp is None or postb is None: continue
                gap=pp-bp; move=postb-bp; toward=None if abs(gap)<1e-12 or abs(move)<1e-12 else ((gap>0)==(move>0)); gapred=abs(postp-postb)<abs(gap) if postp is not None else None
                usable.append((move,toward,gapred))
            toward=[x for x in usable if x[1] is not None]; red=[x for x in usable if x[2] is not None]
            block["byPostOffset"][key]={"events":len(usable),"movedTowardPrePolyShare":sum(x[1] is True for x in toward)/len(toward) if toward else None,"gapReducedShare":sum(x[2] is True for x in red)/len(red) if red else None,"medianTargetMove":median([x[0] for x in usable])}
        result[name]=block
    by_type={}
    for typ in sorted({str(e.get("executionType") or "UNKNOWN") for e in events}):
        sub=[e for e in events if str(e.get("executionType") or "UNKNOWN")==typ]
        by_type[typ]={"parents":len(sub),"share":len(sub)/len(events) if events else None}
    return {"referencePreOffsetSeconds":reference_offset,"buckets":result,"executionTypeCoverage":by_type}


def role_transition_summary(maker:list[dict[str,Any]],taker:list[dict[str,Any]])->dict[str,Any]:
    streams=defaultdict(list)
    for e in maker+taker: streams[int(e["marketId"])].append(e)
    acc=defaultdict(lambda:{"count":0,"gaps":[],"same":0}); examples=[]
    for mid,s in streams.items():
        s.sort(key=lambda r:(int(r["eventMs"]),str(r["role"]),str(r.get("orderHash") or "")))
        for p,c in zip(s,s[1:]):
            label=f"{p['role']}->{c['role']}"; gap=max(0,(int(c["eventMs"])-int(p["eventMs"]))/1000); x=acc[label]; x["count"]+=1;x["gaps"].append(gap);x["same"]+=int(p.get("side")==c.get("side"))
            if p["role"]!=c["role"] and len(examples)<100: examples.append({"marketId":mid,"fromRole":p["role"],"toRole":c["role"],"gapSeconds":gap,"fromSide":p.get("side"),"toSide":c.get("side"),"sameSide":p.get("side")==c.get("side")})
    out={}
    for label,x in sorted(acc.items()):
        g=x["gaps"]; out[label]={"count":x["count"],"medianGapSeconds":median(g),"within1sShare":sum(v<=1 for v in g)/len(g) if g else None,"within3sShare":sum(v<=3 for v in g)/len(g) if g else None,"within5sShare":sum(v<=5 for v in g)/len(g) if g else None,"sameSideShare":x["same"]/x["count"] if x["count"] else None}
    return {"transitions":out,"examples":examples}


def phase_name(seconds_left:float|None)->str:
    if seconds_left is None: return "UNKNOWN"
    if seconds_left>240:return ">240s"
    if seconds_left>180:return "180-240s"
    if seconds_left>120:return "120-180s"
    if seconds_left>60:return "60-120s"
    if seconds_left>30:return "30-60s"
    return "<=30s"

def market_phase_summary(maker:list[dict[str,Any]],taker:list[dict[str,Any]],reference_offset:float)->dict[str,Any]:
    key=f"{reference_offset:g}s"; groups=defaultdict(list)
    for e in maker+taker:
        s=e.get("before",{}).get(key); groups[phase_name(finite(s.get("secondsLeft")) if isinstance(s,dict) else None)].append(e)
    out={}
    for phase,rows in groups.items():
        m=[r for r in rows if r["role"]=="MAKER"]; t=[r for r in rows if r["role"]=="TAKER"]; edges=[]
        for r in rows:
            s=r.get("before",{}).get(key)
            if isinstance(s,dict):
                v=finite(s.get("polyEdgeVsExecutionPrice"))
                if v is not None: edges.append(v)
        out[phase]={"parents":len(rows),"makerParents":len(m),"takerParents":len(t),"takerShare":len(t)/len(rows) if rows else None,"medianPolyEdgeVsExecution":median(edges),"positivePolyEdgeShare":sum(v>0 for v in edges)/len(edges) if edges else None}
    return out


def iso_ms(ms:int|None)->str|None: return datetime.fromtimestamp(ms/1000,tz=timezone.utc).isoformat() if ms is not None else None


def main()->int:
    p=argparse.ArgumentParser(description="V2 read-only reconstruction of Predict wallet fair-value execution: maker ladder, parent-level taker bursts, role switching, and edge buckets.")
    p.add_argument("--wallet",default=DEFAULT_WALLET); p.add_argument("--maker-input",default="data/6da6_maker_hash_profile.json"); p.add_argument("--observer-db",default="data/multi_prediction_observer.db"); p.add_argument("--assets",default="BTC"); p.add_argument("--durations",default="5"); p.add_argument("--hours",type=float,default=24); p.add_argument("--pages",type=int,default=100); p.add_argument("--page-size",type=int,default=500); p.add_argument("--max-sample-age-ms",type=int,default=2500); p.add_argument("--offsets",default="0,0.5,1,2,3,5"); p.add_argument("--post-offsets",default="0.5,1,2,3,5"); p.add_argument("--skip-takers",action="store_true"); p.add_argument("--recenter-min-gap-seconds",type=float,default=5); p.add_argument("--recenter-min-price-delta",type=float,default=.02); p.add_argument("--output",default="data/6da6_fair_value_execution_v2.json")
    a=p.parse_args(); assets=parse_assets(a.assets); durations=parse_durations(a.durations); offsets=parse_offsets(a.offsets); post=parse_offsets(a.post_offsets); max_age=max(100,int(a.max_sample_age_ms)); ref=1.0 if 1.0 in offsets else offsets[0]; wallet=norm_address(a.wallet)
    if not wallet: raise SystemExit("--wallet must be a 0x address")
    cutoff_ms=int((datetime.now(timezone.utc)-timedelta(hours=max(.1,a.hours))).timestamp()*1000); maker,recognized=load_maker_events(Path(a.maker_input),assets,durations,cutoff_ms)
    api_key=str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip(); taker_fills=[]; fetch_cov=None; parse_counts={}; note=None
    if not a.skip_takers:
        if not api_key: note="PREDICT_FUN_API_KEY unavailable; maker-only V2 analysis produced"
        else:
            matches,fetch_cov=fetch_signer_matches(wallet=wallet,is_maker=False,api_key=api_key,hours=a.hours,pages=a.pages,page_size=a.page_size); taker_fills,parse_counts=extract_taker_buy_events(matches,wallet,assets,durations)
    taker=reconstruct_parents(taker_fills,"TAKER")
    index=TrajectoryIndex(Path(a.observer_db),assets); dbmin,dbmax=index.min_ms,index.max_ms
    try:
        all_offsets=tuple(sorted(set(offsets+post)))
        maker_a=[align_event(e,index,offsets=all_offsets,max_age_ms=max_age) for e in maker]; taker_a=[align_event(e,index,offsets=all_offsets,max_age_ms=max_age) for e in taker]
    finally: index.close()
    def cov_fmt():
        return {k:{**v,"minUtc":iso_ms(v["minMs"]),"maxUtc":iso_ms(v["maxMs"])} for k,v in index.coverage_by_asset.items()}
    types=Counter(e.get("executionType") or "UNKNOWN" for e in taker)
    summary={"version":"PREDICT_WALLET_FAIR_VALUE_EXECUTION_V2","wallet":wallet,"makerInput":a.maker_input,"observerDb":a.observer_db,"filter":f"assets={','.join(sorted(assets))}; durations={','.join(map(str,sorted(durations)))}m; BUY/BID only","requestedHours":a.hours,"recognizedMakerInputScopeParentCounts":dict(recognized),"makerParentsInScope":len(maker_a),"takerFillLegsInScope":len(taker_fills),"takerParentsReconstructed":len(taker_a),"takerParentFillFragmentation":{"multiFillParents":sum(int(e.get("fillLegs") or 0)>1 for e in taker),"shareFillLegsFromMultiFillParents":sum(int(e.get("fillLegs") or 0) for e in taker if int(e.get("fillLegs") or 0)>1)/len(taker_fills) if taker_fills else None,"fillLegsPerParent":{"median":median([e["fillLegs"] for e in taker]),"p90":percentile([float(e["fillLegs"]) for e in taker],.9),"p99":percentile([float(e["fillLegs"]) for e in taker],.99),"max":max((e["fillLegs"] for e in taker),default=None)}},"takerParseCounts":parse_counts,"takerFetchCoverage":fetch_cov,"takerFetchNote":note,"executionTypeCoverage":{"note":"MINT/NORMAL is reported only when the public match payload explicitly exposes such a field; UNKNOWN is not inferred.","counts":dict(types)},"observerCoverageUtc":{"min":iso_ms(dbmin),"max":iso_ms(dbmax)},"observerCoverageByAsset":cov_fmt(),"observerAvailableColumns":index.available_columns,"offsetSeconds":list(offsets),"postOffsetSeconds":list(post),"referenceOffsetSeconds":ref,"makerFixedShareFingerprint":fixed_share_profile(maker_a),"makerLadder":ladder_profile(maker_a),"makerFirstFill":summarize_event_edges(maker_a,offsets),"takerParentFirstFill":summarize_event_edges(taker_a,offsets),"makerRepricingRaw":maker_repricing(maker_a,ref),"makerRecenterFiltered":maker_repricing(maker_a,ref,max(0,a.recenter_min_gap_seconds),max(0,a.recenter_min_price_delta)),"takerParentEdgeBuckets":edge_bucket_summary(taker_a,ref,post),"takerParentFairValueFollowThrough":taker_stale_quote_tests(taker_a,ref,post),"parentRoleTransitions":role_transition_summary(maker_a,taker_a),"marketPhase":market_phase_summary(maker_a,taker_a,ref)}
    report={"summary":summary,"interpretationGuide":{"grid":"Same-second multi-price fixed-size maker parents support a layered ladder hypothesis, but firstExecutedAt is a fill timestamp, not placement time.","recenter":"makerRecenterFiltered removes much same-second ladder noise before comparing target price movement with Poly/Binance Prediction movement.","takerParent":"Taker fill legs are grouped by orderHash before edge and role-transition tests so one aggressive parent split across many makers is not counted as many decisions.","executionType":"Public match history may not expose MINT/NORMAL. The profiler never fabricates a split; absent explicit fields are UNKNOWN.","causality":"Poly alignment does not prove direct Poly usage; Poly and the wallet may share another upstream source."},"makerEvents":maker_a,"takerParentEvents":taker_a}
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8"); print(json.dumps(summary,indent=2,ensure_ascii=False)); print(f"wrote {out.resolve()}"); return 0

if __name__=="__main__": raise SystemExit(main())
