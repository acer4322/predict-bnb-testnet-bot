from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
import joblib

# Reuse the already validated strict-past teacher builder from the R3 safe-expand work.
import sys
sys.path.insert(0, str(Path('tools').resolve()))
import train_r3_safe_expand_v3 as base

OUT=Path('data/research/r3_v0'); OUT.mkdir(parents=True,exist_ok=True)

def main():
    # build_rows returns chronological Target checkpoints with strict-past features and future label fields.
    rows=base.build_rows()
    df=pd.DataFrame(rows)
    # restrict to PRE_SAFE; if builder exposes first-safe state use floor<0, which is the intended formation domain.
    df=df[np.isfinite(pd.to_numeric(df['floor'],errors='coerce'))].copy()
    df=df[df['floor'] < 0].copy()
    # Require future 5s fields used by prior teacher builder.
    needed=['future_surplus_delta','future_min_floor','future_floor','surplus_shares']
    missing=[c for c in needed if c not in df.columns]
    if missing:
        raise RuntimeError('missing teacher fields: '+','.join(missing))
    cur=np.maximum(df['surplus_shares'].abs().to_numpy(float),1e-9)
    delta=df['future_surplus_delta'].to_numpy(float)
    # permission: current surplus not materially contracted (>10% or 5 shares) in next 5s
    contract_thr=np.maximum(5.0,0.10*cur)
    df['y_permission']=(delta >= -contract_thr).astype(int)
    # weak-side build proxy: surplus contracts materially while floor improves; this is the protection-building action.
    df['y_weak_build']=((delta <= -contract_thr) & (df['future_floor'].to_numpy(float) > df['floor'].to_numpy(float))).astype(int)
    # crossing hazard: reaches nonnegative floor within horizon
    df['y_cross']=(df['future_floor'].to_numpy(float) >= 0).astype(int)

    drop={'y_permission','y_weak_build','y_cross','future_surplus_delta','future_min_floor','future_floor','label','winner','market_id','ts_ms'}
    feats=[c for c in base.FEATURES if c in df.columns and c not in drop]
    # chronological market split
    mids=list(dict.fromkeys(df['market_id'].tolist())) if 'market_id' in df.columns else []
    if not mids: raise RuntimeError('market_id missing')
    n=len(mids); a=int(n*.6); b=int(n*.8)
    sets=[('train',set(mids[:a])),('validation',set(mids[a:b])),('test',set(mids[b:]))]
    report={'version':'R3_BASE_FORMATION_EXPERTS_V0','rows':len(df),'markets':n,'features':feats,'experts':{}}
    for label,name in [('y_permission','formation_surplus_permission'),('y_weak_build','weak_side_build'),('y_cross','safe_crossing_hazard')]:
        tr=df[df.market_id.isin(sets[0][1])]; va=df[df.market_id.isin(sets[1][1])]; te=df[df.market_id.isin(sets[2][1])]
        Xtr=tr[feats].replace([np.inf,-np.inf],np.nan).fillna(0); ytr=tr[label]
        model=HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=15,learning_rate=.07,l2_regularization=2.0,random_state=42)
        model.fit(Xtr,ytr)
        rr={}
        for sn,ms in sets:
            d=df[df.market_id.isin(ms)]; X=d[feats].replace([np.inf,-np.inf],np.nan).fillna(0); y=d[label].to_numpy(); p=model.predict_proba(X)[:,1]
            auc=float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
            bal=float(balanced_accuracy_score(y,(p>=.5).astype(int)))
            rr[sn]={'rows':len(d),'positiveRate':float(np.mean(y)),'auc':auc,'balancedAccuracyAt05':bal}
        report['experts'][name]=rr
        joblib.dump({'model':model,'features':feats,'label':label},OUT/f'r3_{name}_hgb_v0.joblib')
    (OUT/'r3_base_formation_experts_v0_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
if __name__=='__main__': main()
