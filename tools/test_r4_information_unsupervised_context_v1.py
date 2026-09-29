from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_information_unsupervised_context_v1.json'
VERSION='R4_INFORMATION_UNSUPERVISED_CONTEXT_V1'

LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
BASE=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
EXT=[
'spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','spot_micro_minus_spot_bps',
'futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','futures_micro_minus_futures_bps',
'perp_spot_basis_bps','spot_minus_chainlink_bps','chainlink_minus_strike_bps']


def hgb(seed):
 return HistGradientBoostingClassifier(learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=240,random_state=seed)
def metric(y,p):
 return {'n':int(len(y)),'repairRate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 df=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);df.market_id=df.market_id.astype(int);df['repair']=1-df.is_add.astype(int)
 mt=df.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms');ms=mt.market_id.astype(int).tolist();n=len(ms);a=int(n*.70);b=int(n*.85)
 split={'train':ms[:a],'validation':ms[a:b],'test':ms[b:]};parts={k:df[df.market_id.isin(set(v))].copy() for k,v in split.items()}
 # Unsupervised context representation: fit only on unique public snapshots in outer train, never on Target labels.
 unique_train=parts['train'].drop_duplicates(['market_id','sampled_at_ms'])
 pre=Pipeline([('imp',SimpleImputer(strategy='median')),('scale',StandardScaler()),('pca',PCA(n_components=4,random_state=20260827))])
 pre.fit(unique_train[EXT])
 pc_names=[f'external_context_pc{i+1}' for i in range(4)]
 for sk,p in parts.items():
  z=pre.transform(p[EXT])
  for i,c in enumerate(pc_names): p[c]=z[:,i]
 results={}
 sets={'LOGIC_ONLY':LOGIC,'BASE_PREDICT_STRIKE':BASE,'LOGIC_PLUS_CONTEXT':LOGIC+pc_names,'BASE_PLUS_CONTEXT':BASE+pc_names,'RAW_ALL':BASE+EXT}
 for j,(name,feats) in enumerate(sets.items()):
  m=hgb(20260827+j);m.fit(parts['train'][feats],parts['train'].repair);results[name]={}
  for sk in ('validation','test'):
   p=parts[sk];results[name][sk]=metric(p.repair.to_numpy(),m.predict_proba(p[feats])[:,1])
 # PCA loadings, top absolute features per factor.
 pca=pre.named_steps['pca'];loadings=[]
 for i,row in enumerate(pca.components_):
  ranked=sorted([{'feature':f,'loading':float(v),'absLoading':float(abs(v))} for f,v in zip(EXT,row)],key=lambda x:x['absLoading'],reverse=True)
  loadings.append({'factor':pc_names[i],'explainedVarianceRatio':float(pca.explained_variance_ratio_[i]),'topLoadings':ranked[:8]})
 artifact={'version':VERSION,'researchOnly':True,'runtimePromotionAllowed':False,'question':'Can raw external spot/futures/basis information be compressed without Target-action supervision into a small context representation that the Formation logic layer can use more robustly than raw feature concatenation?','coverage':{'rows':int(len(df)),'markets':int(df.market_id.nunique()),'uniqueTrainPublicSnapshots':int(len(unique_train))},'split':split,'externalRawFeatures':EXT,'representation':{'method':'train-only median imputation + standardization + fixed 4-component PCA; no Target action label used in representation fit','factors':loadings,'totalExplainedVariance':float(pca.explained_variance_ratio_.sum())},'results':results,'guards':['PCA component count fixed at 4 before result inspection; no component-count sweep.','Representation fit uses only outer-train public information and no Target action label.','Logic model remains research teacher only; no action authority.','No winner/settlement/random row split.']}
 OUT.write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'representation':artifact['representation'],'results':results},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
