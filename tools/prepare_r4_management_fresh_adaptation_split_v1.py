from __future__ import annotations
import json,sqlite3
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0'
FRESH=S/'target_general_state_fresh_increment_v3.csv';BOOK=ROOT/'data/wallet_maker_book_inference.db';TARGET=ROOT/'data/target_wallet_official_v1.db'
PILOT=P/'r4_fresh20_v21_lifecycle_backfill_report.json';OUT=P/'r4_management_fresh_adaptation_v1_split.json'

def chunks(xs,n=150):
 for i in range(0,len(xs),n):yield xs[i:i+n]

def main():
 d=pd.read_csv(FRESH,usecols=['market_id','market_end_ms']).drop_duplicates().sort_values(['market_end_ms','market_id']);mids=[int(x) for x in d.market_id.unique()]
 pilot=set(int(x) for x in json.loads(PILOT.read_text())['developmentMarkets']);meta=set();upd={};mk={}
 bc=sqlite3.connect(BOOK);tc=sqlite3.connect(TARGET)
 try:
  for blk in chunks(mids):
   qs=','.join('?'*len(blk))
   meta.update(int(r[0]) for r in bc.execute(f'select market_id from maker_book_inference_markets where market_id in ({qs})',blk))
   for r in bc.execute(f'select market_id,count(*) from maker_book_inference_updates where market_id in ({qs}) group by market_id',blk):upd[int(r[0])]=int(r[1])
   for r in tc.execute(f"select market_id,count(*) from wallet_shadow_target_events where market_id in ({qs}) and asset='BTC' and role='MAKER' and quote_type='BID' group by market_id",blk):mk[int(r[0])]=int(r[1])
 finally:bc.close();tc.close()
 eligible=[]
 for r in d.itertuples(index=False):
  m=int(r.market_id)
  if m in pilot or m not in meta or upd.get(m,0)<=0 or mk.get(m,0)<=0:continue
  eligible.append({'marketId':m,'marketEndMs':int(r.market_end_ms),'bookUpdates':upd[m],'targetMakerEvents':mk[m]})
  if len(eligible)>=100:break
 if len(eligible)<100:raise RuntimeError(f'only {len(eligible)} eligible')
 adapt=[x['marketId'] for x in eligible[:60]];val=[x['marketId'] for x in eligible[60:100]]
 out={'version':'R4_MANAGEMENT_FRESH_ADAPTATION_V1_SPLIT','researchOnly':True,'selectionUsesLabels':False,'selectionUsesModelScores':False,'adaptationMarkets':adapt,'untouchedValidationMarkets':val,'backfillMarkets':adapt+val,'rows':eligible}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'adaptation':len(adapt),'validation':len(val),'firstAdapt':adapt[:5],'firstValidation':val[:5]},indent=2))
if __name__=='__main__':main()
