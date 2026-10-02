from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from interpret.glassbox import ExplainableBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_progress_transition_gate_v1.json'
PRE=ROOT/'data/research/r4_v0/hourly/r4_management_progress_transition_gate_v1_preregistered.json'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s'];FULL=BASE+PROG

def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=220,random_state=seed)
def ebm(seed,features):return ExplainableBoostingClassifier(feature_names=features,max_bins=64,max_interaction_bins=16,interactions=4,outer_bags=4,learning_rate=.035,max_rounds=700,early_stopping_rounds=50,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan)
 d=d[(d.seconds_left>=60)&(d.seconds_left<=300)&(d.build_now==1)&d.management_label_5s.notna()&(d.management_label_5s!='')].dropna(subset=FULL).copy().sort_values(['market_id','t'])
 d['transition5']=(d.management_label_5s!='CONTINUE_WEAK').astype(int)
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=min(200,max(120,int(len(ms)*2/3)));rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)]
  b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'testRows':int(len(te))}
  for name,ctor in [('HGB',lambda seed,feats:hgb(seed)),('EBM',lambda seed,feats:ebm(seed,feats))]:
   for j,(suffix,feats) in enumerate([('BASE',BASE),('PROGRESS',FULL)]):
    m=ctor(29000+bi*20+j,feats);m.fit(tr[feats],tr.transition5);b[f'{name}_{suffix}']=met(te.transition5,m.predict_proba(te[feats])[:,1])
  blocks.append(b)
 def summ(k):
  q=[b[k] for b in blocks];return {'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'meanAp':float(np.mean([x['ap'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'blockAucs':[float(x['auc']) for x in q]}
 summary={k:summ(k) for k in ['HGB_BASE','HGB_PROGRESS','EBM_BASE','EBM_PROGRESS']}
 deltas={}
 passed_any=False
 for name in ['HGB','EBM']:
  a=summary[f'{name}_BASE'];p=summary[f'{name}_PROGRESS'];delta={'meanAuc':p['meanAuc']-a['meanAuc'],'worstAuc':p['worstAuc']-a['worstAuc'],'meanAp':p['meanAp']-a['meanAp'],'logLossImprovement':a['meanLogLoss']-p['meanLogLoss'],'allProgressBlockAucAboveHalf':all(x>.5 for x in p['blockAucs'])};delta['passes']=delta['meanAuc']>=0 and delta['worstAuc']>=0 and delta['meanAp']>=0 and delta['logLossImprovement']>=0 and delta['allProgressBlockAucAboveHalf'];deltas[name]=delta;passed_any=passed_any or delta['passes']
 art={'version':'R4_MANAGEMENT_PROGRESS_TRANSITION_GATE_V1','researchOnly':True,'actionAuthority':False,'preRegistration':str(PRE.relative_to(ROOT)).replace('\\','/'),'coverage':{'rows':int(len(d)),'markets':int(d.market_id.nunique()),'positiveRate':float(d.transition5.mean()),'blocks':len(blocks)},'summary':summary,'deltas':deltas,'status':'TESTED_KEEP_SIGNAL' if passed_any else 'TESTED_REJECTED','fixedRulePassed':passed_any,'blocks':blocks,'guards':['No threshold sweep.','Same four expanding chronological blocks.','Future label only scoring.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'coverage':art['coverage'],'summary':summary,'deltas':deltas},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
