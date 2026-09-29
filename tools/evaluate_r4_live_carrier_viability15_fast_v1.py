from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score,recall_score
FEATS=['secondsLeft','orderAgeS','remainingRatio','partialFillRatio','price','sideIsUp','sameSideLiveCount','oppSideLiveCount','quoteOffsetTicks','spreadTicks','predictSourceAgeMs','predictReceiptAgeMs','cancelPending','sameSideFillShares5s','sameSideFillShares15s','timeSinceSameSideFillS','lastMakerAgeMs','makerFills5s','makerShares5s','combinedAbsNet','combinedCoverage','worstCaseFloor','bestCasePnl','makerAbsNet']
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args(); df=pd.DataFrame(json.load(open(a.input,encoding='utf-8'))['rows']); oof=[]
 for mid in sorted(df.marketId.unique()):
  tr=df[df.marketId!=mid];te=df[df.marketId==mid];y=tr.labelFill15s.to_numpy(int)
  if len(np.unique(y))<2:continue
  m=Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('et',ExtraTreesClassifier(n_estimators=200,max_depth=5,min_samples_leaf=5,class_weight='balanced',random_state=1515,n_jobs=4))]);m.fit(tr[FEATS],y);p=m.predict_proba(te[FEATS])[:,1]
  for idx,pp in zip(te.index,p):oof.append((idx,float(pp)))
 od=pd.DataFrame(oof,columns=['idx','p']).set_index('idx').sort_index(); sub=df.loc[od.index].copy();sub['p']=od.p;y=sub.labelFill15s.to_numpy(int);p=sub.p.to_numpy(float);yh=(p>=.5).astype(int)
 metrics={'n':len(sub),'markets':int(sub.marketId.nunique()),'pos':int(y.sum()),'balancedAccuracy':float(balanced_accuracy_score(y,yh)),'auc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'positiveRecall':float(recall_score(y,yh,pos_label=1,zero_division=0)),'negativeRecall':float(recall_score(y,yh,pos_label=0,zero_division=0))}
 # fixed OOF market bootstrap; no refit or threshold tuning
 rng=np.random.default_rng(151500); mids=np.array(sorted(sub.marketId.unique())); vals=[]
 for _ in range(5000):
  draw=rng.choice(mids,size=len(mids),replace=True); ix=np.concatenate([sub.index[sub.marketId==m].to_numpy() for m in draw]); yy=sub.loc[ix,'labelFill15s'].to_numpy(int);pp=sub.loc[ix,'p'].to_numpy(float)
  if len(np.unique(yy))<2:continue
  vals.append((roc_auc_score(yy,pp),balanced_accuracy_score(yy,(pp>=.5).astype(int))))
 z=np.array(vals);boot={'n':len(z),'aucMedian':float(np.median(z[:,0])),'aucP025':float(np.quantile(z[:,0],.025)),'aucP975':float(np.quantile(z[:,0],.975)),'baMedian':float(np.median(z[:,1])),'baP025':float(np.quantile(z[:,1],.025)),'baP975':float(np.quantile(z[:,1],.975))}
 per=[]
 for mid in sorted(sub.marketId.unique()):
  q=sub[sub.marketId==mid];yy=q.labelFill15s.to_numpy(int);pp=q.p.to_numpy(float);per.append({'marketId':int(mid),'n':len(q),'pos':int(yy.sum()),'auc':None if len(np.unique(yy))<2 else float(roc_auc_score(yy,pp))})
 Path(a.output).write_text(json.dumps({'version':'R4_LIVE_CARRIER_VIABILITY15_FAST_V1','researchOnly':True,'features':FEATS,'metrics':metrics,'bootstrap':boot,'perMarket':per},indent=2),encoding='utf-8');print(json.dumps({'metrics':metrics,'bootstrap':boot}))
if __name__=='__main__':main()
