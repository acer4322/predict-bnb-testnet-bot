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
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_hierarchical_multiexpert_v2.json';F=base.FEATURES;C=base.CLASSES;SEED=26082807

def ebm(seed,minleaf=16):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=minleaf,n_jobs=-2,random_state=seed)
def lgb(seed,minleaf=35):return LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=minleaf,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=seed,verbosity=-1,n_jobs=-1)
def combine(pt,ph):
 pt=np.clip(pt,1e-6,1-1e-6);ph=np.clip(ph,1e-6,1-1e-6);p=np.column_stack([1-pt,pt*ph,pt*(1-ph)]);return p/p.sum(1,keepdims=True)
def main():
 base.seed_all(SEED);d=pd.read_csv(SRC);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True);ms=base.market_order(d);device='cuda' if torch.cuda.is_available() else 'cpu';blocks=[]
 for bi,trm,tem in base.blocks(ms):
  tr_all=d[d.market_id.isin(trm)].copy();te_all=d[d.market_id.isin(tem)].copy();tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy().sort_values(['market_id','t']);te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy().sort_values(['market_id','t']);tr['transition']=(tr.management_label_5s!='CONTINUE_WEAK').astype(int);te['transition']=(te.management_label_5s!='CONTINUE_WEAK').astype(int)
  mu,sd=base.fit_scaler(tr_all)
  # Stage 1 specialists: CONTINUE vs TRANSITION.
  e1=ebm(SEED+bi).fit(tr[F],tr.transition);pe=e1.predict_proba(te[F])[:,list(e1.classes_).index(1)];l1=lgb(SEED+bi).fit(tr[F],tr.transition);pl=l1.predict_proba(te[F])[:,list(l1.classes_).index(1)];xtr,_,_,_=base.build_seq(tr_all,tr,mu,sd);xte,_,_,_=base.build_seq(te_all,te,mu,sd);base.seed_all(SEED+bi+29);pt2,ep1=bt.train_binary(base.TinyTransformer(len(F),2),xtr,tr.transition.to_numpy(np.int64),xte,te.transition.to_numpy(np.int64),device,epochs=30);ps=pt2[:,1];ptrans=(pe+pl+ps)/3
  # Stage 2 specialists: only transition rows; HANDOFF vs OBSERVE.
  tr2=tr[tr.transition==1].copy();tr2['handoff']=(tr2.management_label_5s=='HANDOFF_ALLOW').astype(int)
  e2=ebm(SEED+100+bi,minleaf=12).fit(tr2[F],tr2.handoff);phe=e2.predict_proba(te[F])[:,list(e2.classes_).index(1)];l2=lgb(SEED+100+bi,minleaf=25).fit(tr2[F],tr2.handoff);phl=l2.predict_proba(te[F])[:,list(l2.classes_).index(1)];xtr2,_,_,_=base.build_seq(tr_all,tr2,mu,sd);base.seed_all(SEED+bi+129);ph2,ep2=bt.train_binary(base.TinyTransformer(len(F),2),xtr2,tr2.handoff.to_numpy(np.int64),xte,(te.management_label_5s=='HANDOFF_ALLOW').astype(int).to_numpy(np.int64),device,epochs=30);phs=ph2[:,1];ph_avg=(phe+phl+phs)/3;ph_seq15=.425*phe+.425*phl+.15*phs
  pA=combine(ptrans,ph_avg);pB=combine(ptrans,ph_seq15);pC=combine(.425*pe+.425*pl+.15*ps,ph_avg)
  rec={'block':bi,'testMarkets':len(tem),'testRows':len(te),'epochsStage1':ep1,'epochsStage2':ep2,'models':{'HIER_FULL_AVG3':mm.met(te.management_label_5s,pA),'HIER_STAGE2_SEQ15':mm.met(te.management_label_5s,pB),'HIER_STAGE1_SEQ15':mm.met(te.management_label_5s,pC)}};blocks.append(rec);print(json.dumps(rec),flush=True)
 names=['HIER_FULL_AVG3','HIER_STAGE2_SEQ15','HIER_STAGE1_SEQ15'];summary={}
 for n in names:
  q=[b['models'][n] for b in blocks];summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'stdMacroAuc':float(np.std([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in C}}
 prev=json.loads((ROOT/'data/research/r4_v0/hourly/r4_management_ebm_multi_model_markets_v1.json').read_text(encoding='utf-8'))['summary']['AVG3']
 art={'version':'R4_MANAGEMENT_HIERARCHICAL_MULTIEXPERT_V2','researchOnly':True,'actionAuthority':False,'design':{'stage1':'dedicated EBM+LightGBM+binary Transformer: CONTINUE vs TRANSITION','stage2':'dedicated EBM+LightGBM+binary Transformer trained only on transition rows: HANDOFF vs OBSERVE'},'coverage':{'markets':int(d.market_id.nunique()),'forwardTestMarkets':sum(b['testMarkets'] for b in blocks)},'summary':summary,'referenceFlatAVG3':prev,'blocks':blocks,'guards':['Each stage trained only on its own historical curriculum.','Same chronological market blocks.','Strict causal Transformer sequences.','No threshold/weight sweep.','Research only; no action authority.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'referenceFlatAVG3':prev},indent=2))
if __name__=='__main__':main()
