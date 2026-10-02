from __future__ import annotations
import json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';MAIN=ROOT/'data/wallet_maker_book_inference.db'
SOURCES=[ROOT/'data/research/lan_worker_returns/r4-mgmt-fresh-b3/compact_result.db',ROOT/'data/research/lan_worker_returns/r4-mgmt-fresh-b4/compact_result.db']
TABLES=['maker_book_inference_v21_allocations','maker_book_inference_v21_parent_lifecycles','maker_book_inference_v21_cancel_candidates','maker_book_inference_v21_market_meta']

def main():
 split=json.loads((P/'r4_management_fresh_adaptation_v1_split.json').read_text());allids=[int(x) for x in split['backfillMarkets']]
 db=sqlite3.connect(MAIN);merged={}
 try:
  for si,src in enumerate(SOURCES):
   alias=f's{si}';db.execute(f"attach database ? as {alias}",(str(src),));db.execute('begin immediate')
   ids=[int(r[0]) for r in db.execute(f'select distinct market_id from {alias}.maker_book_inference_v21_market_meta')]
   qs=','.join('?'*len(ids))
   stats={}
   for t in TABLES:
    db.execute(f'delete from {t} where market_id in ({qs})',ids)
    cols=[r[1] for r in db.execute(f'pragma {alias}.table_info({t})')]
    db.execute(f"insert into {t}({','.join(cols)}) select {','.join(cols)} from {alias}.{t}")
    stats[t]=int(db.execute(f'select count(*) from {t} where market_id in ({qs})',ids).fetchone()[0])
   merged[str(src.relative_to(ROOT)).replace('\\','/')]={'markets':len(ids),'rows':stats}
   db.commit();db.execute(f'detach database {alias}')
 except Exception:
  db.rollback();raise
 finally:db.close()
 db=sqlite3.connect(MAIN);qs=','.join('?'*len(allids));life={int(r[0]):(int(r[1]),int(r[2] or 0)) for r in db.execute(f'''select market_id,count(*),sum(case when placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 then 1 else 0 end) from maker_book_inference_v21_parent_lifecycles where market_id in ({qs}) group by market_id''',allids)};db.close()
 rep={'version':'R4_MANAGEMENT_LIFECYCLE_COMPACT_MERGE_V1','researchOnly':True,'merged':merged,'coverage':{'requestedMarkets':len(allids),'marketsWithLifecycle':sum(v[0]>0 for v in life.values()),'marketsWithHighConfidence':sum(v[1]>0 for v in life.values()),'totalLifecycles':sum(v[0] for v in life.values()),'totalHighConfidence':sum(v[1] for v in life.values()),'missing':[m for m in allids if m not in life]}}
 (P/'r4_management_fresh_adaptation_v1_lifecycle_merge_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
