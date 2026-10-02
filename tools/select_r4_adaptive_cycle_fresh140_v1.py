from pathlib import Path
import json, sqlite3

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PREV=json.loads((P/'r4_target_episodic_fresh100_cohort_v2.json').read_text(encoding='utf-8'))
cutoff=max(int(x['windowEndMs']) for x in PREV['markets'])
need=140

off=sqlite3.connect(f"file:{(ROOT/'data/target_wallet_official_v1.db').as_posix()}?mode=ro", uri=True)
off.execute('pragma query_only=on')
life=sqlite3.connect(f"file:{(ROOT/'data/wallet_maker_book_inference.db').as_posix()}?mode=ro", uri=True)
life.execute('pragma query_only=on')

cands=off.execute("select market_id,window_end_ms from target_markets where asset='BTC' and window_end_ms>? order by window_end_ms asc, market_id asc",(cutoff,)).fetchall()
rows=[]
for mid,endms in cands:
    n=life.execute("select count(*) from maker_book_inference_v21_parent_lifecycles indexed by idx_maker_book_v21_parent_market_time where market_id=? and placement_first_ms is not null and last_target_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",(int(mid),)).fetchone()[0]
    if int(n)>=20:
        rows.append({'marketId':int(mid),'windowEndMs':int(endms),'highConfidenceLifecycleRows':int(n)})
        if len(rows)>=need: break

off.close(); life.close()
if len(rows)<100:
    raise SystemExit(f'only {len(rows)} eligible newer markets after cutoff {cutoff}')
# Keep chronology split. If 140 available: 80/20/20/20 reserve. If fewer, use 60/20/20 and any remainder reserve.
if len(rows)>=140:
    use=rows[:140]; a=80; g=20; f=20
else:
    use=rows[:min(len(rows),120)]; a=len(use)-40; g=20; f=20
ids=[x['marketId'] for x in use]
out={
 'version':'R4_ADAPTIVE_CYCLE_FRESH_COHORT_V1',
 'status':'FROZEN_BEFORE_LABEL_EXTRACTION_OR_MODEL_SCORING',
 'selection':{'asset':'BTC','windowEndAfter':cutoff,'highConfidenceLifecycleMinRowsPerMarket':20,'order':'window_end_ms ASC, market_id ASC','outcomeBlind':True},
 'markets':use,
 'splits':{'adapt':ids[:a],'guard':ids[a:a+g],'final':ids[a+g:a+g+f],'reserve':ids[a+g+f:]},
 'guards':['chronology/data-availability only','no Target action content/winner/PNL/model score inspected for selection','strictly newer than fresh100 v2','reserve untouched by this first adaptive-cycle test']
}
path=P/'r4_adaptive_cycle_fresh_cohort_v1.json'; path.write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'eligibleFound':len(rows),'frozenMarkets':len(use),'splitSizes':{k:len(v) for k,v in out['splits'].items()},'cutoff':cutoff,'first':use[0] if use else None,'last':use[-1] if use else None,'out':str(path)},indent=2))
