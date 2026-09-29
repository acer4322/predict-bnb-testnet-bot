from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.tree import DecisionTreeClassifier, export_text

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_repair_carrier_delay_teacher_v1.csv'
OUT=P/'r4_fast_carrier_need_geometry_v1.json'

FEATURES=[
 'seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross',
 'weak_active_owners','dominant_active_owners','weak_oldest_age_s','current_mode_age_s',
 'events_5s','events_15s','transitions_15s','weak_unresolved_shares','weak_progress_ratio',
 'weak_fill_shares_5s','same_side_active_objectives','opposite_side_active_objectives',
 'same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s',
 'recent_objective_opens_15s','recent_parallel_opens_15s','parent_objective_age_s',
 'parent_residual','parent_reserved','parent_confirmed','parent_residual_ratio','requested_px'
]

def safe_auc(y,x):
    m=np.isfinite(x)
    if m.sum()<4 or len(np.unique(y[m]))<2:return None
    a=float(roc_auc_score(y[m],x[m]))
    return {'auc':a,'direction':'HIGHER_FAST' if a>=0.5 else 'LOWER_FAST','strength':max(a,1-a),'n':int(m.sum())}

def main():
    d=pd.read_csv(SRC)
    d=d[d.labelNeedWithin15s.notna()].copy()
    d['y']=d.labelNeedWithin15s.astype(int)
    uni={}
    for f in FEATURES:
        if f not in d:continue
        x=pd.to_numeric(d[f],errors='coerce').to_numpy(float);y=d.y.to_numpy(int)
        z=safe_auc(y,x)
        if z:
            pos=d.loc[d.y==1,f].dropna();neg=d.loc[d.y==0,f].dropna()
            z.update({'fastMedian':float(pos.median()) if len(pos) else None,'slowMedian':float(neg.median()) if len(neg) else None})
            uni[f]=z
    ranked=sorted(uni.items(),key=lambda kv:kv[1]['strength'],reverse=True)
    # descriptive shallow tree only; no promotion/tuning
    use=[f for f,_ in ranked[:8]]
    X=d[use].apply(pd.to_numeric,errors='coerce').fillna(0.0)
    y=d.y.astype(int)
    tree=DecisionTreeClassifier(max_depth=2,min_samples_leaf=4,random_state=20260830,class_weight='balanced')
    tree.fit(X,y)
    role={}
    for r,g in d.groupby('knownRole'):
        role[str(r)]={'n':int(len(g)),'fastN':int(g.y.sum()),'fastRate':float(g.y.mean()),'medianDelayS':float(g.futureCarrierNeedDelayS.median())}
    out={'version':'R4_FAST_CARRIER_NEED_GEOMETRY_V1','researchOnly':True,'diagnosticOnly':True,'rows':int(len(d)),'markets':int(d.marketId.nunique()),'fastN':int(d.y.sum()),'slowN':int((1-d.y).sum()),'byRole':role,'univariateRanked':[{'feature':k,**v} for k,v in ranked],'shallowTreeFeatures':use,'shallowTreeText':export_text(tree,feature_names=use),'warning':'Future carrier need is label-only. This artifact is descriptive on a small consumed Target teacher and cannot authorize runtime actions or thresholds.'}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
