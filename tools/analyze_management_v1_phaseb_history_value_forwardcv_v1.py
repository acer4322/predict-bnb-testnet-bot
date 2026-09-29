from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path('data/research/r4_v0/p0_provenance_v1')
TRUE=ROOT/'MANAGEMENT_TRAINING_V1_HISTORY_MARKET_ACTION_VALUE_CORPUS78_V2_FIXED_FAVORED_20260907.json'
SHUF=ROOT/'MANAGEMENT_TRAINING_V1_HISTORY_MARKET_ACTION_VALUE_CORPUS78_V2_HISTORY_SHUFFLE_CONTROL_20260907.json'
OUT=ROOT/'MANAGEMENT_TRAINING_V1_PHASEB_HISTORY_VALUE_FORWARD_CV_V1_20260907.json'

BASE_FEATURES=[
 'actionHistory','actionHold','actionHasSide','actionSideUp','actionRepair','actionExpand','actionPrice','actionQty',
 'marketSideUp','upQty','downQty','cost','absNet','grossQty','currentUpPayoff','currentDownPayoff','currentFloor','currentBest','currentGap',
 'repairDebtUP','repairDebtDOWN','totalDebt','marketCandidatePrice','marketCandidateQty','historyCandidatePrice','historyCandidateQty','candidateCrossSum','candidatePriceDiffHistoryMinusMarket',
 'freeSlots','liveSlots','liveCoreCount','liveRepairCount','liveExpandCount','liveCancelPendingCount','liveUpCount','liveDownCount','qLadderPresent','qLadderSideUp','qLadderRepair','pendingActive']
HISTORY_FEATURES=['historySideUp','historyAgeMs','historyRunLength','recentCleanCount','recentCleanUpRatio','recentRiskUpQty','recentRiskDownQty','recentRiskNet']
TARGETS=['dFavored5000ms','dOpposite5000ms','dFloor5000ms','dFavoredTerminal','dOppositeTerminal','dFloorTerminal','dFillsTerminal']
RIDGE=10.0

def load(path):return pd.DataFrame(json.load(open(path,encoding='utf-8'))['rows'])
def prep(train,test,cols):
 a=train[cols].astype(float).to_numpy();b=test[cols].astype(float).to_numpy();
 med=np.nanmedian(a,axis=0);med=np.where(np.isfinite(med),med,0.0);a=np.where(np.isfinite(a),a,med);b=np.where(np.isfinite(b),b,med)
 mu=a.mean(0);sd=a.std(0);sd=np.where(sd<1e-8,1.0,sd);a=(a-mu)/sd;b=(b-mu)/sd
 return np.c_[np.ones(len(a)),a],np.c_[np.ones(len(b)),b]
def fit_predict(train,test,cols,target):
 x,z=prep(train,test,cols);y=train[target].astype(float).to_numpy();reg=np.eye(x.shape[1])*RIDGE;reg[0,0]=0
 beta=np.linalg.solve(x.T@x+reg,x.T@y);return z@beta
def folds(df):
 ms=df[['marketId','t']].drop_duplicates().sort_values(['t','marketId']).reset_index(drop=True);n=len(ms)
 # Four expanding, strictly forward blocks: first 30 train, then 12/12/12/remainder.
 cuts=[30,42,54,66,n];out=[]
 for i in range(4):
  tr=set(ms.iloc[:cuts[i]].marketId.astype(int));te=set(ms.iloc[cuts[i]:cuts[i+1]].marketId.astype(int));
  if not te:continue
  out.append((tr,te))
 return out

def metric(y,p):
 y=np.asarray(y,float);p=np.asarray(p,float);return {'mae':float(np.mean(np.abs(y-p))),'rmse':float(np.sqrt(np.mean((y-p)**2))),'bias':float(np.mean(p-y))}
