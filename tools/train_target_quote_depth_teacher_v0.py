from __future__ import annotations
import json, numpy as np, pandas as pd
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_mature_mm_knob_projection_v0_placements.csv'
OUT=ROOT/'data/research/target_quote_depth_teacher_v0_report.json'
SETS={
 'INVENTORY':['secondsLeft','absNet','gross','imbalanceRatio','pairedCoverage','worstCaseFloor'],
 'MARKET':['secondsLeft','alignment','simple3Strength'],
 'POSTFILL':['secondsLeft','lastSameSideFillAgeMs','lastAnyFillAgeMs','lastSameSideMarkout1sTicks','sameSideFillStreak'],
 'FULL':['secondsLeft','absNet','gross','imbalanceRatio','pairedCoverage','worstCaseFloor','lastSameSideFillAgeMs','lastAnyFillAgeMs','lastSameSideMarkout1sTicks','sameSideFillStreak']}

def enc(df,cols):
 x=pd.DataFrame(index=df.index)
 for c in cols:
  if c not in df.columns: continue
  if pd.api.types.is_numeric_dtype(df[c]): x[c]=pd.to_numeric(df[c],errors='coerce')
  else:
   for v in sorted(df[c].dropna().astype(str).unique()): x[f'{c}={v}']=(df[c].astype(str)==v).astype(float)
 return x

def main():
 d=pd.read_csv(SRC).sort_values(['marketId','placementMs']).reset_index(drop=True)
 d=d[pd.to_numeric(d.quoteOffsetTicks,errors='coerce').notna()].copy();d['y']=pd.to_numeric(d.quoteOffsetTicks,errors='coerce').clip(-10,20)
 mids=list(dict.fromkeys(d.marketId.astype(int).tolist()));cuts=[int(len(mids)*x) for x in (.55,.70,.85,1.)]
 out={}
 for name,cols in SETS.items():
  X=enc(d,cols);folds=[]
  for i in range(3):
   tr=d.marketId.isin(mids[:cuts[i]]);te=d.marketId.isin(mids[cuts[i]:cuts[i+1]])
   model=Pipeline([('imp',SimpleImputer(strategy='median')),('m',HistGradientBoostingRegressor(max_depth=3,learning_rate=.06,max_iter=180,l2_regularization=2,random_state=7))]);model.fit(X[tr],d.loc[tr,'y']);p=model.predict(X[te]);base=np.repeat(float(d.loc[tr,'y'].median()),te.sum())
   folds.append({'mae':mean_absolute_error(d.loc[te,'y'],p),'medianBaselineMae':mean_absolute_error(d.loc[te,'y'],base),'liftMae':mean_absolute_error(d.loc[te,'y'],base)-mean_absolute_error(d.loc[te,'y'],p),'testRows':int(te.sum())})
  out[name]={'features':list(X.columns),'folds':folds,'meanLiftMae':float(np.mean([f['liftMae'] for f in folds]))}
 r={'reportVersion':'TARGET_QUOTE_DEPTH_TEACHER_V0','researchOnly':True,'guardrails':['Target placement is teacher output only','Strict-past public/inventory features only','No winner/PnL','No threshold sweep','Target ownership placement remains inferred, not private ground truth'],'coverage':{'rows':len(d),'markets':int(d.marketId.nunique())},'target':'quoteOffsetTicks clipped to [-10,20] for robust descriptive regression','sets':out}
 OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(r,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
