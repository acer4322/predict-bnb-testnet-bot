from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
CP=json.load(open(P/'r4_r3_live_engine_management_checkpoints_v1.json',encoding='utf-8'))['rows']
CR=json.load(open(P/'r4_live_carrier_viability_echtgeld11_v1.json',encoding='utf-8'))['rows']

d=pd.DataFrame(CP)
c=pd.DataFrame(CR)
# Frozen eligibility from preregistration.
d=d[(d.secondsLeft<=180)&(d.secondsLeft>60)&(d.weakOwnerCount>0)&(d.weakPipelineCoversGap==True)].copy()
# Venue-feasible carriers only; Stage0 hard precedence.
c=c[(c.notional>=1.0)].copy()
# Align exact strict-past decision snapshot time and weak side.
c=c.merge(d[['marketId','decisionMs','weakSide']], left_on=['marketId','t','side'], right_on=['marketId','decisionMs','weakSide'], how='inner')

agg=c.groupby(['marketId','decisionMs']).agg(
 carrier_min_quote_offset_ticks=('quoteOffsetTicks','min'),
 carrier_mean_quote_offset_ticks=('quoteOffsetTicks','mean'),
 carrier_max_order_age_s=('orderAgeS','max'),
 carrier_mean_remaining_ratio=('remainingRatio','mean'),
 carrier_max_partial_fill_ratio=('partialFillRatio','max'),
 carrier_min_time_since_same_fill_s=('timeSinceSameSideFillS','min'),
 carrier_same_side_live_max=('sameSideLiveCount','max'),
 carrier_min_notional=('notional','min'),
 carrier_mean_predict_receipt_age_ms=('predictReceiptAgeMs','mean'),
 carrier_count=('orderId','nunique')
).reset_index()
d=d.merge(agg,on=['marketId','decisionMs'],how='left')
# carrier_count is required; missing aggregation means ownership checkpoint could not be matched to a feasible live carrier.
d=d[d.carrier_count.fillna(0)>0].copy()

baseline=['gap','weakReserved','unownedWeakDeficit','weakOwnerCount','weakNewestAgeMs','weakOldestAgeMs','smallUnowned2LiveLots']
carrier=['carrier_min_quote_offset_ticks','carrier_mean_quote_offset_ticks','carrier_max_order_age_s','carrier_mean_remaining_ratio','carrier_max_partial_fill_ratio','carrier_min_time_since_same_fill_s','carrier_same_side_live_max','carrier_min_notional','carrier_mean_predict_receipt_age_ms']
label='existingWeakAnyFill15s'
for col in baseline+carrier:
 d[col]=pd.to_numeric(d[col],errors='coerce')
d[label]=d[label].astype(int)

# Median imputation is fit on training fold only. Simple standardization likewise training-only.
def fit_predict(train,test,features):
 Xtr=train[features].copy(); Xte=test[features].copy()
 meds=Xtr.median(numeric_only=True)
 Xtr=Xtr.fillna(meds); Xte=Xte.fillna(meds)
 mu=Xtr.mean(); sd=Xtr.std(ddof=0).replace(0,1.0).fillna(1.0)
 Xtr=(Xtr-mu)/sd; Xte=(Xte-mu)/sd
 m=LogisticRegression(C=0.5,class_weight='balanced',max_iter=2000,solver='liblinear',random_state=7)
 m.fit(Xtr,train[label])
 return m.predict_proba(Xte)[:,1]

folds=[]; oof=[]
for mid in sorted(d.marketId.unique()):
 te=d[d.marketId==mid].copy(); tr=d[d.marketId!=mid].copy()
 if te[label].nunique()<2 or tr[label].nunique()<2:
  folds.append({'marketId':int(mid),'rows':int(len(te)),'positive':int(te[label].sum()),'evaluable':False})
  continue
 pb=fit_predict(tr,te,baseline); pa=fit_predict(tr,te,baseline+carrier)
 y=te[label].to_numpy()
 fb={'marketId':int(mid),'rows':int(len(te)),'positive':int(y.sum()),'evaluable':True,
     'baselineAuc':float(roc_auc_score(y,pb)),'augmentedAuc':float(roc_auc_score(y,pa)),
     'deltaAuc':float(roc_auc_score(y,pa)-roc_auc_score(y,pb)),
     'baselineAp':float(average_precision_score(y,pb)),'augmentedAp':float(average_precision_score(y,pa)),
     'deltaAp':float(average_precision_score(y,pa)-average_precision_score(y,pb)),
     'baselineBA':float(balanced_accuracy_score(y,pb>=0.5)),'augmentedBA':float(balanced_accuracy_score(y,pa>=0.5))}
 folds.append(fb)
 for (_,r),b,a in zip(te.iterrows(),pb,pa):
  oof.append({'marketId':int(r.marketId),'decisionMs':int(r.decisionMs),'secondsLeft':float(r.secondsLeft),'weakSide':str(r.weakSide),
              'actualFill15s':int(r[label]),'pBaseline':float(b),'pCarrierAug':float(a),'carrierCount':int(r.carrier_count),
              'gap':float(r.gap),'weakReserved':float(r.weakReserved),'unownedWeakDeficit':float(r.unownedWeakDeficit),
              'minQuoteOffsetTicks':float(r.carrier_min_quote_offset_ticks),'maxOrderAgeS':float(r.carrier_max_order_age_s),
              'meanRemainingRatio':float(r.carrier_mean_remaining_ratio),'minTimeSinceSameFillS':float(r.carrier_min_time_since_same_fill_s)})

