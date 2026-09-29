from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
import test_r4_management_ebm_multi_model_markets_v1 as mm

SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_meta_router_v1.json'
OOF=ROOT/'data/research/r4_v0/hourly/r4_management_meta_router_v1_oof_rows.csv'
CLASSES=base.CLASSES; FEATURES=base.FEATURES; EXPERTS=['EBM','LIGHTGBM','TINY_TRANSFORMER']; SEED=26082741
SEM=['seconds_left','risk_deficit','floor_per_gross','absnet_ratio','weak_active_owners','dominant_active_owners','current_mode_age_s','transitions_15s']

def entropy(p):
 p=np.clip(p,1e-7,1); return -(p*np.log(p)).sum(1)

def router_features(df,pe,pl,pt):
 ps=[pe,pl,pt]; z={}
 for name,p in zip(EXPERTS,ps):
  for j,c in enumerate(CLASSES): z[f'{name}_{c}']=p[:,j]
  z[f'{name}_max']=p.max(1); z[f'{name}_entropy']=entropy(p)
 z['argmax_agree_all']=((pe.argmax(1)==pl.argmax(1))&(pl.argmax(1)==pt.argmax(1))).astype(float)
 z['ebm_lgb_agree']=(pe.argmax(1)==pl.argmax(1)).astype(float)
 z['ebm_tf_agree']=(pe.argmax(1)==pt.argmax(1)).astype(float)
 z['lgb_tf_agree']=(pl.argmax(1)==pt.argmax(1)).astype(float)
 for c in SEM: z[c]=df[c].to_numpy(float)
 return pd.DataFrame(z)

def best_expert_target(y,pe,pl,pt):
 yi=np.array([CLASSES.index(str(v)) for v in y],int); stack=np.stack([pe,pl,pt],axis=1)
 truep=stack[np.arange(len(yi)),:,yi]
 return np.asarray(EXPERTS,dtype=object)[np.argmax(truep,axis=1)]

def train_router(X,y,seed):
 # deliberately small/regularized router; predicts which expert assigns the highest probability to the true class.
 m=HistGradientBoostingClassifier(learning_rate=.05,max_iter=140,max_leaf_nodes=7,max_depth=2,min_samples_leaf=45,l2_regularization=2.0,random_state=seed)
 return m.fit(X,y)

def align_router_proba(model,X):
 p0=model.predict_proba(X); order=list(model.classes_)
 return np.column_stack([p0[:,order.index(e)] if e in order else np.zeros(len(X)) for e in EXPERTS])

