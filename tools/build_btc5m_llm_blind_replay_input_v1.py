from pathlib import Path
import json,lzma,gzip,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.hftbacktest_true_match_calibration_v0 import normalize_match

mid=2484820
src=ROOT/f"data/execution_tape_v1/markets/{mid}.json.xz"
out=ROOT/"data/research/btc5m_llm_blind_replay_20260922_v1"
out.mkdir(parents=True,exist_ok=True)
d=json.loads(lzma.open(src,"rt",encoding="utf-8").read())
updates=d["updates"]
bids={}; asks={}; books=[]
for i,row in enumerate(updates):
    source,received,count,checkpoint,bs,az,changes=row
    if i==0 or checkpoint:
        if bs is not None: bids={float(k):float(v) for k,v in bs.items() if float(v)>0}
        if az is not None: asks={float(k):float(v) for k,v in az.items() if float(v)>0}
    if i>0:
        for p,before,after,delta in (changes or {}).get("bids",[]):
            p=float(p); after=float(after)
            if after>0: bids[p]=after
            else: bids.pop(p,None)
        for p,before,after,delta in (changes or {}).get("asks",[]):
            p=float(p); after=float(after)
            if after>0: asks[p]=after
            else: asks.pop(p,None)
    if bids and asks:
        bb=max(bids); ba=min(asks)
        books.append(dict(
            source_ms=int(source),received_ms=int(received),best_bid=float(bb),best_ask=float(ba),
            bids=[[float(p),float(bids[p])] for p in sorted(bids,reverse=True)[:5]],
            asks=[[float(p),float(asks[p])] for p in sorted(asks)[:5]],
        ))
market=d.get("market") or {}
start=int(market.get("first_seen_ms") or updates[0][1])
end=int(market.get("window_end_ms") or updates[-1][1])
public=dict(
    market_id=mid,start_ms=start,end_ms=end,
    quality_status="BLIND_REPLAY_INPUT_FROM_ARCHIVED_L2",
    books=books,scope="STRICT_PAST_PUBLIC_L2_ONLY_NO_TARGET_NO_WINNER"
)
trades=[]
for raw in d.get("matches") or []:
    if not isinstance(raw,dict):
        continue
    n=normalize_match(raw)
    if n is not None:
        trades.append(n)
trades.sort(key=lambda x:(int(x["tsMs"]),str(x.get("transactionHash") or ""),float(x["nativeYesPrice"]),float(x["qty"])))
execution=dict(market_id=mid,updates=updates,trades=trades)
for name,obj in [("PUBLIC_2484820.json.gz",public),("EXECUTION_2484820.json.gz",execution)]:
    with gzip.open(out/name,"wt",encoding="utf-8") as f:
        json.dump(obj,f,separators=(",",":"),ensure_ascii=False)
summary=dict(
    market_id=mid,start_ms=start,end_ms=end,updates=len(updates),books=len(books),
    matches_raw=len(d.get("matches") or []),normalized_trades=len(trades),
    first_book=books[0] if books else None,last_book=books[-1] if books else None,
    no_target=True,no_winner=True
)
(out/"INPUT_SUMMARY.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps(summary,ensure_ascii=False))
