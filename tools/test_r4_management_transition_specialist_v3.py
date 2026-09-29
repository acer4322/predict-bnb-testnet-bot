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
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_transition_specialist_v3.json'
C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']; SEED=26082763
SEM=['seconds_left','risk_deficit','floor_per_gross','absnet_ratio','weak_active_owners','dominant_active_owners','current_mode_age_s','transitions_15s']
def norm(p): p=np.clip(np.asarray(p,float),1e-7,1); return p/p.sum(1,keepdims=True)
def probs(d,p): return norm(d[[f'{p}_{c}' for c in C]].to_numpy())
def ent(p): return -(np.clip(p,1e-7,1)*np.log(np.clip(p,1e-7,1))).sum(1)
def met(y,p):
 p=norm(p); y=np.asarray(y); pred=np.asarray(C)[p.argmax(1)]; Y=label_binarize(y,classes=C)
 return {'n':len(y),'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}
def xfeat(d,e,l,t):
 z={'ebm_transition':e[:,1]+e[:,2],'lgb_transition':l[:,1]+l[:,2],'tf_transition':t[:,1]+t[:,2],
    'ebm_entropy':ent(e),'lgb_entropy':ent(l),'tf_entropy':ent(t),'anchor_l1':np.abs(e-l).sum(1),
    'tf_vs_anchor_transition':(t[:,1]+t[:,2])-.5*((e[:,1]+e[:,2])+(l[:,1]+l[:,2]))}
 for c in SEM:z[c]=d[c].to_numpy(float)
 return pd.DataFrame(z)
def build_probs(ptrans,split):
 split=np.clip(split,1e-6,1); split=split/split.sum(1,keepdims=True)
 out=np.zeros((len(ptrans),3)); out[:,0]=1-ptrans; out[:,1:]=ptrans[:,None]*split; return norm(out)
def main():
 d=pd.read_csv(SRC).sort_values(['block','market_id','t']).reset_index(drop=True); prior=[]; blocks=[]
 for b in sorted(d.block.unique()):
  q=d[d.block==b].copy().reset_index(drop=True); e=probs(q,'EBM'); l=probs(q,'LIGHTGBM'); t=probs(q,'TINY_TRANSFORMER'); X=xfeat(q,e,l,t); y=q.label.to_numpy(); yt=(q.label!='CONTINUE_WEAK').astype(int).to_numpy(); anchor=.5*e+.5*l; avg3=(e+l+t)/3
  rec={'block':int(b),'rows':len(q),'markets':int(q.market_id.nunique()),'trainRows':sum(len(x[0]) for x in prior),'models':{'EBM':met(y,e),'LIGHTGBM':met(y,l),'ANCHOR50':met(y,anchor),'AVG3':met(y,avg3)}}
  if prior:
   PX=pd.concat([x[0] for x in prior],ignore_index=True); PY=np.concatenate([x[1] for x in prior])
   m=HistGradientBoostingClassifier(learning_rate=.04,max_iter=140,max_leaf_nodes=7,max_depth=2,min_samples_leaf=45,l2_regularization=3,random_state=SEED+int(b)).fit(PX,PY)
   pt=m.predict_proba(X)[:,list(m.classes_).index(1)]
   # Variant A: anchors decide HANDOFF vs OBSERVE conditional on transition.
   pA=build_probs(pt,anchor[:,1:])
   # Variant B: Transformer contributes ONLY the conditional HANDOFF-vs-OBSERVE split, never CONTINUE probability.
   pB=build_probs(pt,t[:,1:])
   rec['transition']={'auc':float(roc_auc_score(yt,pt)),'ap':float(average_precision_score(yt,pt)),'logLoss':float(log_loss(yt,pt,labels=[0,1])),'rate':float(yt.mean())}
   rec['models']['TRANSITION_GATE_ANCHOR_SPLIT']=met(y,pA); rec['models']['TRANSITION_GATE_TF_SPLIT']=met(y,pB)
  blocks.append(rec); prior.append((X,yt)); print(json.dumps(rec),flush=True)
 ev=[b for b in blocks if 'TRANSITION_GATE_ANCHOR_SPLIT' in b['models']]; names=['EBM','LIGHTGBM','ANCHOR50','AVG3','TRANSITION_GATE_ANCHOR_SPLIT','TRANSITION_GATE_TF_SPLIT']; summary={}
 for n in names:
  a=[b['models'][n] for b in ev]; summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in a])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in a])),'meanMacroAp':float(np.mean([x['macroAp'] for x in a])),'meanLogLoss':float(np.mean([x['logLoss'] for x in a])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in a]))}
 ts=[b['transition'] for b in ev]; transitionSummary={'meanAuc':float(np.mean([x['auc'] for x in ts])),'worstAuc':float(np.min([x['auc'] for x in ts])),'meanAp':float(np.mean([x['ap'] for x in ts])),'meanLogLoss':float(np.mean([x['logLoss'] for x in ts]))}
 art={'version':'R4_MANAGEMENT_TRANSITION_SPECIALIST_V3','researchOnly':True,'actionAuthority':False,'design':{'stage1':'strict-prior OOS learned CONTINUE vs TRANSITION gate','stage2A':'EBM/LGBM anchor conditional HANDOFF-vs-OBSERVE split','stage2B':'Transformer conditional HANDOFF-vs-OBSERVE split only; no CONTINUE authority','evaluationBlocks':[2,3,4]},'coverage':{'rows':len(d),'markets':int(d.market_id.nunique()),'evaluationMarkets':sum(b['markets'] for b in ev)},'transitionSummary':transitionSummary,'summary':summary,'blocks':blocks,'guards':['No current/future block in gate training.','Transformer never controls CONTINUE probability.','No threshold/weight sweep.','Research only; no action authority.']}
 OUT.write_text(json.dumps(art,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'transitionSummary':transitionSummary,'summary':summary},indent=2))
if __name__=='__main__':main()
