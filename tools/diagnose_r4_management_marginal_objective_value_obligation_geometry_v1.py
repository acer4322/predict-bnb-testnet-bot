from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_stage3_expanded48_dataset_v2.csv'
PRE=P/'r4_management_marginal_objective_value_obligation_geometry_v1_preregistered.json'
OUT=P/'r4_management_marginal_objective_value_obligation_geometry_v1_diagnostic.json'
EPS=1e-9


def safe_div(a,b):
    return a/np.where(np.abs(b)>EPS,b,np.nan)

def enrich(d:pd.DataFrame)->pd.DataFrame:
    x=d.copy()
    q=pd.to_numeric(x['candidateQty'],errors='coerce').abs().clip(lower=EPS)
    gap=pd.to_numeric(x['candidateGap'],errors='coerce').abs().clip(lower=EPS)
    parent_res=pd.to_numeric(x['parent_residual'],errors='coerce').fillna(0).clip(lower=0)
    parent_rsv=pd.to_numeric(x['parent_reserved'],errors='coerce').fillna(0).clip(lower=0)
    parent_conf=pd.to_numeric(x['parent_confirmed'],errors='coerce').fillna(0).clip(lower=0)
    same_res=pd.to_numeric(x['same_side_total_residual'],errors='coerce').fillna(0).clip(lower=0)
    opp_res=pd.to_numeric(x['opposite_side_total_residual'],errors='coerce').fillna(0).clip(lower=0)
    cand_rsv=pd.to_numeric(x['candidateReservedQty'],errors='coerce').fillna(0).clip(lower=0)
    x['candidate_to_gap']=q/gap
    x['parent_residual_units']=parent_res/q
    x['parent_reserved_units']=parent_rsv/q
    x['parent_confirmed_units']=parent_conf/q
    x['same_side_residual_units']=same_res/q
    x['opposite_side_residual_units']=opp_res/q
    x['candidate_reserved_units']=cand_rsv/q
    x['same_side_unowned_residual_units']=np.maximum(0,same_res-parent_res)/q
    x['existing_credit_capacity_ratio']=np.minimum(1.0,same_res/q)
    x['parent_credit_capacity_ratio']=np.minimum(1.0,parent_res/q)
    x['residual_excess_units']=(same_res-q)/q
    side=x['candidateSide'].astype(str)
    pu=pd.to_numeric(x['predictUpMidPublic'],errors='coerce')
    pdn=pd.to_numeric(x['predictDownMidPublic'],errors='coerce')
    mid=np.where(side.eq('UP'),pu,pdn)
    x['candidate_px_minus_side_mid']=pd.to_numeric(x['candidatePx'],errors='coerce')-mid
    spot=pd.to_numeric(x['spotMinusStrikeBpsPublic'],errors='coerce')
    x['spot_toward_candidate_bps']=np.where(side.eq('UP'),spot,-spot)
    return x

def model(kind:str,seed:int):
    if kind=='LOGIT':
        return Pipeline([
            ('imp',SimpleImputer(strategy='median')),
            ('sc',StandardScaler()),
            ('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed)),
        ])
    return Pipeline([
        ('imp',SimpleImputer(strategy='median')),
        ('clf',ExtraTreesClassifier(n_estimators=500,max_depth=3,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=1,random_state=seed)),
    ])

def score_lomo(d:pd.DataFrame,fs:list[str],kind:str):
    probs=np.full(len(d),np.nan); pred=np.empty(len(d),dtype=object)
    mids=list(dict.fromkeys(int(x) for x in d.marketId.tolist()))
    for i,mid in enumerate(mids):
        te=np.where(d.marketId.to_numpy()==mid)[0]
        tr=np.where(d.marketId.to_numpy()!=mid)[0]
        m=model(kind,33000+i)
        m.fit(d.iloc[tr][fs],d.iloc[tr]['label'])
        cls=list(m.classes_)
        pp=m.predict_proba(d.iloc[te][fs])[:,cls.index('SUBSTITUTE')]
        probs[te]=pp
        pred[te]=np.where(pp>=.5,'SUBSTITUTE','ADDITIVE')
    y=d.label.to_numpy(); yy=(y=='SUBSTITUTE').astype(int)
    return {
        'n':int(len(d)),
        'markets':int(d.marketId.nunique()),
        'substituteSupport':int(yy.sum()),
        'additiveSupport':int((1-yy).sum()),
        'balancedAccuracy':float(balanced_accuracy_score(y,pred)),
        'auc':float(roc_auc_score(yy,probs)),
        'ap':float(average_precision_score(yy,probs)),
        'substituteRecall':float(np.mean(pred[yy==1]=='SUBSTITUTE')),
        'additiveRecall':float(np.mean(pred[yy==0]=='ADDITIVE')),
    }

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8'))
    d=enrich(pd.read_csv(SRC))
    d=d[d.knownRole.isin(['PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'])].copy().reset_index(drop=True)
    d['label']=np.where(d.knownRole.eq('PREPOSITION_REPAIR_SUBSTITUTE'),'SUBSTITUTE','ADDITIVE')
    # teacher-only branch preference summary; not used in X
    d['teacher_credit_minus_add_floor']=pd.to_numeric(d.target_credit_floor_gain,errors='coerce')-pd.to_numeric(d.target_add_floor_gain,errors='coerce')
    d['teacher_credit_minus_add_absnet']=pd.to_numeric(d.target_credit_absnet_gain,errors='coerce')-pd.to_numeric(d.target_add_absnet_gain,errors='coerce')
    teacher={}
    for role,g in d.groupby('knownRole'):
        teacher[role]={
            'n':int(len(g)),
            'meanCreditMinusAddFloor':float(g.teacher_credit_minus_add_floor.mean()),
            'medianCreditMinusAddFloor':float(g.teacher_credit_minus_add_floor.median()),
            'meanCreditMinusAddAbsNet':float(g.teacher_credit_minus_add_absnet.mean()),
            'medianCreditMinusAddAbsNet':float(g.teacher_credit_minus_add_absnet.median()),
        }
    results={}
    for kind in ('LOGIT','EXTRATREES'):
        results[kind]={}
        for name,fs in pre['featureSets'].items():
            results[kind][name]=score_lomo(d,list(fs),kind)
    out={
        'version':'R4_MANAGEMENT_MARGINAL_OBJECTIVE_VALUE_OBLIGATION_GEOMETRY_V1_DIAGNOSTIC',
        'researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
        'contractStatus':pre['status'],'rows':int(len(d)),'markets':int(d.marketId.nunique()),
        'roleCounts':d.knownRole.value_counts().to_dict(),
        'teacherBranchPreference':teacher,
        'results':results,
        'interpretationBoundary':'Future branch economics are teacher/interpretation only. Runtime feature sets are strict-past obligation/economic geometry from expanded48. Development cohort is already consumed; no promotion.',
        'guards':pre['guards'],
    }
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,indent=2))
if __name__=='__main__':main()
