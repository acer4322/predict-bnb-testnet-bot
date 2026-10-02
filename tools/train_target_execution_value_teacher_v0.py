from __future__ import annotations
import json, math
from pathlib import Path
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_mature_mm_knob_projection_v0_reactions.csv'
OUT=ROOT/'data/research/target_execution_value_teacher_v0_report.json'
VERSION='TARGET_EXECUTION_VALUE_TEACHER_V0'
LABEL='reactionClass'
# Deliberately coarse, interpretable mature-MM knobs only. No PnL/winner/private future state.
NUM=['secondsLeft','postAbsNet','postPairedCoverage','markout1sTicks','pairLockedEdge']
CAT=['inventoryRole','alignment','simple3Direction','toxicity1s']


def main():
    df=pd.read_csv(SRC)
    # Normalize source column names without inventing unavailable state.
    rename={
      'secondsLeftAt1s':'secondsLeft','postPairedCoverage':'postPairedCoverage',
      'postAbsNet':'postAbsNet','markout1sTicks':'markout1sTicks',
      'oppBestBidLockedEdge':'pairLockedEdge','role':'inventoryRole',
      'alignmentAt1s':'alignment','simple3DirectionAt1s':'simple3Direction',
      'toxicity1s':'toxicity1s'}
    df=df.rename(columns=rename)
    # Source projection uses slightly different names in some builds.
    aliases={'secondsLeft':'secondsLeft','postAbsNet':'postAbsNet','postPairedCoverage':'postPairedCoverage',
             'markout1sTicks':'markout1sTicks','oppBestBidLockedEdge':'pairLockedEdge','inventoryRole':'inventoryRole',
             'alignment':'alignment','simple3Direction':'simple3Direction','toxicity1s':'toxicity1s'}
    # Only use columns actually present; report omissions explicitly.
    nums=[c for c in NUM if c in df.columns]
    cats=[c for c in CAT if c in df.columns]
    required=[LABEL]
    if LABEL not in df.columns:
        raise SystemExit(f'missing {LABEL}; columns={list(df.columns)}')
    df=df[df[LABEL].isin(['CONTINUE_SAME_FIRST','SWITCH_OPPOSITE_FIRST','PAUSE_NO_PARENT_1_TO_5S'])].copy()
    df=df.sort_values(['marketId','fillMs'] if 'fillMs' in df.columns else ['marketId']).reset_index(drop=True)
    # Market-level chronological folds: train strict-past markets, test next block.
    mids=list(dict.fromkeys(df['marketId'].tolist()))
    cuts=[int(len(mids)*x) for x in (.55,.70,.85,1.0)]
    folds=[]
    pre=ColumnTransformer([
      ('num',Pipeline([('imp',SimpleImputer(strategy='median'))]),nums),
      ('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),('oh',OneHotEncoder(handle_unknown='ignore'))]),cats)
    ])
    for i in range(3):
        train_m=set(mids[:cuts[i]])
        test_m=set(mids[cuts[i]:cuts[i+1]])
        tr=df[df.marketId.isin(train_m)]; te=df[df.marketId.isin(test_m)]
        if len(tr)<100 or len(te)<20: continue
        model=Pipeline([('pre',pre),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.06,max_iter=180,l2_regularization=2.0,random_state=7))])
        Xtr=tr[nums+cats]; Xte=te[nums+cats]; ytr=tr[LABEL]; yte=te[LABEL]
        model.fit(Xtr,ytr); pred=model.predict(Xte); proba=model.predict_proba(Xte)
        base=ytr.value_counts().idxmax()
        folds.append({'fold':i+1,'trainMarkets':len(train_m),'testMarkets':len(test_m),'trainRows':len(tr),'testRows':len(te),
                      'accuracy':accuracy_score(yte,pred),'balancedAccuracy':balanced_accuracy_score(yte,pred),
                      'majorityBaselineAccuracy':float((yte==base).mean()),'logLoss':log_loss(yte,proba,labels=model.classes_),
                      'testClassCounts':yte.value_counts().to_dict()})
    # Univariate descriptive separation; no threshold optimization.
    desc={}
    for c in nums:
        desc[c]={}
        for lab,g in df.groupby(LABEL):
            s=pd.to_numeric(g[c],errors='coerce').dropna()
            desc[c][lab]={'n':len(s),'median':float(s.median()) if len(s) else None,'mean':float(s.mean()) if len(s) else None}
    for c in cats:
        tab=pd.crosstab(df[c].fillna('NA'),df[LABEL],normalize='columns')
        desc[c]=tab.round(4).to_dict()
    report={'reportVersion':VERSION,'researchOnly':True,'source':str(SRC.relative_to(ROOT)),
            'guardrails':['Target is teacher label only','No winner/PnL in inputs','No runtime Target/private state','Strict-past market chronological evaluation','No threshold tuning'],
            'coverage':{'rows':len(df),'markets':int(df.marketId.nunique())},'features':{'numeric':nums,'categorical':cats,'missingRequested':[c for c in NUM+CAT if c not in nums+cats]},
            'classCounts':df[LABEL].value_counts().to_dict(),'folds':folds,'descriptiveSeparation':desc}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
