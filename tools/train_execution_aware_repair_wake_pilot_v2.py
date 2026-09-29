from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
import tools.train_execution_aware_repair_wake_pilot_v1 as base
OUT=ROOT/'data/research/execution_aware_repair_wake_v0'

def main():
    d=base.dataset().sort_values('marketId').reset_index(drop=True)
    # At creation time this is the fixed first40-known pilot cohort. Freeze exactly these rows in artifact metadata.
    train=d.copy(); w=1.0+np.minimum(np.abs(train.deltaUsdt.to_numpy(float)),50.0)/10.0
    model=ExplainableBoostingClassifier(feature_names=base.FEATURES,max_bins=8,max_interaction_bins=4,interactions=0,outer_bags=3,learning_rate=.025,max_rounds=450,early_stopping_rounds=45,min_samples_leaf=4,n_jobs=-2,random_state=20260821)
    model.fit(train[base.FEATURES].apply(pd.to_numeric,errors='coerce'),train.label.astype(int),sample_weight=w)
    OUT.mkdir(parents=True,exist_ok=True); art=OUT/'execution_aware_repair_wake_ebm_pilot_v2.joblib'
    joblib.dump({'version':'EXECUTION_AWARE_REPAIR_WAKE_EBM_PILOT_V2','model':model,'features':base.FEATURES,'trainingMarkets':train.marketId.astype(int).tolist(),'researchOnly':True,'sampleWeight':'1 + min(abs(counterfactualDeltaUsdt),50)/10','decisionThreshold':0.5},art)
    p=model.predict_proba(train[base.FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]
    imp=list(model.term_importances()); names=list(model.term_names_); order=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:12]
    report={'version':'EXECUTION_AWARE_REPAIR_WAKE_EBM_PILOT_V2','researchOnly':True,'dataset':{'rows':len(train),'markets':int(train.marketId.nunique()),'beneficial':int(train.label.sum()),'harmfulOrNeutral':int((1-train.label).sum())},'trainingMetrics':base.metric(train.label,p),'trainingEconomics':base.economics(train,p),'topTerms':[{'term':str(names[i]),'importance':float(imp[i])} for i in order],'artifact':str(art),'nextTestBoundary':'Do not modify model or threshold before evaluating the next untouched cohort (indices 40-49).'}
    (OUT/'execution_aware_repair_wake_ebm_pilot_v2_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
