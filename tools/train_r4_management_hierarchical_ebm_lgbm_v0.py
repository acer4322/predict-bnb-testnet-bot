from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_hierarchical_ebm_lgbm_v0_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_hierarchical_ebm_lgbm_v0.json'
F=base.FEATURES;C=base.CLASSES;SEED=26082781

def met(y,p):
 y=np.asarray(y,str);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);p=p/p.sum(1,keepdims=True);pred=np.asarray(C)[np.argmax(p,1)];Y=label_binarize(y,classes=C);rec=recall_score(y,pred,labels=C,average=None,zero_division=0)
 return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(C,rec)}}
def ebm_multi(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def ebm_bin(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def lgb(seed):return LGBMClassifier(objective='binary',n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.,random_state=seed,verbosity=-1,n_jobs=-1)
def align(p,classes):
 co=list(classes);return np.column_stack([p[:,co.index(c)] for c in C])
def main():
 pre=json.loads(PREREG.read_text(encoding='utf-8'));d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True);ms=base.market_order(d);blocks=[]
 for bi,trm,tem in base.blocks(ms):
  tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];tr=tr[(tr.build_now==1)&tr.management_label_5s.notna()&(tr.management_label_5s!='')].copy();te=te[(te.build_now==1)&te.management_label_5s.notna()&(te.management_label_5s!='')].copy();yt=te.management_label_5s.astype(str).to_numpy()
  bm=ebm_multi(SEED+bi).fit(tr[F],tr.management_label_5s);pb=align(bm.predict_proba(te[F]),bm.classes_)
  a=tr.copy();a['noncontinue']=(a.management_label_5s!='CONTINUE_WEAK').astype(int);ha=ebm_bin(SEED+100+bi).fit(a[F],a.noncontinue);pnc=ha.predict_proba(te[F])[:,list(ha.classes_).index(1)]
  r=tr[tr.management_label_5s!='CONTINUE_WEAK'].copy();r['handoff']=(r.management_label_5s=='HANDOFF_ALLOW').astype(int);hb=lgb(SEED+200+bi).fit(r[F],r.handoff);ph=hb.predict_proba(te[F])[:,list(hb.classes_).index(1)]
  p=np.column_stack([1-pnc,pnc*ph,pnc*(1-ph)])
  b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':int(len(tr)),'routeRows':int(len(r)),'testRows':int(len(te)),'MULTICLASS_EBM':met(yt,pb),'HIER_EBM_LGBM':met(yt,p)};blocks.append(b);print(json.dumps({'block':bi,'baseline':b['MULTICLASS_EBM'],'hier':b['HIER_EBM_LGBM']},ensure_ascii=False),flush=True)
 def summ(n):
  q=[b[n] for b in blocks];return {'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in C}}
 s={'MULTICLASS_EBM':summ('MULTICLASS_EBM'),'HIER_EBM_LGBM':summ('HIER_EBM_LGBM')};a=s['MULTICLASS_EBM'];h=s['HIER_EBM_LGBM'];rh=a['meanRecall'];rr=h['meanRecall'];minor=((rr['HANDOFF_ALLOW']>=rh['HANDOFF_ALLOW'] and rr['OBSERVE_NO_EVENT']>=rh['OBSERVE_NO_EVENT']) or ((rr['HANDOFF_ALLOW']-rh['HANDOFF_ALLOW']>=.05 and rr['OBSERVE_NO_EVENT']>=rh['OBSERVE_NO_EVENT']-.01) or (rr['OBSERVE_NO_EVENT']-rh['OBSERVE_NO_EVENT']>=.05 and rr['HANDOFF_ALLOW']>=rh['HANDOFF_ALLOW']-.01)))
 keep=(h['meanMacroAuc']>=a['meanMacroAuc'] and h['meanLogLoss']<=a['meanLogLoss'] and h['worstMacroAuc']>=a['worstMacroAuc']-.005 and minor)
 art={'version':'R4_MANAGEMENT_HIERARCHICAL_EBM_LGBM_V0','researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'coverage':{'markets':int(d.market_id.nunique()),'blocks':len(blocks)},'summary':s,'blocks':blocks,'status':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED','fixedRulePassed':bool(keep),'guards':pre['guards']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'summary':s},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
