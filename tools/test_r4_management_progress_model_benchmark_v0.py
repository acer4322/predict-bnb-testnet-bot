from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_progress_model_benchmark_v0_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_progress_model_benchmark_v0.json'
PORT=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross'];RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s'];MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s'];PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s'];F=PORT+RESP+MEM+PROG;C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT'];SEED=26082791

def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def ebm(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def lgb_multi(seed):return LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.,random_state=seed,verbosity=-1,n_jobs=-1)
def lgb_bin(seed):return LGBMClassifier(objective='binary',n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.,random_state=seed,verbosity=-1,n_jobs=-1)
def binmet(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def align(p,classes):
 co=list(classes);return np.column_stack([p[:,co.index(c)] for c in C])
def mm(y,p):
 y=np.asarray(y,str);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);p=p/p.sum(1,keepdims=True);pred=np.asarray(C)[np.argmax(p,1)];Y=label_binarize(y,classes=C);rec=recall_score(y,pred,labels=C,average=None,zero_division=0);return {'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'recall':{c:float(v) for c,v in zip(C,rec)}}
def blocks(ms):
 initial=min(200,max(120,int(len(ms)*2/3)));rem=len(ms)-initial;s=[rem//4]*4
 for i in range(rem%4):s[i]+=1
 cur=initial;out=[]
 for bi,n in enumerate(s,1):out.append((bi,ms[:cur],ms[cur:cur+n]));cur+=n
 return out

def main():
 pre=json.loads(PREREG.read_text(encoding='utf-8'));d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=F).copy();d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].sort_values(['market_id','t']);ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();res=[]
 for bi,trm,tem in blocks(ms):
  tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];tr=tr[tr.build_now==1].copy();te=te[te.build_now==1].copy();yt=te.management_label_5s.astype(str).to_numpy();y0=tr.continue_weak_5s.astype(int).to_numpy();yt0=te.continue_weak_5s.astype(int).to_numpy();b={'block':bi,'testMarkets':len(tem)}
  # HGB
  m0=hgb(SEED+bi).fit(tr[F],y0);m1=hgb(SEED+10+bi).fit(tr[F],tr.management_label_5s);b['HGB']={'M0':binmet(yt0,m0.predict_proba(te[F])[:,1]),'M1':mm(yt,align(m1.predict_proba(te[F]),m1.classes_))}
  # EBM
  e0=ebm(SEED+20+bi).fit(tr[F],y0);e1=ebm(SEED+30+bi).fit(tr[F],tr.management_label_5s);b['EBM']={'M0':binmet(yt0,e0.predict_proba(te[F])[:,list(e0.classes_).index(1)]),'M1':mm(yt,align(e1.predict_proba(te[F]),e1.classes_))}
  # LGBM
  l0=lgb_bin(SEED+40+bi).fit(tr[F],y0);l1=lgb_multi(SEED+50+bi).fit(tr[F],tr.management_label_5s);b['LGBM']={'M0':binmet(yt0,l0.predict_proba(te[F])[:,1]),'M1':mm(yt,align(l1.predict_proba(te[F]),l1.classes_))}
  res.append(b);print(json.dumps({'block':bi,'HGB':b['HGB']['M1'],'EBM':b['EBM']['M1'],'LGBM':b['LGBM']['M1']},ensure_ascii=False),flush=True)
 summ={}
 for n in ['HGB','EBM','LGBM']:
  q=[b[n] for b in res];summ[n]={'M0':{'meanAuc':float(np.mean([x['M0']['auc'] for x in q])),'worstAuc':float(np.min([x['M0']['auc'] for x in q])),'meanAp':float(np.mean([x['M0']['ap'] for x in q])),'meanLogLoss':float(np.mean([x['M0']['logLoss'] for x in q]))},'M1':{'meanMacroAuc':float(np.mean([x['M1']['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['M1']['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['M1']['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['M1']['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['M1']['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['M1']['recall'][c] for x in q])) for c in C}}}
 best_auc=max(v['M1']['meanMacroAuc'] for v in summ.values());best_ll=min(v['M1']['meanLogLoss'] for v in summ.values());cands=[n for n,v in summ.items() if v['M1']['meanMacroAuc']>=best_auc-.002 and v['M1']['worstMacroAuc']>=max(x['M1']['worstMacroAuc'] for x in summ.values())-.005 and v['M1']['meanLogLoss']<=best_ll+.01];primary=max(cands,key=lambda n:summ[n]['M1']['meanMacroAuc']) if cands else max(summ,key=lambda n:summ[n]['M1']['meanMacroAuc'])
 art={'version':'R4_MANAGEMENT_PROGRESS_MODEL_BENCHMARK_V0','researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'coverage':{'markets':len(ms),'rows':int(len(d)),'blocks':len(res)},'features':F,'summary':summ,'primaryCandidate':primary,'blocks':res,'guards':pre['guards']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'primary':primary,'summary':summ},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
