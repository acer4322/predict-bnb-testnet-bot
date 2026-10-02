from __future__ import annotations
import json, math, sqlite3, sys
from pathlib import Path
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
DB=ROOT/'data/hft_forward_paper_v1.db'; D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
F=['predictUpBid','predictUpAsk','predictDownBid','predictDownAsk','spotQueueImbalance','spotTakerImbalance250ms','spotTakerImbalance1s','futuresQueueImbalance','futuresTakerImbalance250ms','futuresTakerImbalance1s','perpSpotBasisBps','directionScore','spotReturn250msBps','spotReturn1sBps','spotReturn3sBps','spotReturn5sBps','futuresReturn250msBps','futuresReturn1sBps','futuresReturn3sBps','futuresReturn5sBps','spotMinusChainlinkBps','predictSourceAgeMs','chainlinkSourceAgeMs']
def fin(v):
 try:
  x=float(v); return x if math.isfinite(x) else np.nan
 except:return np.nan
def main():
 c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 rr=[dict(r) for r in c.execute("select market_id,window_end_ms,realized_pnl_usdt from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' and realized_pnl_usdt is not null order by window_end_ms,market_id")];c.close()
 rows=[]
 for r in rr:
  try:s=load_public_snapshots(int(r['market_id']))[0]
  except:continue
  rows.append({'marketId':int(r['market_id']),'windowEndMs':int(r['window_end_ms']),'pnl':float(r['realized_pnl_usdt']),'x':[fin(s.get(k)) for k in F]})
 cut=max(1,int(len(rows)*.7)); tr=rows[:cut]; te=rows[cut:]
 Xtr=np.asarray([r['x'] for r in tr]); Xte=np.asarray([r['x'] for r in te]); ytr=np.asarray([r['pnl']>0 for r in tr],int); yte=np.asarray([r['pnl']>0 for r in te],int)
 clf=Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=7,l2_regularization=5,learning_rate=.05,random_state=31))]);clf.fit(Xtr,ytr); p=clf.predict_proba(Xte)[:,1]
 # Natural 0.5 threshold, not PnL tuned.
 trade=p>=.5
 test_rows=[]
 for r,pp,a in zip(te,p,trade):test_rows.append({'marketId':r['marketId'],'pnl':r['pnl'],'pPositive':float(pp),'trade':bool(a),'policyPnl':r['pnl'] if a else 0.0})
 agg={'markets':len(rows),'train':len(tr),'test':len(te),'trainPnl':sum(r['pnl'] for r in tr),'testBaselinePnl':sum(r['pnl'] for r in te),'testPolicyPnl':sum(r['pnl'] if a else 0.0 for r,a in zip(te,trade)),'testTraded':int(trade.sum()),'testSkipped':int((~trade).sum()),'testAuc':float(roc_auc_score(yte,p)) if len(set(yte))>1 else None,'testAp':float(average_precision_score(yte,p)),'testBalancedAccuracy':float(balanced_accuracy_score(yte,trade.astype(int)))}
 out={'version':'R2_MARKET_ADMISSION_V0','researchOnly':True,'dreamFillAllowed':False,'policy':'Before any R2 action, classify whether unchanged HFT R2 market PnL will be positive using only the first strict-past public snapshot; trade if p>=0.5, else abstain. Teacher is earlier realized PnL sign; current chronological 30 markets untouched. No threshold sweep.','features':F,'aggregate':agg,'rows':test_rows}
 path=D/'r2_market_admission_v0.json';path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'report':str(path),'aggregate':agg,'traded':[r for r in test_rows if r['trade']]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
