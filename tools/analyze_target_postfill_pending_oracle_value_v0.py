from __future__ import annotations
import json, math, sqlite3
from pathlib import Path
from collections import defaultdict
import pandas as pd
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import balanced_accuracy_score, accuracy_score, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_mature_mm_knob_projection_v0_reactions.csv'
BOOK_DB=ROOT/'data/wallet_maker_book_inference.db'
OUT=ROOT/'data/research/target_postfill_pending_oracle_value_v0_report.json'
AUG=ROOT/'data/research/target_postfill_pending_oracle_value_v0.csv'
LABEL='reactionClass'
MIN_PC=.85;MIN_FC=.70;MIN_CONF=.75


def ro(p):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c

def load_parents(markets):
 c=ro(BOOK_DB);out=defaultdict(list)
 try:
  mids=sorted(set(int(x) for x in markets))
  for k in range(0,len(mids),300):
   b=mids[k:k+300];qs=','.join('?'*len(b))
   q=f'''select parent_id,market_id,target_side,target_price,placement_first_ms,placement_last_ms,last_target_ms,expected_parent_shares,target_filled_shares,resting_ms,placement_coverage,fill_allocation_coverage,confidence from maker_book_inference_v21_parent_lifecycles where market_id in ({qs}) and placement_last_ms is not null and placement_coverage>=? and fill_allocation_coverage>=? and confidence>=? order by market_id,placement_last_ms'''
   for r in c.execute(q,[*b,MIN_PC,MIN_FC,MIN_CONF]):out[int(r['market_id'])].append(dict(r))
 finally:c.close()
 return out

def pending_features(row,parents):
 mid=int(row.marketId); cp=int(row.fillMs)+1000; side=str(row.side);opp='DOWN' if side=='UP' else 'UP'
 active=[]
 for p in parents.get(mid,[]):
  pl=int(p['placement_last_ms'])
  if pl>cp: break
  if int(p['last_target_ms'])<=cp: continue
  # upper-bound ownership/survival oracle: placement existed by cp, later Target fill confirms it persisted.
  sh=max(0.0,float(p.get('expected_parent_shares') or 0)-float(p.get('target_filled_shares') or 0))
  # if expected/filled bookkeeping is unavailable or yields zero, retain count/age signal only
  active.append((str(p['target_side']),max(0.0,sh),float(cp-pl),float(p['target_price'])))
 def agg(s):
  xs=[x for x in active if x[0]==s];return {'count':len(xs),'shares':sum(x[1] for x in xs),'oldest':max((x[2] for x in xs),default=np.nan),'youngest':min((x[2] for x in xs),default=np.nan)}
 a=agg(side);b=agg(opp)
 return {'pendingSameCount':a['count'],'pendingOppCount':b['count'],'pendingSameShares':a['shares'],'pendingOppShares':b['shares'],'pendingBoth':float(a['count']>0 and b['count']>0),'pendingAny':float(bool(active)),'pendingCountNet':a['count']-b['count'],'pendingShareNet':a['shares']-b['shares'],'pendingSameOldestMs':a['oldest'],'pendingOppOldestMs':b['oldest'],'pendingSameYoungestMs':a['youngest'],'pendingOppYoungestMs':b['youngest']}

def eval_model(df,features):
 nums=[c for c in features if c not in ['alignment','simple3Direction','toxicity1s']];cats=[c for c in features if c in ['alignment','simple3Direction','toxicity1s']]
 mids=list(dict.fromkeys(df.marketId.astype(int).tolist()));cuts=[int(len(mids)*x) for x in (.55,.70,.85,1.)];folds=[]
 for i in range(3):
  tr=df[df.marketId.isin(mids[:cuts[i]])];te=df[df.marketId.isin(mids[cuts[i]:cuts[i+1]])]
  pre=ColumnTransformer([('num',Pipeline([('imp',SimpleImputer(strategy='median'))]),nums),('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),('oh',OneHotEncoder(handle_unknown='ignore'))]),cats)])
  m=Pipeline([('pre',pre),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.06,max_iter=180,l2_regularization=2,random_state=7))]);m.fit(tr[features],tr[LABEL]);pred=m.predict(te[features]);pro=m.predict_proba(te[features]);base=tr[LABEL].value_counts().idxmax()
  folds.append({'balancedAccuracy':balanced_accuracy_score(te[LABEL],pred),'accuracy':accuracy_score(te[LABEL],pred),'baselineAccuracy':float((te[LABEL]==base).mean()),'logLoss':log_loss(te[LABEL],pro,labels=m.classes_),'trainRows':len(tr),'testRows':len(te)})
 return folds

def main():
 df=pd.read_csv(SRC)
 needed=['marketId','fillMs','side',LABEL]
 for c in needed:
  if c not in df.columns:raise SystemExit(f'missing {c}')
 parents=load_parents(df.marketId.unique())
 odf=pd.DataFrame([pending_features(r,parents) for _,r in df.iterrows()]);aug=pd.concat([df.reset_index(drop=True),odf],axis=1);aug.to_csv(AUG,index=False)
 base=[c for c in ['secondsLeft','postAbsNet','postPairedCoverage','markout1sTicks','alignment','simple3Direction','toxicity1s'] if c in aug.columns]
 oracle=base+list(odf.columns)
 report={'reportVersion':'TARGET_POSTFILL_PENDING_ORACLE_VALUE_V0','researchOnly':True,'teacherOnlyOracle':True,'runtimeDeployable':False,'antiLeakGuard':'Only inferred placements with placement_last_ms <= fill+1s checkpoint and last_target_ms > checkpoint are exposed; future fill is ownership/survival confirmation only.','coverage':{'rows':len(aug),'markets':int(aug.marketId.nunique()),'pendingAnyRate':float(aug.pendingAny.mean()),'pendingBothRate':float(aug.pendingBoth.mean())},'baseFeatures':base,'oracleFeatures':list(odf.columns),'baseFolds':eval_model(aug,base),'oracleFolds':eval_model(aug,oracle)}
 report['meanBalancedAccuracyBase']=float(np.mean([x['balancedAccuracy'] for x in report['baseFolds']]));report['meanBalancedAccuracyOracle']=float(np.mean([x['balancedAccuracy'] for x in report['oracleFolds']]));report['lift']=report['meanBalancedAccuracyOracle']-report['meanBalancedAccuracyBase']
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
