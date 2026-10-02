from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, recall_score

DATA = Path('data/research/r4_v0/p0_provenance_v1/r4_p0b_stage3_expanded48_dataset_v2.csv')
OUT = Path('data/research/r4_v0/p0_provenance_v1/r4_p0b_stage3_additive_admission_v0_seed_robustness_v1.json')
FEATURES = [
    'seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross',
    'candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic',
    'candidateReservedQty','candidateReservedRootCount'
]
SEEDS = [7,19,43,101,509,2027,51000,88888]

def main():
    d = pd.read_csv(DATA).replace([np.inf,-np.inf],np.nan)
    d['candidatePxMinusSideMid'] = d.candidatePx - np.where(
        d.candidateSide.astype(str).eq('UP'), d.predictUpMidPublic, d.predictDownMidPublic)
    eps = 1e-9
    y = ((d.target_add_floor_gain >= -eps) & (d.target_add_absnet_gain >= -eps) &
         ((d.target_add_floor_gain > eps) | (d.target_add_absnet_gain > eps))).astype(int).to_numpy()
    mids = d.marketId.astype(int).to_numpy()
    uniq = list(dict.fromkeys(mids.tolist()))
    out = {
        'version':'R4_P0B_STAGE3_ADDITIVE_ADMISSION_V0_SEED_ROBUSTNESS_V1',
        'rows':int(len(d)), 'markets':int(d.marketId.nunique()),
        'featureSet':'LOCAL_ECON_EXEC_V0', 'threshold':0.5,
        'evaluation':'True leave-one-market-out; development-consumed Expanded48 only.',
        'results':{}
    }
    for kind in ['LOGIT','EXTRATREES']:
        vals=[]
        for seed in SEEDS:
            prob=np.zeros(len(d))
            for j,mid in enumerate(uniq):
                te=np.where(mids==mid)[0]; tr=np.where(mids!=mid)[0]
                if kind=='LOGIT':
                    m=Pipeline([
                        ('imp',SimpleImputer(strategy='median')),
                        ('sc',StandardScaler()),
                        ('clf',LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=seed+j))])
                else:
                    m=Pipeline([
                        ('imp',SimpleImputer(strategy='median')),
                        ('clf',ExtraTreesClassifier(n_estimators=500,max_depth=4,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed+j))])
                m.fit(d.loc[tr,FEATURES],y[tr])
                prob[te]=m.predict_proba(d.loc[te,FEATURES])[:,list(m.classes_).index(1)]
            q=(prob>=.5).astype(int)
            vals.append({
                'seed':seed,
                'balancedAccuracy':float(balanced_accuracy_score(y,q)),
                'auc':float(roc_auc_score(y,prob)),
                'positiveRecall':float(recall_score(y,q,pos_label=1)),
                'negativeRecall':float(recall_score(y,q,pos_label=0))})
        out['results'][kind]={
            'runs':vals,
            'balancedAccuracyMean':float(np.mean([v['balancedAccuracy'] for v in vals])),
            'balancedAccuracyMin':float(np.min([v['balancedAccuracy'] for v in vals])),
            'balancedAccuracyMax':float(np.max([v['balancedAccuracy'] for v in vals])),
            'aucMean':float(np.mean([v['auc'] for v in vals])),
            'aucMin':float(np.min([v['auc'] for v in vals])),
            'aucMax':float(np.max([v['auc'] for v in vals]))}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,indent=2))

if __name__=='__main__': main()
