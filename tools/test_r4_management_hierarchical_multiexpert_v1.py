from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
import test_r4_management_binary_transition_models_v1 as bt
import test_r4_management_ebm_multi_model_markets_v1 as mm
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_hierarchical_multiexpert_v1.json';F=base.FEATURES;C=base.CLASSES;SEED=26082785

def ebm(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def lgb(seed):return LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=seed,verbosity=-1,n_jobs=-1)
def combine(ptrans,phand):
 ptrans=np.clip(ptrans,1e-6,1-1e-6);phand=np.clip(phand,1e-6,1-1e-6);p=np.column_stack([1-ptrans,ptrans*phand,ptrans*(1-phand)]);return p/p.sum(1,keepdims=True)
def main():
 base.seed_all(SEED);d=pd.read_csv(SRC);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True);ms=base.market_order(d);device='cuda' if torch.cuda.is_available() else 'cpu';blocks=[]
 for bi,trm,tem in base.blocks(ms):
  tr_all=d[d.market_id.isin(trm)].copy();te_all=d[d.market_id.isin(tem)].copy();tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy().sort_values(['market_id','t']);te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy().sort_values(['market_id','t']);tr['transition']=(tr.management_label_5s!='CONTINUE_WEAK').astype(int);te['transition']=(te.management_label_5s!='CONTINUE_WEAK').astype(int)
  # Stage 1: three independent transition experts.
  e1=ebm(SEED+bi).fit(tr[F],tr.transition);pe=e1.predict_proba(te[F])[:,list(e1.classes_).index(1)];l1=lgb(SEED+bi).fit(tr[F],tr.transition);pl=l1.predict_proba(te[F])[:,list(l1.classes_).index(1)]
  mu,sd=base.fit_scaler(tr_all);xtr,_,_,_=base.build_seq(tr_all,tr,mu,sd);xte,_,_,_=base.build_seq(te_all,te,mu,sd);base.seed_all(SEED+bi+29);pt2,eps=bt.train_binary(base.TinyTransformer(len(F),2),xtr,tr.transition.to_numpy(np.int64),xte,te.transition.to_numpy(np.int64),device,epochs=30);pt=pt2[:,1]
  ptrans_avg=(pe+pl+pt)/3;ptrans_seq15=.425*pe+.425*pl+.15*pt;ptrans_anchor=.5*pe+.5*pl
  # Stage 2: conditional HANDOFF vs OBSERVE only, no CONTINUE rows.
  tr2=tr[tr.transition==1].copy();te2=te.copy();tr2['handoff']=(tr2.management_label_5s=='HANDOFF_ALLOW').astype(int)
  e2=ebm(SEED+100+bi).fit(tr2[F],tr2.handoff);phe=e2.predict_proba(te2[F])[:,list(e2.classes_).index(1)];l2=lgb(SEED+100+bi).fit(tr2[F],tr2.handoff);phl=l2.predict_proba(te2[F])[:,list(l2.classes_).index(1)];ph=.5*phe+.5*phl
  pA=combine(ptrans_avg,ph);pB=combine(ptrans_seq15,ph);pC=combine(ptrans_anchor,ph)
  rec={'block':bi,'testMarkets':len(tem),'testRows':len(te),'transitionTransformerEpochs':eps,'transitionMetrics':{'AVG3':bt.bmet(te.transition,ptrans_avg),'SEQ15':bt.bmet(te.transition,ptrans_seq15),'ANCHOR50':bt.bmet(te.transition,ptrans_anchor)},'conditionalHandoff':{'EBM':bt.bmet((te.management_label_5s=='HANDOFF_ALLOW').astype(int),phe),'LIGHTGBM':bt.bmet((te.management_label_5s=='HANDOFF_ALLOW').astype(int),phl)},'models':{'HIER_TRANS_AVG3':mm.met(te.management_label_5s,pA),'HIER_TRANS_SEQ15':mm.met(te.management_label_5s,pB),'HIER_TRANS_ANCHOR':mm.met(te.management_label_5s,pC)}};blocks.append(rec);print(json.dumps(rec),flush=True)
 names=['HIER_TRANS_AVG3','HIER_TRANS_SEQ15','HIER_TRANS_ANCHOR'];summary={}
 for n in names:
  q=[b['models'][n] for b in blocks];summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in C}}
 prev=json.loads((ROOT/'data/research/r4_v0/hourly/r4_management_ebm_multi_model_markets_v1.json').read_text(encoding='utf-8'))['summary']
 art={'version':'R4_MANAGEMENT_HIERARCHICAL_MULTIEXPERT_V1','researchOnly':True,'actionAuthority':False,'design':{'stage1':'CONTINUE vs TRANSITION using EBM+LightGBM+direct binary Transformer','stage2':'conditional HANDOFF vs OBSERVE using EBM+LightGBM only'},'coverage':{'markets':int(d.market_id.nunique()),'forwardTestMarkets':sum(b['testMarkets'] for b in blocks)},'summary':summary,'referenceFlatMulticlass':{k:prev[k] for k in ['EBM','LIGHTGBM','TINY_TRANSFORMER','AVG3']},'blocks':blocks,'guards':['Same chronological blocks/features.','Stage2 trained only on historical transition rows.','No thresholds or weight sweep.','Research only; no action authority.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'referenceFlat':art['referenceFlatMulticlass']},indent=2))
if __name__=='__main__':main()