def main():
 base.seed_all(SEED)
 d=pd.read_csv(SRC)
 d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=FEATURES).copy().sort_values(['market_id','t']).reset_index(drop=True)
 ms=base.market_order(d); bspec=base.blocks(ms); device='cuda' if torch.cuda.is_available() else 'cpu'
 prior_X=[]; prior_best=[]; oof_rows=[]; blocks=[]
 for bi,trm,tem in bspec:
  base.seed_all(SEED+bi)
  tr_all=d[d.market_id.isin(trm)].copy(); te_all=d[d.market_id.isin(tem)].copy()
  tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy().reset_index(drop=True)
  te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy().reset_index(drop=True)
  # base experts trained only on chronology before this test block
  em=mm.ebm(SEED+bi).fit(tr[FEATURES],tr.management_label_5s); e0=em.predict_proba(te[FEATURES]); eo=list(em.classes_); pe=np.column_stack([e0[:,eo.index(c)] for c in CLASSES])
  lm=LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.0,random_state=SEED+bi,verbosity=-1,n_jobs=-1)
  lm.fit(tr[FEATURES],tr.management_label_5s); l0=lm.predict_proba(te[FEATURES]); lo=list(lm.classes_); pl=np.column_stack([l0[:,lo.index(c)] for c in CLASSES])
  mu,sd=base.fit_scaler(tr_all); tr_e=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')]; te_e=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')]
  xtr,ytr,_,_=base.build_seq(tr_all,tr_e,mu,sd); xte,yte,_,_=base.build_seq(te_all,te_e,mu,sd)
  base.seed_all(SEED+bi+29); pt,eps=base.train_torch(base.TinyTransformer(len(FEATURES),len(CLASSES)),xtr,ytr,xte,yte,device)
  Xr=router_features(te,pe,pl,pt); best=best_expert_target(te.management_label_5s,pe,pl,pt)
  cand={'EBM':pe,'LIGHTGBM':pl,'TINY_TRANSFORMER':pt,'AVG3':(pe+pl+pt)/3,'EBM_LGB_50':.5*pe+.5*pl}
  b={'block':bi,'testMarkets':len(tem),'testRows':len(te),'transformerEpochs':eps,'routerTrainRows':int(sum(len(x) for x in prior_X))}
  if prior_X:
   PX=pd.concat(prior_X,ignore_index=True); PY=np.concatenate(prior_best)
   router=train_router(PX,PY,SEED+100+bi); rw=align_router_proba(router,Xr)
   soft=rw[:,0,None]*pe+rw[:,1,None]*pl+rw[:,2,None]*pt
   hard=np.stack([pe,pl,pt],axis=1)[np.arange(len(te)),rw.argmax(1),:]
   cand['META_SOFT']=soft; cand['META_HARD']=hard
   b['routerExpertWeightsMean']={e:float(rw[:,j].mean()) for j,e in enumerate(EXPERTS)}
   b['routerHardChoiceRate']={e:float((rw.argmax(1)==j).mean()) for j,e in enumerate(EXPERTS)}
  b['models']={k:mm.met(te.management_label_5s,p) for k,p in cand.items()}
  # market-level log loss for router blocks only
  b['marketLogLossMean']={k:float(np.mean(list(mm.market_ll(te,p).values()))) for k,p in cand.items()}
  blocks.append(b)
  # append this block's genuinely OOS expert observations only AFTER scoring this block
  prior_X.append(Xr); prior_best.append(best)
  tmp=Xr.copy(); tmp['market_id']=te.market_id.to_numpy(); tmp['t']=te.t.to_numpy(); tmp['label']=te.management_label_5s.to_numpy(); tmp['best_expert']=best; tmp['block']=bi
  oof_rows.append(tmp)
  print(json.dumps({'block':bi,'routerTrainRows':b['routerTrainRows'],'routerWeights':b.get('routerExpertWeightsMean'),'models':b['models']},ensure_ascii=False),flush=True)
 # router evaluated strictly on blocks 2-4 only
 evalb=[b for b in blocks if 'META_SOFT' in b['models']]
 names=['EBM','LIGHTGBM','TINY_TRANSFORMER','AVG3','EBM_LGB_50','META_SOFT','META_HARD']
 summary={}
 for n in names:
  q=[b['models'][n] for b in evalb]
  summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanMarketLogLoss':float(np.mean([b['marketLogLossMean'][n] for b in evalb]))}
 pd.concat(oof_rows,ignore_index=True).to_csv(OOF,index=False)
 art={'version':'R4_MANAGEMENT_META_ROUTER_V1','researchOnly':True,'actionAuthority':False,'design':{'block1':'OOS router training buffer only','evaluationBlocks':[2,3,4],'evaluationMarkets':int(sum(b['testMarkets'] for b in evalb)),'routerTarget':'expert with highest probability assigned to true class on prior OOS rows','routerInputs':'expert probabilities/confidence/entropy/agreement + management semantic state','routerModel':'small HistGradientBoostingClassifier'},'coverage':{'targetMarkets':int(d.market_id.nunique()),'routerEvaluationMarkets':int(sum(b['testMarkets'] for b in evalb)),'routerEvaluationRows':int(sum(b['testRows'] for b in evalb))},'summary':summary,'blocks':blocks,'oofRows':str(OOF.relative_to(ROOT)).replace('\\','/'),'guards':['Router never trains on current/future test block.','Only prior chronological OOS expert predictions enter router training.','No weight sweep or threshold sweep.','No settlement/winner features.','Research only; no action authority.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
