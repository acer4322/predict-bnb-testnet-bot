from __future__ import annotations
import json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';DB=ROOT/'data/wallet_maker_book_inference.db'

def main():
 s=json.loads((P/'r4_management_fresh_adaptation_v1_split.json').read_text());ids=[int(x) for x in s['backfillMarkets']]
 db=sqlite3.connect(DB); qs=','.join('?'*len(ids)); have={int(r[0]) for r in db.execute(f'select distinct market_id from maker_book_inference_v21_parent_lifecycles where market_id in ({qs})',ids)};db.close()
 missing=[m for m in ids if m not in have];batches=[missing[i:i+20] for i in range(0,len(missing),20)]
 for i,b in enumerate(batches,1):(P/f'r4_management_fresh_adaptation_v1_missing_batch{i}.json').write_text(json.dumps({'backfillMarkets':b},indent=2),encoding='utf-8')
 print(json.dumps({'missing':len(missing),'batches':[len(x) for x in batches]},indent=2))
if __name__=='__main__':main()
