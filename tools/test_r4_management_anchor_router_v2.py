from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score
from sklearn.preprocessing import label_binarize

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_meta_router_v1_oof_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_anchor_router_v2.json'
C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
SEM=['seconds_left','risk_deficit','floor_per_gross','absnet_ratio','weak_active_owners','dominant_active_owners','current_mode_age_s','transitions_15s']
SEED=26082752

def norm(p):
 p=np.clip(np.asarray(p,float),1e-7,1); return p/p.sum(1,keepdims=True)

def met(y,p):
 p=norm(p); y=np.asarray(y); pred=np.asarray(C)[p.argmax(1)]; Y=label_binarize(y,classes=C)
 return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def probs(df,prefix): return norm(df[[f'{prefix}_{c}' for c in C]].to_numpy())
def ent(p): return -(np.clip(p,1e-7,1)*np.log(np.clip(p,1e-7,1))).sum(1)

def feats(df):
 e=probs(df,'EBM'); l=probs(df,'LIGHTGBM'); t=probs(df,'TINY_TRANSFORMER')
 z={}
 for name,p in [('EBM',e),('LIGHTGBM',l)]:
  for j,c in enumerate(C): z[f'{name}_{c}']=p[:,j]
  z[f'{name}_max']=p.max(1); z[f'{name}_entropy']=ent(p)
 z['anchor_agree']=(e.argmax(1)==l.argmax(1)).astype(float)
 z['anchor_l1']=np.abs(e-l).sum(1)
 # Transformer is context only: how much sequence expert sees non-continuation pressure.
 z['tf_transition_pressure']=t[:,1]+t[:,2]
 z['tf_handoff_p']=t[:,1]; z['tf_observe_p']=t[:,2]; z['tf_entropy']=ent(t)
 for c in SEM: z[c]=df[c].to_numpy(float)
 return pd.DataFrame(z),e,l,t

def anchor_target(df,e,l):
 yi=np.array([C.index(str(v)) for v in df.label],int)
 ep=e[np.arange(len(df)),yi]; lp=l[np.arange(len(df)),yi]
 return (lp>ep).astype(int) # 1 => LGBM, 0 => EBM

def router(seed):
 return HistGradientBoostingClassifier(learning_rate=.04,max_iter=120,max_leaf_nodes=5,max_depth=2,min_samples_leaf=55,l2_regularization=3.0,random_state=seed)

def main():
 d=pd.read_csv(SRC).sort_values(['block','market_id','t']).reset_index(drop=True)
 prior=[]; blocks=[]
 for b in sorted(d.block.unique()):
  q=d[d.block==b].copy().reset_index(drop=True); X,e,l,t=feats(q); y=q.label.to_numpy(); at=anchor_target(q,e,l)
  cand={'EBM':e,'LIGHTGBM':l,'ANCHOR_50':.5*e+.5*l,'AVG3':(e+l+t)/3}
  rec={'block':int(b),'rows':int(len(q)),'markets':int(q.market_id.nunique()),'routerTrainRows':int(sum(len(x[0]) for x in prior))}
  if prior:
   PX=pd.concat([x[0] for x in prior],ignore_index=True); PY=np.concatenate([x[1] for x in prior])
   m=router(SEED+int(b)).fit(PX,PY); w=m.predict_proba(X)[:,list(m.classes_).index(1)]
   # probability LGBM gets the authority; EBM gets complementary weight.
   soft=(1-w[:,None])*e+w[:,None]*l
   # confidence dampening keeps router from extreme all-or-nothing authority.
   wd=.25+.5*w
   damp=(1-wd[:,None])*e+wd[:,None]*l
   cand['ANCHOR_ROUTER_SOFT']=soft; cand['ANCHOR_ROUTER_DAMPED']=damp
   rec['meanLgbWeight']=float(w.mean()); rec['p10LgbWeight']=float(np.quantile(w,.1)); rec['p90LgbWeight']=float(np.quantile(w,.9))
  rec['models']={k:met(y,p) for k,p in cand.items()}
  blocks.append(rec)
  prior.append((X,at)) # append only after scoring current block
  print(json.dumps(rec),flush=True)
 ev=[b for b in blocks if 'ANCHOR_ROUTER_SOFT' in b['models']]
 names=['EBM','LIGHTGBM','ANCHOR_50','AVG3','ANCHOR_ROUTER_SOFT','ANCHOR_ROUTER_DAMPED']
 summary={}
 for n in names:
  q=[b['models'][n] for b in ev]
  summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q]))}
 art={'version':'R4_MANAGEMENT_ANCHOR_ROUTER_V2','researchOnly':True,'actionAuthority':False,'design':{'finalAuthorityExperts':['EBM','LIGHTGBM'],'transformerRole':'context-only transition pressure; never receives direct final probability weight','evaluationBlocks':[2,3,4],'routerTraining':'strictly prior OOS blocks only','dampedWeight':'LGBM final weight constrained to 0.25..0.75'},'coverage':{'oofRows':int(len(d)),'markets':int(d.market_id.nunique()),'evaluationMarkets':int(sum(b['markets'] for b in ev))},'summary':summary,'blocks':blocks,'guards':['No current/future block enters router training.','Transformer is context-only, consistent with external-HFT weakness.','No threshold or weight sweep.','Research only; no action authority.']}
 OUT.write_text(json.dumps(art,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary},indent=2))
if __name__=='__main__': main()
