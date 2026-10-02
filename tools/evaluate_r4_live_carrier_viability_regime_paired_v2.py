from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score,recall_score
BASE=['secondsLeft','orderAgeS','remainingRatio','partialFillRatio','price','sideIsUp','sameSideLiveCount','oppSideLiveCount','quoteOffsetTicks','spreadTicks','predictSourceAgeMs','predictReceiptAgeMs','cancelPending','sameSideFillShares5s','sameSideFillShares15s','timeSinceSameSideFillS','lastMakerAgeMs','makerFills5s','makerShares5s','combinedAbsNet','combinedCoverage','worstCaseFloor','bestCasePnl','makerAbsNet']
REG=['venuePriorAcceptedCount','venuePriorAnyFillRate','venuePriorTerminalCount','venuePriorTerminalFillRate','venueAckCount30s','venueAckCount60s','venueFirstFillCount30s','venueFirstFillCount60s','venueFillQty30s','venueFillQty60s','venueTimeSinceLastFirstFillS','venueMedianObservedFillDelayS','venueActiveAcceptedBacklog','venueStale30Count','venueStale60Count','venueStale30Fraction','venueStale60Fraction','venueRejectCount30s','venueRejectCount60s']
def model(seed):return Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('et',ExtraTreesClassifier(n_estimators=240,max_depth=5,min_samples_leaf=5,class_weight='balanced',random_state=seed,n_jobs=4))])
def met(y,p):
 yh=(p>=.5).astype(int);return {'balancedAccuracy':float(balanced_accuracy_score(y,yh)),'auc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'positiveRecall':float(recall_score(y,yh,pos_label=1,zero_division=0)),'negativeRecall':float(recall_score(y,yh,pos_label=0,zero_division=0))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();df=pd.DataFrame(json.load(open(a.input,encoding='utf-8'))['rows']);o=[]
 for mid in sorted(df.marketId.unique()):
  tr=df[df.marketId!=mid];te=df[df.marketId==mid];y=tr.labelFill15s.to_numpy(int)
  if len(np.unique(y))<2:continue
  mb=model(1520);mr=model(1521);mb.fit(tr[BASE],y);mr.fit(tr[BASE+REG],y);pb=mb.predict_proba(te[BASE])[:,1];pr=mr.predict_proba(te[BASE+REG])[:,1]
  for idx,a0,b0 in zip(te.index,pb,pr):o.append((idx,float(a0),float(b0)))
 od=pd.DataFrame(o,columns=['idx','pBase','pReg']).set_index('idx').sort_index();sub=df.loc[od.index].copy();sub[['pBase','pReg']]=od[['pBase','pReg']];y=sub.labelFill15s.to_numpy(int);base=met(y,sub.pBase.to_numpy(float));reg=met(y,sub.pReg.to_numpy(float))
 rng=np.random.default_rng(152100);mids=np.array(sorted(sub.marketId.unique()));vals=[]
 for _ in range(5000):
  draw=rng.choice(mids,size=len(mids),replace=True);ix=np.concatenate([sub.index[sub.marketId==m].to_numpy() for m in draw]);yy=sub.loc[ix,'labelFill15s'].to_numpy(int);pb=sub.loc[ix,'pBase'].to_numpy(float);pr=sub.loc[ix,'pReg'].to_numpy(float)
  if len(np.unique(yy))<2:continue
  vals.append((roc_auc_score(yy,pr)-roc_auc_score(yy,pb),balanced_accuracy_score(yy,(pr>=.5).astype(int))-balanced_accuracy_score(yy,(pb>=.5).astype(int))))
 z=np.array(vals);boot={'n':len(z),'aucDeltaMedian':float(np.median(z[:,0])),'aucDeltaP025':float(np.quantile(z[:,0],.025)),'aucDeltaP975':float(np.quantile(z[:,0],.975)),'pAucDeltaPositive':float(np.mean(z[:,0]>0)),'baDeltaMedian':float(np.median(z[:,1])),'baDeltaP025':float(np.quantile(z[:,1],.025)),'baDeltaP975':float(np.quantile(z[:,1],.975)),'pBaDeltaPositive':float(np.mean(z[:,1]>0))}
 per=[]
 for mid in sorted(sub.marketId.unique()):
  q=sub[sub.marketId==mid];yy=q.labelFill15s.to_numpy(int);x={'marketId':int(mid),'n':len(q),'pos':int(yy.sum())}
  if len(np.unique(yy))>1:x.update({'baseAuc':float(roc_auc_score(yy,q.pBase)),'regimeAuc':float(roc_auc_score(yy,q.pReg)),'aucDelta':float(roc_auc_score(yy,q.pReg)-roc_auc_score(yy,q.pBase))})
  per.append(x)
 out={'version':'R4_LIVE_CARRIER_VIABILITY_REGIME_PAIRED_V2','researchOnly':True,'horizonSeconds':15,'model':'ExtraTrees fixed paired LOMO','baseFeatures':BASE,'regimeFeatures':REG,'baseMetrics':base,'regimeMetrics':reg,'pairedMarketBootstrap':boot,'perMarket':per};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'base':base,'regime':reg,'bootstrap':boot}))
if __name__=='__main__':main()
