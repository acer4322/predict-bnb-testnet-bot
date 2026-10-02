from __future__ import annotations
import bisect,json,sqlite3,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_supervisor_mode_v0 as b
P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0';BOOK=ROOT/'data/wallet_maker_book_inference.db';TARGET=ROOT/'data/target_wallet_official_v1.db'

def main():
 split=json.loads((P/'r4_management_fresh_adaptation_v1_split.json').read_text(encoding='utf-8')); mids=[int(x) for x in split['backfillMarkets']]; raw=pd.read_csv(S/'target_general_state_fresh_increment_v3.csv');raw=raw[raw.market_id.isin(mids)].sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);raw=raw[raw.groupby('market_id',sort=False).cumcount().mod(2).eq(0)].copy().reset_index(drop=True)
 gb=raw.groupby('market_id',sort=False);mem={}
 for base in b.MEM_BASE:
  cur=pd.to_numeric(raw[base],errors='coerce')
  for lag in b.LAGS:mem[f'{base}_delta{lag}s']=cur-pd.to_numeric(gb[base].shift(lag),errors='coerce')
 d=pd.concat([raw,pd.DataFrame(mem,index=raw.index)],axis=1);bc=sqlite3.connect(BOOK);tc=sqlite3.connect(TARGET);placements={};takers={}
 for m in mids:
  placements[m]=[int(r[0]) for r in bc.execute("select placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 order by placement_first_ms",(m,))]
  takers[m]=[int(r[0]) for r in tc.execute("select first_event_ms from target_parent_orders where market_id=? and asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null order by first_event_ms",(m,))]
 bc.close();tc.close();mode=[]
 for r in d[['market_id','checkpoint_ms']].itertuples(index=False):
  m=int(r.market_id);t=int(r.checkpoint_ms);ts=takers[m];j=bisect.bisect_right(ts,t);tk=bool(j<len(ts) and 0<ts[j]-t<=3000);ps=placements[m];k=bisect.bisect_right(ps,t);mk=bool(k<len(ps) and 0<ps[k]-t<=1000);mode.append('TAKER' if tk else 'MAKER' if mk else 'HOLD')
 d['option_mode_v2']=mode;adapt=set(int(x) for x in split['adaptationMarkets']);val=set(int(x) for x in split['untouchedValidationMarkets']);d['split']=np.where(d.market_id.isin(adapt),'ADAPT','VALIDATION')
 features=b.CURRENT+[f'{base}_delta{lag}s' for base in b.MEM_BASE for lag in b.LAGS]
 outcols=['market_id','market_end_ms','checkpoint_ms','split','option_mode_v2']+features;d[outcols].to_csv(P/'r4_management_fresh_adaptation_v1_rows.csv',index=False)
 rep={'version':'R4_MANAGEMENT_FRESH_ADAPTATION_ROWS_V1','markets':int(d.market_id.nunique()),'rows':len(d),'adaptMarkets':int(d[d.split.eq('ADAPT')].market_id.nunique()),'validationMarkets':int(d[d.split.eq('VALIDATION')].market_id.nunique()),'counts':{k:g.option_mode_v2.value_counts().to_dict() for k,g in d.groupby('split')},'featureCount':len(features)};(P/'r4_management_fresh_adaptation_v1_rows_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
