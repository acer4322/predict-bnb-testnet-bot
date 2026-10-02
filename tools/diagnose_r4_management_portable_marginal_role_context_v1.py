from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PRE=P/'r4_management_portable_marginal_role_context_v1_preregistered.json';WM=P/'r4_management_wholemarket_marginal_role_context_v1.json';OUT=P/'r4_management_portable_marginal_role_context_v1_diagnostic.json'
CH=[P/'r4_management_marginal_objective_value_obligation_trajectory_chunk_0_13_v1.json',P/'r4_management_marginal_objective_value_obligation_trajectory_chunk_13_13_v1.json']

def model(seed):
 return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])
def main():
 pre=json.loads(PRE.read_text());fs=list(pre['features']);rows=[]
 for f in CH:rows+=json.loads(f.read_text())['rows']
 d=pd.DataFrame([r for r in rows if 'error' not in r]).replace([np.inf,-np.inf],np.nan);d['label']=np.where(d.knownRole.eq('PREPOSITION_REPAIR_SUBSTITUTE'),'SUBSTITUTE','ADDITIVE')
 p=np.full(len(d),np.nan);pred=np.empty(len(d),dtype=object);mids=list(dict.fromkeys(int(x) for x in d.marketId.tolist()))
 for i,mid in enumerate(mids):
  te=np.where(d.marketId.to_numpy()==mid)[0];tr=np.where(d.marketId.to_numpy()!=mid)[0];m=model(88000+i);m.fit(d.iloc[tr][fs],d.iloc[tr].label);cls=list(m.classes_);pp=m.predict_proba(d.iloc[te][fs])[:,cls.index('SUBSTITUTE')];p[te]=pp;pred[te]=np.where(pp>=.5,'SUBSTITUTE','ADDITIVE')
 yy=(d.label.to_numpy()=='SUBSTITUTE').astype(int);dev={'n':len(d),'markets':int(d.marketId.nunique()),'balancedAccuracy':float(balanced_accuracy_score(d.label,pred)),'auc':float(roc_auc_score(yy,p)),'ap':float(average_precision_score(yy,p)),'substituteRecall':float(np.mean(pred[yy==1]=='SUBSTITUTE')),'additiveRecall':float(np.mean(pred[yy==0]=='ADDITIVE'))}
 # Fit all consumed dev rows only, then annotate consumed whole-market rows.
 fm=model(88999);fm.fit(d[fs],d.label);cls=list(fm.classes_)
 wm=json.loads(WM.read_text());w=pd.DataFrame(wm['rows']).replace([np.inf,-np.inf],np.nan)
 rename={'same_residual_units':'same_side_residual_units','opp_residual_units':'opposite_side_residual_units','same_credit_capacity_ratio':'existing_credit_capacity_ratio','same_active_delta5':'same_side_active_delta5','same_active_delta15':'same_side_active_delta15','opp_active_delta5':'opposite_side_active_delta5','opp_active_delta15':'opposite_side_active_delta15','same_residual_delta5_units':'same_side_residual_delta5_units','same_residual_delta15_units':'same_side_residual_delta15_units','opp_residual_delta5_units':'opposite_side_residual_delta5_units','opp_residual_delta15_units':'opposite_side_residual_delta15_units'};w=w.rename(columns=rename)
 pp=fm.predict_proba(w[fs])[:,cls.index('SUBSTITUTE')];w['pSubstitute']=pp;w['roleContext']=np.where(pp>=.5,'SUBSTITUTE_CONTEXT','ADDITIVE_CONTEXT')
 def grp(col):
  z={}
  for k,g in w.groupby(col):z[str(k)]={'n':int(len(g)),'meanPSubstitute':float(g.pSubstitute.mean()),'medianPSubstitute':float(g.pSubstitute.median()),'substituteContextRate':float(np.mean(g.pSubstitute>=.5)),'meanSameResidualUnits':float(g.same_side_residual_units.mean()),'meanPExistingWeak5s':float(pd.to_numeric(g.pExistingWeak5s,errors='coerce').mean())}
  return z
 out={'version':'R4_MANAGEMENT_PORTABLE_MARGINAL_ROLE_CONTEXT_V1_DIAGNOSTIC','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'contractStatus':pre['status'],'developmentLOMO':dev,'wholeMarketConsumedAnnotation':{'rows':int(len(w)),'markets':int(w.marketId.nunique()),'byErrorType':grp('errorType'),'byOriginalDecision':grp('originalDecision'),'byTeacherDecision':grp('teacherDecision'),'highSubstituteContextRows':w.sort_values('pSubstitute',ascending=False).head(12)[['marketId','t','originalDecision','teacherDecision','errorType','pSubstitute','same_side_residual_units','same_side_active_delta15','pExistingWeak5s']].to_dict('records')},'interpretationBoundary':'Portable pSubstitute is a shadow role context only. No action mutation and no promotion from either consumed cohort.','guards':pre['guards']}
 OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
