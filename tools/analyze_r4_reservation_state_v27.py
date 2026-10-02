import json, math
from pathlib import Path
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, balanced_accuracy_score

SRC=Path('data/research/r4_v0/p0_provenance_v1/r4_exact_first_late_v25_summary.json')
OUT=Path('data/research/r4_v0/p0_provenance_v1/r4_reservation_state_v27_crosscohort.json')
d=json.loads(SRC.read_text(encoding='utf-8'))
rows=[r for r in d['rows'] if r.get('label') in ('BENEFICIAL','HARMFUL')]
feature_sets={
 'RISK_ALPHA_MIN':['directionAligned','alphaSupportMean','rv10sMeanBps','floor','upside'],
 'PRICE_FLOW':['predictAligned','spotQIAligned','futuresQIAligned','spotTaker1sAligned','futuresTaker1sAligned','rv10sMeanBps'],
 'RESERVATION_STATE':['directionAligned','predictAligned','alphaSupportMean','spotQIAligned','futuresQIAligned','spotTaker1sAligned','futuresTaker1sAligned','rv10sMeanBps','floor','upside','pairEdge','secondsPastNeed','quoteOffsetTicks']
}
cohorts=sorted(set(r['cohort'] for r in rows))
res={}
for name,fs in feature_sets.items():
    folds=[]; pooled_y=[]; pooled_p=[]; pooled_hat=[]
    for hold in cohorts:
        tr=[r for r in rows if r['cohort']!=hold]; te=[r for r in rows if r['cohort']==hold]
        Xtr=[[r['features'].get(f) for f in fs] for r in tr]; ytr=[1 if r['label']=='BENEFICIAL' else 0 for r in tr]
        Xte=[[r['features'].get(f) for f in fs] for r in te]; yte=[1 if r['label']=='BENEFICIAL' else 0 for r in te]
        pipe=Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('lr',LogisticRegression(C=0.25,solver='liblinear',max_iter=2000,class_weight='balanced',random_state=7))])
        pipe.fit(Xtr,ytr); p=pipe.predict_proba(Xte)[:,1]; hat=(p>=0.5).astype(int)
        auc=float(roc_auc_score(yte,p)) if len(set(yte))==2 else None
        ba=float(balanced_accuracy_score(yte,hat))
        folds.append({'heldOut':hold,'n':len(te),'beneficial':sum(yte),'harmful':len(yte)-sum(yte),'auc':auc,'balancedAccuracy':ba})
        pooled_y += yte; pooled_p += [float(x) for x in p]; pooled_hat += [int(x) for x in hat]
    res[name]={'features':fs,'folds':folds,'pooledAuc':float(roc_auc_score(pooled_y,pooled_p)),'pooledBalancedAccuracy':float(balanced_accuracy_score(pooled_y,pooled_hat)),'meanFoldAuc':float(np.mean([x['auc'] for x in folds if x['auc'] is not None])),'meanFoldBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in folds]))}
out={'version':'R4_RESERVATION_STATE_V27_CROSSCOHORT','researchOnly':True,'strictPastFeatures':True,'postEpisodeLabelOnly':True,'source':str(SRC),'nNonNeutral':len(rows),'cohorts':cohorts,'model':'leave-one-cohort-out logistic, C=0.25 fixed, balanced classes, train-only median imputation + standardization','featureSets':res,'ranking':sorted([{'name':k,'pooledAuc':v['pooledAuc'],'meanFoldAuc':v['meanFoldAuc'],'pooledBalancedAccuracy':v['pooledBalancedAccuracy'],'meanFoldBalancedAccuracy':v['meanFoldBalancedAccuracy']} for k,v in res.items()], key=lambda z:(z['meanFoldBalancedAccuracy'],z['pooledAuc']), reverse=True)}
OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'ok':True,'out':str(OUT),'n':len(rows),'ranking':out['ranking']},ensure_ascii=False))
