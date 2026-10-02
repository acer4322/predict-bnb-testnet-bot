import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path('.')
FILES={
 'TRAIN_FRESH24':'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv',
 'TEST_UNSEEN24':'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv',
 'TEST_REPLICATION3':'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_replication3_v1_rows.csv',
}
BASE=['seconds_left','predict_edge','pre_abs_payoff_gap','pre_risk_deficit','floor','absNet','p_build','p_prepare_role_routed']
AUG=BASE+['p_build_x_p_prepare_role_routed']
TARGETS=['floorImproved5s','absNetReduced5s','jointQuality5s']

def load(p):
 d=pd.read_csv(p)
 d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].copy()
 d['jointQuality5s']=((d['floorImproved5s'].astype(int)==1)&(d['absNetReduced5s'].astype(int)==1)).astype(int)
 d['p_build_x_p_prepare_role_routed']=d['p_build']*d['p_prepare_role_routed']
 cols=list(dict.fromkeys(BASE+AUG+TARGETS+['marketId']))
 return d[cols].replace([np.inf,-np.inf],np.nan).dropna().reset_index(drop=True)

def metric(y,p):
 return {'n':int(len(y)),'positive':int(y.sum()),'negative':int(len(y)-y.sum()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

data={k:load(v) for k,v in FILES.items()}
out={
 'version':'R4_PREPARE_BUILD_INTERACTION_LOCAL_QUALITY_V1',
 'testId':'R4_PREPARE_BUILD_INTERACTION_LOCAL_QUALITY_V1_20260828_1434',
 'researchOnly':True,
 'actionAuthority':False,
 'semanticNovelty':'Incremental nonlinear BUILDxPREPARE belief interaction after conditioning on additive BUILD/PREPARE plus portfolio/payoff geometry; prior regime maps were descriptive and quote attribution tested a different information increment.',
 'layerAssignment':{'geometry':'LOGIC_CONTEXT','p_build':'BUILD_BELIEF','p_prepare_role_routed':'PREPARE_BELIEF','interaction':'PARALLEL_BELIEF_INTERACTION_CONTEXT_CANDIDATE','output':'FORMATION_LOCAL_QUALITY_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'},
 'cohort':{k:{'rows':int(len(v)),'markets':int(v.marketId.nunique())} for k,v in data.items()},
 'features':{'baseline':BASE,'candidate':AUG},
 'targets':{},
 'fixedKeepRule':'all 6 eligible; mean dAUC>=0.005; worst dAUC>=-0.01; >=5/6 dAUC>=0; mean dAP>0; mean log-loss improvement>0',
 'guards':['2026-08-16 SEALED by source cohort','no Echtgeld fit/ingestion','future 5s outcomes scoring-only','no action coupling','no threshold/model/hyperparameter sweep']
}
all_d=[]
for target in TARGETS:
 tr=data['TRAIN_FRESH24']
 ytr=tr[target].astype(int).values
 if len(np.unique(ytr))<2: raise RuntimeError('train target single class '+target)
 base=make_pipeline(StandardScaler(),LogisticRegression(C=1,max_iter=2000,random_state=0))
 aug=make_pipeline(StandardScaler(),LogisticRegression(C=1,max_iter=2000,random_state=0))
 base.fit(tr[BASE],ytr); aug.fit(tr[AUG],ytr)
 tout={'trainSupport':{'rows':int(len(tr)),'positive':int(ytr.sum()),'negative':int(len(ytr)-ytr.sum())},'holdouts':{}}
 for hk in ['TEST_UNSEEN24','TEST_REPLICATION3']:
  te=data[hk]; y=te[target].astype(int).values
  support=(int(y.sum())>=50 and int(len(y)-y.sum())>=50)
  ent={'supportEligible':bool(support),'rows':int(len(y)),'positive':int(y.sum()),'negative':int(len(y)-y.sum())}
  if support:
   pb=base.predict_proba(te[BASE])[:,1]; pa=aug.predict_proba(te[AUG])[:,1]
   mb=metric(y,pb); ma=metric(y,pa)
   delta={'deltaAuc':ma['auc']-mb['auc'],'deltaAp':ma['ap']-mb['ap'],'logLossImprovement':mb['logLoss']-ma['logLoss']}
   ent.update({'baseline':mb,'candidate':ma,'delta':delta}); all_d.append(delta)
  tout['holdouts'][hk]=ent
 out['targets'][target]=tout
eligible=len(all_d)
if eligible==6:
 mean_auc=float(np.mean([x['deltaAuc'] for x in all_d])); mean_ap=float(np.mean([x['deltaAp'] for x in all_d])); mean_ll=float(np.mean([x['logLossImprovement'] for x in all_d])); worst=float(np.min([x['deltaAuc'] for x in all_d])); nonneg=int(sum(x['deltaAuc']>=0 for x in all_d))
 keep=(mean_auc>=0.005 and worst>=-0.01 and nonneg>=5 and mean_ap>0 and mean_ll>0)
 decision='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
 out['primaryResult']={'eligibleComparisons':6,'meanDeltaAuc':mean_auc,'meanDeltaAp':mean_ap,'meanLogLossImprovement':mean_ll,'worstDeltaAuc':worst,'nonnegativeAucComparisons':nonneg,'decision':decision}
else:
 decision='TESTED_INCONCLUSIVE'
 out['primaryResult']={'eligibleComparisons':eligible,'decision':decision,'reason':'support gate incomplete'}
out['decision']=decision
Path('data/research/r4_v0/hourly/r4_prepare_build_interaction_local_quality_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out['cohort'],indent=2)); print(json.dumps(out['primaryResult'],indent=2))
for t,v in out['targets'].items():
 print('\n',t)
 for h,e in v['holdouts'].items():
  print(h,e.get('delta'), 'support',e['positive'],e['negative'])