ev=[f for f in folds if f.get('evaluable')]
# pooled OOF only over evaluable-market rows; probabilities are strictly OOF by market.
od=pd.DataFrame(oof)
pooled={}
if len(od) and od.actualFill15s.nunique()==2:
 y=od.actualFill15s.to_numpy(); pb=od.pBaseline.to_numpy(); pa=od.pCarrierAug.to_numpy()
 pooled={'rows':int(len(od)),'markets':int(od.marketId.nunique()),'positiveRate':float(y.mean()),
         'baselineAuc':float(roc_auc_score(y,pb)),'augmentedAuc':float(roc_auc_score(y,pa)),'deltaAuc':float(roc_auc_score(y,pa)-roc_auc_score(y,pb)),
         'baselineAp':float(average_precision_score(y,pb)),'augmentedAp':float(average_precision_score(y,pa)),'deltaAp':float(average_precision_score(y,pa)-average_precision_score(y,pb)),
         'baselineBA':float(balanced_accuracy_score(y,pb>=0.5)),'augmentedBA':float(balanced_accuracy_score(y,pa>=0.5)),
         'baselineLogLoss':float(log_loss(y,np.clip(pb,1e-6,1-1e-6))),'augmentedLogLoss':float(log_loss(y,np.clip(pa,1e-6,1-1e-6)))}
# Whole-market non-authoritative annotation: low predicted viability should rank false ownership confidence (pipeline claims coverage but no fill in 15s).
market_ann=[]
if len(od):
 for m,g in od.groupby('marketId'):
  market_ann.append({'marketId':int(m),'rows':int(len(g)),'actualFill15sRate':float(g.actualFill15s.mean()),
                     'meanCarrierViability':float(g.pCarrierAug.mean()),'medianCarrierViability':float(g.pCarrierAug.median()),
                     'meanBaselineViability':float(g.pBaseline.mean())})
neg_auc=None
if len(od) and od.actualFill15s.nunique()==2:
 neg_auc=float(roc_auc_score(1-od.actualFill15s,1-od.pCarrierAug))
mean_da=float(np.mean([f['deltaAuc'] for f in ev])) if ev else None
mean_dp=float(np.mean([f['deltaAp'] for f in ev])) if ev else None
neg_folds=int(sum(f['deltaAuc']<0 for f in ev))
keep=bool(ev and mean_da>=0.03 and mean_dp>=0.02 and neg_folds<=2)
rep={'version':'R4_MANAGEMENT_EXISTING_CARRIER_VIABILITY_V1','researchOnly':True,'actionAuthority':False,'strictPastRuntimeFeatures':True,
     'sourceBoundary':'historical Echtgeld11 + 8781 durable lifecycle; future fill labels scoring only','eligibilityRows':int(len(d)),'eligibilityMarkets':int(d.marketId.nunique()),
     'positiveRate15s':float(d[label].mean()) if len(d) else None,'folds':folds,'summary':{'evaluableFolds':len(ev),'meanDeltaAuc':mean_da,'meanDeltaAp':mean_dp,'negativeAucFolds':neg_folds,'keepGatePassed':keep},
     'pooledOOF':pooled,'falseOwnershipConfidenceRankingAuc':neg_auc,'wholeMarketAnnotations':market_ann,
     'decision':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED',
     'interpretation':'Tests whether actual live-carrier state adds transferable completion information beyond ownership/reservation state. No refresh/veto/action threshold is created.'}
(P/'r4_management_existing_carrier_viability_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
(P/'r4_management_existing_carrier_viability_v1_oof.json').write_text(json.dumps(oof,indent=2),encoding='utf-8')
print(json.dumps(rep,indent=2))
