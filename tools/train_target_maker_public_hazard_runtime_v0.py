from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research'; SRC=D/'target_maker_direct_hazard_v1.csv'; OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'; ART=OUT/'target_maker_public_hazard_runtime_v0.joblib'; REP=OUT/'target_maker_public_hazard_runtime_v0_report.json'
FEATURES=['seconds_left','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge','spot_queue_imbalance','spot_taker_imbalance_1s','spot_return_1s_bps','spot_return_3s_bps','futures_queue_imbalance','futures_taker_imbalance_1s','futures_return_1s_bps','futures_return_3s_bps','perp_spot_basis_bps','spot_minus_strike_bps','chainlink_minus_strike_bps','spot_minus_chainlink_bps','direction_score','abs_direction_score']
LABEL='label_next_inferred_placement_any_5s'
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
 return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])),'balancedAccuracyAt0p5':float(balanced_accuracy_score(y,pred)),'positiveAt0p5':float(pred.mean())}
def main():
 d=pd.read_csv(SRC,low_memory=False).sort_values(['decision_sampled_at_ms','market_id']).copy(); mids=d.groupby('market_id').decision_sampled_at_ms.min().sort_values().index.astype(int).tolist(); a=int(len(mids)*.70); b=int(len(mids)*.85); sets={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 tr=d[d.market_id.isin(sets['train'])]; model=Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=180,l2_regularization=3.0,min_samples_leaf=40,random_state=20260822))]); model.fit(tr[FEATURES],tr[LABEL].astype(int)); metrics={}
 for k,s in sets.items(): x=d[d.market_id.isin(s)]; metrics[k]=met(x[LABEL].astype(int),model.predict_proba(x[FEATURES])[:,1])
 joblib.dump({'version':'TARGET_MAKER_PUBLIC_HAZARD_RUNTIME_V0','model':model,'features':FEATURES,'trainingMarkets':sorted(sets['train']),'label':LABEL,'runtimeTargetDataAllowed':False,'semantics':'Distilled public-only P(Target Maker placement within next 5s | strict-past public state); Target is teacher only.'},ART)
 rep={'version':'TARGET_MAKER_PUBLIC_HAZARD_RUNTIME_V0','researchOnly':True,'rows':len(d),'markets':len(mids),'splitMarkets':{k:len(v) for k,v in sets.items()},'features':FEATURES,'metrics':metrics,'guardrails':['Target is training teacher only; no Target runtime input','Strict-past public state only','Chronological market split','Natural 0.5 boundary only; no threshold sweep','Participation timing only; does not choose R2 side/quantity']}; REP.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
