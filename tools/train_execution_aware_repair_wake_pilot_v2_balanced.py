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
    raw=1.0+np.minimum(np.abs(d.deltaUsdt.to_numpy(float)),50.0)/10.0
    y=d.label.to_numpy(int); w=raw.copy()
    for cls in (0,1):
        mask=y==cls; s=float(w[mask].sum())
        if s>0: w[mask]*=(len(w)/2.0)/s
    model=ExplainableBoostingClassifier(feature_names=base.FEATURES,max_bins=8,max_interaction_bins=4,interactions=0,outer_bags=3,learning_rate=.03,max_rounds=450,early_stopping_rounds=45,min_samples_leaf=3,n_jobs=-2,random_state=20260821)
    model.fit(d[base.FEATURES].apply(pd.to_numeric,errors='coerce'),d.label.astype(int),sample_weight=w)
    p=model.predict_proba(d[base.FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]
    OUT.mkdir(parents=True,exist_ok=True); art=OUT/'execution_aware_repair_wake_ebm_pilot_v2_balanced.joblib'
    joblib.dump({'version':'EXECUTION_AWARE_REPAIR_WAKE_EBM_PILOT_V2_BALANCED','model':model,'features':base.FEATURES,'trainingMarkets':d.marketId.astype(int).tolist(),'researchOnly':True,'sampleWeight':'economic severity, then class-total balanced','decisionThreshold':0.5},art)
    imp=list(model.term_importances()); names=list(model.term_names_); order=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:12]
    rep={'version':'EXECUTION_AWARE_REPAIR_WAKE_EBM_PILOT_V2_BALANCED','researchOnly':True,'dataset':{'rows':len(d),'markets':int(d.marketId.nunique()),'beneficial':int(d.label.sum()),'harmfulOrNeutral':int((1-d.label).sum())},'trainingMetrics':base.metric(d.label,p),'trainingEconomics':base.economics(d,p),'predRange':[float(p.min()),float(p.max())],'topTerms':[{'term':str(names[i]),'importance':float(imp[i])} for i in order],'artifact':str(art),'nextTestBoundary':'Frozen model + threshold 0.5. Evaluate only on untouched indices 40-49 next.'}
    (OUT/'execution_aware_repair_wake_ebm_pilot_v2_balanced_report.json').write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False))
if __name__=='__main__':main()
