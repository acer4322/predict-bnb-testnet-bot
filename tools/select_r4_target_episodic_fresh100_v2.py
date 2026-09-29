from pathlib import Path
import json, sqlite3
ROOT=Path(__file__).resolve().parents[1]
OFF=ROOT/'data/target_wallet_official_v1.db'
LIFE=ROOT/'data/wallet_maker_book_inference.db'
PREV=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_target_episodic_fresh160_cohort_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_target_episodic_fresh100_cohort_v2.json'
prev=json.loads(PREV.read_text(encoding='utf-8'))
cut=max(int(x['windowEndMs']) for x in prev['markets'])
c=sqlite3.connect(f'file:{OFF.as_posix()}?mode=ro',uri=True); c.execute('pragma query_only=on')
rows=c.execute("select market_id,window_end_ms from target_markets where asset='BTC' and window_end_ms>? order by window_end_ms,market_id",(cut,)).fetchall(); c.close()
ids=[int(r[0]) for r in rows]
lc=sqlite3.connect(f'file:{LIFE.as_posix()}?mode=ro',uri=True); lc.execute('pragma query_only=on')
counts={}
for i in range(0,len(ids),700):
    ch=ids[i:i+700]
    if not ch: continue
    ph=','.join('?'*len(ch))
    q=f"select market_id,count(*) from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and last_target_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 group by market_id"
    counts.update({int(m):int(n) for m,n in lc.execute(q,ch)})
lc.close()
sel=[]
for mid,we in rows:
    n=counts.get(int(mid),0)
    if n>=20: sel.append({'marketId':int(mid),'windowEndMs':int(we),'highConfidenceLifecycleRows':n})
    if len(sel)>=100: break
if len(sel)<100: raise SystemExit(f'eligible only {len(sel)}')
rep={'version':'R4_TARGET_EPISODIC_FRESH100_COHORT_V2','status':'FROZEN_BEFORE_ACTION_LABEL_EXTRACTION','selection':{'asset':'BTC','windowEndAfter':cut,'highConfidenceLifecycleMinRowsPerMarket':20,'order':'window_end_ms ASC, market_id ASC','outcomeBlind':True},'markets':sel,'splits':{'adapt60':[x['marketId'] for x in sel[:60]],'guard20':[x['marketId'] for x in sel[60:80]],'final20':[x['marketId'] for x in sel[80:100]]},'guards':['selection used only asset, chronology, and high-confidence lifecycle availability','no Target action content/winner/PNL inspected for selection','previous cohort untouchedReserve40 remains untouched']}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
print(json.dumps({'cutoff':cut,'selected':len(sel),'first':sel[0],'adaptEnd':sel[59],'guardEnd':sel[79],'finalEnd':sel[99],'artifact':str(OUT.relative_to(ROOT))},indent=2))