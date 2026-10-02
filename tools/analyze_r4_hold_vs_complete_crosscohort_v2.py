import json, math
from pathlib import Path
from statistics import mean

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, balanced_accuracy_score

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_hold_vs_complete_value_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_hold_vs_complete_crosscohort_v2.json'

def val(f,k):
    x=f.get(k)
    return float(x) if x is not None and math.isfinite(float(x)) else 0.0

def feats(row,names):
    f=row['features']; z=[]
    for n in names:
        x=val(f,n)
        if n in {'riskPressure','riskSqrt'}: x=math.log1p(max(0,x))
        z.append(x)
    return z

rows=[r for r in json.loads(SRC.read_text(encoding='utf-8'))['rows'] if r['label'] in ('BENEFICIAL','HARMFUL')]
sets={
 'RISK_MIN':['rv10MeanBps','secondsLeft','riskPressure'],
 'ECON_MIN':['fairEdgePerShare','floor','upside','pairEdge'],
 'RESERVE_RISK':['floor','upside','rv10MeanBps','riskPressure'],
 'ECON_RISK':['fairEdgePerShare','floor','upside','pairEdge','rv10MeanBps','riskPressure'],
 'ALPHA_RISK':['directionalAlphaValue','floor','upside','rv10MeanBps','riskPressure'],
}
cohorts=sorted({r['cohort'] for r in rows})
result={'version':'R4_HOLD_VS_COMPLETE_CROSSCOHORT_V2','researchOnly':True,'strictPastFeatures':True,'postEpisodeLabelOnly':True,'source':str(SRC.relative_to(ROOT)).replace('\\','/'),'n':len(rows),'cohorts':cohorts,'featureSets':{}}
for name,names in sets.items():
    folds=[]; all_y=[]; all_p=[]; all_pred=[]
    for testc in cohorts:
        tr=[r for r in rows if r['cohort']!=testc]; te=[r for r in rows if r['cohort']==testc]
        ytr=np.array([1 if r['label']=='BENEFICIAL' else 0 for r in tr]); yte=np.array([1 if r['label']=='BENEFICIAL' else 0 for r in te])
        if len(set(ytr))<2 or len(set(yte))<2: continue
        Xtr=np.array([feats(r,names) for r in tr]); Xte=np.array([feats(r,names) for r in te])
        m=Pipeline([('s',StandardScaler()),('lr',LogisticRegression(C=0.5,class_weight='balanced',max_iter=2000,random_state=7))])
        m.fit(Xtr,ytr); p=m.predict_proba(Xte)[:,1]; pred=(p>=0.5).astype(int)
        auc=float(roc_auc_score(yte,p)); bal=float(balanced_accuracy_score(yte,pred))
        folds.append({'heldOut':testc,'n':len(te),'auc':auc,'balancedAccuracy':bal,'beneficial':int(yte.sum()),'harmful':int(len(yte)-yte.sum())})
        all_y.extend(yte.tolist()); all_p.extend(p.tolist()); all_pred.extend(pred.tolist())
    result['featureSets'][name]={'features':names,'folds':folds,'pooledAuc':float(roc_auc_score(all_y,all_p)),'pooledBalancedAccuracy':float(balanced_accuracy_score(all_y,all_pred)),'meanFoldAuc':mean(x['auc'] for x in folds),'meanFoldBalancedAccuracy':mean(x['balancedAccuracy'] for x in folds)}
# deterministic ranking, no threshold tuning
result['ranking']=sorted(({ 'name':k,'meanFoldAuc':v['meanFoldAuc'],'pooledAuc':v['pooledAuc'],'meanFoldBalancedAccuracy':v['meanFoldBalancedAccuracy']} for k,v in result['featureSets'].items()), key=lambda x:(x['meanFoldAuc'],x['meanFoldBalancedAccuracy']), reverse=True)
OUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)),'ranking':result['ranking']},ensure_ascii=False))
