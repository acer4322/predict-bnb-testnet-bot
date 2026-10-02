from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,accuracy_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FILES={'train':'r2_residual_multicheckpoint_train15_v0.json','validation':'r2_residual_multicheckpoint_validation15_v0.json','forward':'r2_residual_multicheckpoint_forward20_v0.json'}
OUT=D/'r2_intent_continuation_head_v0_report.json'

def augment(r):
    mid=int(r['marketId']);t=int(r['episodeAtMs']);f=dict(r.get('features') or {})
    rec='UP' if float(f.get('recoverySideIsUp') or 0.0)>0.5 else 'DOWN'; sur='DOWN' if rec=='UP' else 'UP'
    paper=load_reference_paper(mid); orders=sorted(paper['orders'],key=lambda o:int(o['placed_at_ms']))
    past=[o for o in orders if int(o['placed_at_ms'])<=t]; future=[o for o in orders if int(o['placed_at_ms'])>t]
    sides=[str(o['side']).upper() for o in past]
    streak=0
    for s in reversed(sides):
        if s==sur: streak+=1
        else: break
    last3=sides[-3:];last5=sides[-5:]
    f.update({'surplusPastStreak':float(streak),'surplusShareLast3':sum(s==sur for s in last3)/max(1,len(last3)),'surplusShareLast5':sum(s==sur for s in last5)/max(1,len(last5)),
              'intentCountPast':float(len(past)),'msSinceLastR2Intent':float(t-int(past[-1]['placed_at_ms'])) if past else np.nan,
              'currentTargetTowardSurplus':float((float(f.get('targetNet') or 0.0)>0 and sur=='UP') or (float(f.get('targetNet') or 0.0)<0 and sur=='DOWN')),
              'targetAbsNet':abs(float(f.get('targetNet') or 0.0))})
    y=np.nan if not future else float(str(future[0]['side']).upper()==sur)
    return f,y,mid,t,sur

def load(name):
    d=json.load(open(D/FILES[name],encoding='utf-8')); arr=[]
    for r in d['rowsData']:
        f,y,mid,t,sur=augment(r)
        if np.isnan(y): continue
        arr.append({**f,'labelNextIntentContinuesSurplus':y,'marketId':mid,'episodeAtMs':t})
    return pd.DataFrame(arr)

def metrics(y,p):
    pred=(p>=.5).astype(int);y=np.asarray(y,dtype=int)
    return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def main():
    parts={k:load(k) for k in FILES}
    exclude={'labelNextIntentContinuesSurplus','marketId','episodeAtMs'}
    feats=[c for c in parts['train'].columns if c not in exclude and pd.api.types.is_numeric_dtype(parts['train'][c])]
    model=Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=160,l2_regularization=3.0,min_samples_leaf=12,random_state=22))])
    model.fit(parts['train'][feats],parts['train']['labelNextIntentContinuesSurplus'].astype(int))
    mets={}
    for k,df in parts.items():
        p=model.predict_proba(df[feats])[:,1];mets[k]=metrics(df['labelNextIntentContinuesSurplus'],p)
    rep={'version':'R2_INTENT_CONTINUATION_HEAD_V0','researchOnly':True,'purpose':'Predict whether the next Frozen-R2 Maker intent continues the currently surplus side, as a base-policy anchoring signal for residual gating.','features':feats,'metrics':mets,
         'guardrails':['Future R2 intent side is training label only, never runtime input.','No winner/PnL/Target data.','Train Random1-15 only; Random16-30 validation; Random31-50 forward.','Natural 0.5 classification threshold; no sweep.','Head cannot override R2; it may only reduce residual intervention authority.']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
