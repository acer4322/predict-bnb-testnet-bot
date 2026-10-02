from __future__ import annotations
import json, random
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, recall_score
from sklearn.preprocessing import label_binarize
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base

SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_ebm_multi_model_markets_v1.json'
CLASSES=base.CLASSES; FEATURES=base.FEATURES; SEED=26082731

def met(y,p):
 y=np.asarray(y); p=np.asarray(p,float); p=np.clip(p,1e-7,1-1e-7); p=p/p.sum(axis=1,keepdims=True)
 pred=np.asarray(CLASSES)[np.argmax(p,axis=1)]; Y=label_binarize(y,classes=CLASSES)
 auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro'))
 ap=float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)]))
 rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
 return {'n':int(len(y)),'macroAuc':auc,'macroAp':ap,'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}

def market_ll(df,p):
 out={}
 for mid,idx in df.groupby('market_id').groups.items():
  pos=df.index.get_indexer(list(idx)); y=df.loc[list(idx),'management_label_5s'].astype(str).to_numpy(); pp=p[pos]
  out[int(mid)]=float(log_loss(y,np.clip(pp,1e-7,1-1e-7),labels=CLASSES))
 return out

def ebm(seed):
 return ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)

def main():
 base.seed_all(SEED)
 d=pd.read_csv(SRC)
 d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=FEATURES).copy().sort_values(['market_id','t']).reset_index(drop=True)
 ms=base.market_order(d); bspec=base.blocks(ms); device='cuda' if torch.cuda.is_available() else 'cpu'
 blocks=[]; market_wins={'TRI_BLEND_vs_EBM':0,'TRI_BLEND_vs_LGBM':0,'TRI_BLEND_vs_TF':0,'marketsCompared':0}
 for bi,trm,tem in bspec:
  base.seed_all(SEED+bi)
  tr_all=d[d.market_id.isin(trm)].copy(); te_all=d[d.market_id.isin(tem)].copy()
  tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy().reset_index(drop=True)
  te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy().reset_index(drop=True)
  # EBM
  em=ebm(SEED+bi).fit(tr[FEATURES],tr.management_label_5s)
  pe0=em.predict_proba(te[FEATURES]); eo=list(em.classes_); pe=np.column_stack([pe0[:,eo.index(c)] for c in CLASSES])
  # LightGBM
  lm=LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.0,random_state=SEED+bi,verbosity=-1,n_jobs=-1)
  lm.fit(tr[FEATURES],tr.management_label_5s); pl0=lm.predict_proba(te[FEATURES]); lo=list(lm.classes_); pl=np.column_stack([pl0[:,lo.index(c)] for c in CLASSES])
  # Transformer causal sequence
  mu,sd=base.fit_scaler(tr_all); xtr,ytr,_,_=base.build_seq(tr_all,tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')],mu,sd); xte,yte,_,_=base.build_seq(te_all,te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')],mu,sd)
  base.seed_all(SEED+bi+29); pt,eps=base.train_torch(base.TinyTransformer(len(FEATURES),len(CLASSES)),xtr,ytr,xte,yte,device)
  # fixed collaboration candidates; no sweep
  avg3=(pe+pl+pt)/3
  tri=.25*pe+.45*pl+.30*pt  # EBM interpretable anchor + LGBM state anchor + Transformer history expert
  ebm_lgb=.40*pe+.60*pl
  candidates={'EBM':pe,'LIGHTGBM':pl,'TINY_TRANSFORMER':pt,'AVG3':avg3,'TRI_BLEND_25_45_30':tri,'EBM40_LGB60':ebm_lgb}
  b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'testMarketIds':[int(x) for x in tem],'trainRows':int(len(tr)),'testRows':int(len(te)),'transformerEpochs':eps,'models':{k:met(te.management_label_5s,p) for k,p in candidates.items()}}
  # market-wise log-loss wins
  ll={k:market_ll(te,p) for k,p in candidates.items()}; b['marketLogLossMean']={k:float(np.mean(list(v.values()))) for k,v in ll.items()}
  mw={'TRI_BLEND_vs_EBM':sum(ll['TRI_BLEND_25_45_30'][m]<ll['EBM'][m] for m in ll['EBM']), 'TRI_BLEND_vs_LGBM':sum(ll['TRI_BLEND_25_45_30'][m]<ll['LIGHTGBM'][m] for m in ll['EBM']), 'TRI_BLEND_vs_TF':sum(ll['TRI_BLEND_25_45_30'][m]<ll['TINY_TRANSFORMER'][m] for m in ll['EBM']), 'marketsCompared':len(ll['EBM'])}
  b['marketWins']=mw
  for k in market_wins: market_wins[k]+=mw[k]
  blocks.append(b); print(json.dumps({'block':bi,'models':b['models'],'marketWins':mw},ensure_ascii=False),flush=True)
 names=['EBM','LIGHTGBM','TINY_TRANSFORMER','AVG3','TRI_BLEND_25_45_30','EBM40_LGB60']
 summary={}
 for n in names:
  q=[b['models'][n] for b in blocks]
  summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'stdMacroAuc':float(np.std([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in CLASSES},'meanMarketLogLoss':float(np.mean([b['marketLogLossMean'][n] for b in blocks]))}
 art={'version':'R4_MANAGEMENT_EBM_MULTI_MODEL_MARKETS_V1','researchOnly':True,'actionAuthority':False,'task':'M1 management_label_5s current BUILD, multi-market chronological benchmark','coverage':{'source':str(SRC.relative_to(ROOT)).replace('\\','/'),'markets':int(d.market_id.nunique()),'phaseRows':int(len(d)),'forwardTestMarkets':int(sum(b['testMarkets'] for b in blocks)),'blocks':len(blocks),'special20260816ExcludedBySource':True},'experts':{'EBM':'OUR ExplainableBoosting management expert aligned to same M1 task','LIGHTGBM':'current-state expert','TINY_TRANSFORMER':'causal 16-state lifecycle expert'},'fixedCandidates':{'AVG3':'1/3 each','TRI_BLEND_25_45_30':'25% EBM + 45% LightGBM + 30% Transformer','EBM40_LGB60':'40% EBM + 60% LightGBM'},'summary':summary,'aggregateMarketWins':market_wins,'blocks':blocks,'guards':['No weight/threshold sweep.','Same frozen features/labels/chronological blocks as Large300 benchmark.','Sequence strictly current-and-past same-market.','No settlement/winner/future features.','Research only; no runtime authority.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'aggregateMarketWins':market_wins},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