def main():
 true=load(TRUE);shuf=load(SHUF)
 keys=['marketId','t','action'];assert len(true)==156 and len(shuf)==156
 sh=shuf[keys+HISTORY_FEATURES].copy();sh.columns=keys+[f'shuf_{c}' for c in HISTORY_FEATURES]
 d=true.merge(sh,on=keys,how='inner',validate='one_to_one');assert len(d)==156
 flds=folds(d);report={'version':'MANAGEMENT_TRAINING_V1_PHASEB_HISTORY_VALUE_FORWARD_CV_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'ridge':RIDGE,'features':{'base':BASE_FEATURES,'history':HISTORY_FEATURES},'targets':TARGETS,'folds':[],'aggregate':{}}
 allpred={t:{'BASE':[],'TRUE_HISTORY':[],'SHUFFLED_HISTORY':[],'y':[]} for t in TARGETS}
 for fi,(trm,tem) in enumerate(flds,1):
  tr=d[d.marketId.isin(trm)].copy();te=d[d.marketId.isin(tem)].copy();fr={'fold':fi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':len(tr),'testRows':len(te),'results':{}}
  shuf_cols=[f'shuf_{c}' for c in HISTORY_FEATURES]
  for t in TARGETS:
   y=te[t].astype(float).to_numpy();p0=fit_predict(tr,te,BASE_FEATURES,t);p1=fit_predict(tr,te,BASE_FEATURES+HISTORY_FEATURES,t);p2=fit_predict(tr,te,BASE_FEATURES+shuf_cols,t)
   m0,m1,m2=metric(y,p0),metric(y,p1),metric(y,p2)
   fr['results'][t]={'BASE':m0,'TRUE_HISTORY':m1,'SHUFFLED_HISTORY':m2,'trueHistoryMaeImprovement':m0['mae']-m1['mae'],'shuffledHistoryMaeImprovement':m0['mae']-m2['mae'],'trueMinusShuffleMaeAdvantage':m2['mae']-m1['mae']}
   allpred[t]['y'].extend(y.tolist());allpred[t]['BASE'].extend(p0.tolist());allpred[t]['TRUE_HISTORY'].extend(p1.tolist());allpred[t]['SHUFFLED_HISTORY'].extend(p2.tolist())
  report['folds'].append(fr)
 for t in TARGETS:
  y=np.array(allpred[t]['y']);p0=np.array(allpred[t]['BASE']);p1=np.array(allpred[t]['TRUE_HISTORY']);p2=np.array(allpred[t]['SHUFFLED_HISTORY']);m0,m1,m2=metric(y,p0),metric(y,p1),metric(y,p2)
  vals=[f['results'][t] for f in report['folds']]
  report['aggregate'][t]={'BASE':m0,'TRUE_HISTORY':m1,'SHUFFLED_HISTORY':m2,'trueHistoryMaeImprovement':m0['mae']-m1['mae'],'shuffledHistoryMaeImprovement':m0['mae']-m2['mae'],'trueMinusShuffleMaeAdvantage':m2['mae']-m1['mae'],'trueHistoryPositiveFolds':sum(v['trueHistoryMaeImprovement']>0 for v in vals),'trueBeatsShuffleFolds':sum(v['trueMinusShuffleMaeAdvantage']>0 for v in vals),'foldCount':len(vals)}
 report['decision']='HISTORY_VALUE_SIGNAL_NOT_ESTABLISHED' if not all(report['aggregate'][t]['trueBeatsShuffleFolds']>=3 and report['aggregate'][t]['trueHistoryMaeImprovement']>0 for t in ['dFavored5000ms','dFavoredTerminal']) else 'HISTORY_VALUE_SIGNAL_CANDIDATE'
 report['guards']=['Strict forward market folds; no random row split.','History shuffle changes only history feature vector; action/outcome/portfolio/economic rows remain fixed.','Linear ridge fixed a priori; no threshold or hyperparameter sweep.','Targets are same-prefix counterfactual deltas vs frozen market-direction baseline; winner/settlement not features.','Research-only: predictive signal does not grant policy authority.']
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
