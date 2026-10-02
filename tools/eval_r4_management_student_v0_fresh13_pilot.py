from __future__ import annotations
import bisect,json,sqlite3,joblib,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_supervisor_mode_v0 as b
P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0';BOOK=ROOT/'data/wallet_maker_book_inference.db';TARGET=ROOT/'data/target_wallet_official_v1.db'

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);o={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{},'actualCounts':pd.Series(y).value_counts().to_dict(),'predictedCounts':pd.Series(p).value_counts().to_dict()}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;o['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return o

def main():
 rep=json.loads((P/'r4_fresh20_v21_lifecycle_backfill_report.json').read_text());mids=[int(r['marketId']) for r in rep['marketRows'] if int(r['highConfidence'])>0];raw=pd.read_csv(S/'target_general_state_fresh_increment_v3.csv');raw=raw[raw.market_id.isin(mids)].sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);raw=raw[raw.groupby('market_id',sort=False).cumcount().mod(2).eq(0)].copy().reset_index(drop=True)
 gb=raw.groupby('market_id',sort=False);mem={}
 for base in b.MEM_BASE:
  cur=pd.to_numeric(raw[base],errors='coerce')
  for lag in b.LAGS:mem[f'{base}_delta{lag}s']=cur-pd.to_numeric(gb[base].shift(lag),errors='coerce')
 d=pd.concat([raw,pd.DataFrame(mem,index=raw.index)],axis=1);bc=sqlite3.connect(BOOK);tc=sqlite3.connect(TARGET);placements={};takers={}
 for m in mids:
  placements[m]=[(int(t),str(s)) for t,s in bc.execute("select placement_first_ms,target_side from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 order by placement_first_ms",(m,))]
  takers[m]=[int(r[0]) for r in tc.execute("select first_event_ms from target_parent_orders where market_id=? and asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null order by first_event_ms",(m,))]
 bc.close();tc.close();mode=[]
 for r in d[['market_id','checkpoint_ms']].itertuples(index=False):
  m=int(r.market_id);t=int(r.checkpoint_ms);ts=takers[m];j=bisect.bisect_right(ts,t);tk=bool(j<len(ts) and 0<ts[j]-t<=3000);mk=any(t<p<=t+1000 for p,_ in placements[m]);mode.append('TAKER' if tk else 'MAKER' if mk else 'HOLD')
 d['option_mode_v2']=mode;d.to_csv(P/'r4_management_student_v0_fresh13_pilot_rows.csv',index=False);art=joblib.load(P/'r4_management_student_v0_joint_calibrated.joblib');f=art['features'];z=d.copy();sc=score(z.option_mode_v2,art['model'].predict(z[f]));out={'version':'R4_MANAGEMENT_STUDENT_V0_FRESH13_PILOT','researchOnly':True,'markets':len(mids),'rows':len(z),'modeCounts':z.option_mode_v2.value_counts().to_dict(),'score':sc,'note':'Fresh pilot after canonical V2.1 lifecycle backfill; HGB evaluates native NaNs; not a graduation cohort due to small market count.'};(P/'r4_management_student_v0_fresh13_pilot_report.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
