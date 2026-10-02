from __future__ import annotations
import json,sys
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,f1_score,log_loss,brier_score_loss
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'supervisor_options_v0';CSV=OUT/'supervisor_target_act_states_v2.csv';N=int(sys.argv[1]);REPORT=OUT/f'supervisor_target_act_v2_curve_n{N}.json';ART=OUT/'supervisor_target_act_large_v2_validated.joblib'

def weights(y):
 c=y.value_counts();n=len(y);mp={k:np.sqrt(n/max(1,int(v))) for k,v in c.items()};w=y.map(mp).astype(float).to_numpy();return w/w.mean()
def met(m,x,features):
 y=x.gate_act.astype(str);cl=list(map(str,m.classes_));ai=cl.index('ACT');p=m.predict_proba(x[features].apply(pd.to_numeric,errors='coerce'))[:,ai];yb=y.eq('ACT').astype(int);pred=(p>=.5).astype(int);return {'n':len(x),'positiveRate':float(yb.mean()),'predRate':float(pred.mean()),'auc':float(roc_auc_score(yb,p)),'ap':float(average_precision_score(yb,p)),'balancedAccuracy':float(balanced_accuracy_score(yb,pred)),'f1':float(f1_score(yb,pred,zero_division=0)),'logLoss':float(log_loss(yb,np.column_stack([1-p,p]),labels=[0,1])),'brier':float(brier_score_loss(yb,p))}
def main():
 d=pd.read_csv(CSV).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);meta=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=meta.market_id.astype(int).tolist();assert len(ids)==779
 train_pool=ids[:594];val_ids=set(ids[594:699]);test_ids=set(ids[699:779]);tr_ids=set(train_pool[:N]);tr=d[d.market_id.astype(int).isin(tr_ids)].copy();va=d[d.market_id.astype(int).isin(val_ids)].copy();te=d[d.market_id.astype(int).isin(test_ids)].copy();exclude={'market_id','market_end_ms','checkpoint_ms','gate_act','option_mode_v2','taker_next3s_raw'};features=[c for c in d.columns if c not in exclude]
 m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=70,max_leaf_nodes=15,min_samples_leaf=100,l2_regularization=1.0,early_stopping=True,validation_fraction=.1,n_iter_no_change=15,random_state=20260820);y=tr.gate_act.astype(str);m.fit(tr[features].apply(pd.to_numeric,errors='coerce'),y,sample_weight=weights(y));rep={'reportVersion':'SUPERVISOR_TARGET_ACT_V2_CURVE_VARIANT','researchOnly':True,'trainMarkets':N,'trainRows':len(tr),'trainCounts':tr.gate_act.value_counts().to_dict(),'validation':met(m,va,features),'futureTest':met(m,te,features),'split':{'trainPool':594,'validationMarkets':105,'futureTestMarkets':80},'features':len(features),'guards':['No winner/PnL','Raw Taker teacher label fixed','Ordinary only','Fixed validation/future test','No runtime changes']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');
 if N==594:joblib.dump({'version':'SUPERVISOR_TARGET_ACT_LARGE_V2_VALIDATED','researchOnly':True,'runtimePromotion':False,'model':m,'features':features,'trainingMarkets':sorted(tr_ids),'validationMarkets':sorted(val_ids),'futureTestMarkets':sorted(test_ids),'semantics':'Target-state ACT/HOLD teacher V2; exact raw Taker labels; validated on later ordinary markets; still not direct OUR runtime'},ART)
 print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
