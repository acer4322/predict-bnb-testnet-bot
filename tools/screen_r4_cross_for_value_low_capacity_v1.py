from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, mean_absolute_error, mean_squared_error
from scipy.stats import spearmanr
ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_first_per_market_older24_v2.json'
REC=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_paired_dataset_v2.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_low_capacity_screen_v1.json'
FEATS=['risk','transition','floor','surplus','floor_per_surplus','floor_severity','surplus_over_floor_scale']

def rowify(r):
 c=r['baselineCandidate']; floor=float(c['floor']); sur=float(c['surplus']); return {'marketId':int(r['marketId']),'atMs':int(r['atMs']),'deltaFloor':float(r['deltaFloor']),'actionable':int(bool(r['economicActionable'])),'valueClass':r['valueClass'],'risk':float(c['risk']),'transition':float(c['transition']),'floor':floor,'surplus':sur,'floor_per_surplus':floor/max(sur,1e-9),'floor_severity':-floor,'surplus_over_floor_scale':sur/(abs(floor)+5.0)}

def main():
 old=json.loads(OLD.read_text())['rows']; recent_all=json.loads(REC.read_text())['rows']
 # Recent cohort: earliest baseline candidate per market only, no outcome-based selection.
 recent=[]
 for mid in sorted(set(int(r['marketId']) for r in recent_all)):
  rs=sorted([r for r in recent_all if int(r['marketId'])==mid],key=lambda z:int(z['atMs'])); recent.append(rs[0])
 tr=pd.DataFrame([rowify(r) for r in old]); va=pd.DataFrame([rowify(r) for r in recent])
 Xtr=tr[FEATS].values; Xv=va[FEATS].values
 clf=make_pipeline(StandardScaler(),LogisticRegression(C=.25,class_weight='balanced',max_iter=2000,random_state=1)).fit(Xtr,tr.actionable)
 pa=clf.predict_proba(Xv)[:,1]
 reg=make_pipeline(StandardScaler(),Ridge(alpha=10.0)).fit(Xtr,tr.deltaFloor)
 pr=reg.predict(Xv)
 def cls_metrics(y,p):
  return {'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.c_[1-p,p],labels=[0,1]))}
 res={'version':'R4_CROSS_FOR_VALUE_LOW_CAPACITY_SCREEN_V1','definition':'Chronological screening only. Train=19 older independent markets, validation=7 newer independent markets (earliest baseline-reachable candidate per market). Latest 12 exact-overlay markets remain untouched. Low-capacity models only; no threshold sweep.','features':FEATS,'train':{'markets':len(tr),'actionable':int(tr.actionable.sum()),'valuePos':int((tr.valueClass=='VALUE_POS').sum()),'valueNeg':int((tr.valueClass=='VALUE_NEG').sum()),'noOp':int((tr.valueClass=='NO_OP').sum())},'validation':{'markets':len(va),'actionable':int(va.actionable.sum()),'valuePos':int((va.valueClass=='VALUE_POS').sum()),'valueNeg':int((va.valueClass=='VALUE_NEG').sum()),'noOp':int((va.valueClass=='NO_OP').sum()),'actionability':cls_metrics(va.actionable.values,pa),'advantageRegression':{'mae':float(mean_absolute_error(va.deltaFloor,pr)),'rmse':float(mean_squared_error(va.deltaFloor,pr)**.5),'spearman':float(spearmanr(va.deltaFloor,pr).statistic) if len(set(va.deltaFloor))>1 else None,'trueMean':float(va.deltaFloor.mean()),'predMean':float(pr.mean())}},'validationRows':[],'researchOnly':True,'actionAuthority':False}
 for i,r in va.iterrows(): res['validationRows'].append({'marketId':int(r.marketId),'trueClass':r.valueClass,'trueDeltaFloor':float(r.deltaFloor),'pActionable':float(pa[list(va.index).index(i)]),'predDeltaFloor':float(pr[list(va.index).index(i)])})
 OUT.write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps(res,indent=2))
if __name__=='__main__':main()
