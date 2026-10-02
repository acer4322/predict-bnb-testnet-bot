from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0'
CL=['HOLD','MAKER','TAKER']

def model(seed):return HistGradientBoostingClassifier(learning_rate=.06,max_iter=150,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1.5,random_state=seed)
def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);o={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{}}
 for c in CL:
  z=y==c;o['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return o

def fit_scheme(df,features,scheme,seed):
 if scheme=='BALANCED_SUBSAMPLE_EQUAL':
  n=int(df.option_mode_v2.value_counts().min());x=pd.concat([df[df.option_mode_v2.eq(c)].sample(n=n,random_state=seed+i) for i,c in enumerate(CL)]).sample(frac=1,random_state=seed+9);return model(seed+20).fit(x[features],x.option_mode_v2),{'rows':len(x)}
 counts=df.option_mode_v2.value_counts().to_dict();w=np.array([1.0/counts[v] for v in df.option_mode_v2]) if scheme=='ALL_ROWS_INVERSE_FREQ' else np.array([1.0/np.sqrt(counts[v]) for v in df.option_mode_v2]);w=w/np.mean(w);return model(seed+20).fit(df[features],df.option_mode_v2,sample_weight=w),{'rows':len(df),'weights':{c:float((1.0/counts[c] if scheme=='ALL_ROWS_INVERSE_FREQ' else 1.0/np.sqrt(counts[c]))/np.mean([1.0/counts[v] if scheme=='ALL_ROWS_INVERSE_FREQ' else 1.0/np.sqrt(counts[v]) for v in df.option_mode_v2])) for c in CL}}

def main():
 d=pd.read_csv(S/'supervisor_target_act_states_v2.csv');order=d.groupby('market_id').market_end_ms.min().sort_values().index.astype(int).tolist();first699=order[:-80];cut=int(len(first699)*.8);fitm=set(first699[:cut]);devm=set(first699[cut:]);testm=set(order[-80:]);features=[c for c in d.columns if c not in {'market_id','market_end_ms','checkpoint_ms','gate_act','option_mode_v2','taker_next3s_raw'}];fit=d[d.market_id.isin(fitm)].dropna(subset=features+['option_mode_v2']);dev=d[d.market_id.isin(devm)].dropna(subset=features+['option_mode_v2']);res={}
 schemes=['BALANCED_SUBSAMPLE_EQUAL','ALL_ROWS_INVERSE_FREQ','ALL_ROWS_INVERSE_SQRT_FREQ']
 for i,s in enumerate(schemes):
  m,meta=fit_scheme(fit,features,s,1500+i*100);sc=score(dev.option_mode_v2,m.predict(dev[features]));res[s]={'development':sc,'meta':meta,'eligible':all(sc['perClass'][c]['recall']>=.40 for c in CL)}
 eligible=[s for s in schemes if res[s]['eligible']];winner=max(eligible,key=lambda s:res[s]['development']['balancedAccuracy']) if eligible else max(schemes,key=lambda s:res[s]['development']['balancedAccuracy']);all699=d[d.market_id.isin(first699)].dropna(subset=features+['option_mode_v2']);m,meta=fit_scheme(all699,features,winner,1900);te=d[d.market_id.isin(testm)].dropna(subset=features+['option_mode_v2']);final=score(te.option_mode_v2,m.predict(te[features]));checks={'balancedAccuracy':final['balancedAccuracy']>=.58,'holdRecall':final['perClass']['HOLD']['recall']>=.60,'makerRecall':final['perClass']['MAKER']['recall']>=.45,'takerRecall':final['perClass']['TAKER']['recall']>=.45};rep={'version':'R4_MANAGEMENT_STUDENT_V0_JOINT_CALIBRATED','researchOnly':True,'developmentFitMarkets':len(fitm),'developmentValidationMarkets':len(devm),'finalFutureMarkets':len(testm),'schemes':res,'winnerFrozenBeforeFinal80':winner,'final80':final,'checks':checks,'gatePass':all(checks.values())};joblib.dump({'version':rep['version'],'model':m,'features':features,'scheme':winner,'researchOnly':True,'actionAuthority':False},P/'r4_management_student_v0_joint_calibrated.joblib');(P/'r4_management_student_v0_joint_calibrated_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
