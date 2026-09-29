from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0_rows.csv'
LABEL=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_observe_v0_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_with_formation_belief_v1.json'
PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
FULL=PORT+RESP+MEM
FORM=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant','placement_readiness_native_5s']
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def metrics(y,p):
 y=np.asarray(y);pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];Y=label_binarize(y,classes=CLASSES);aps=[average_precision_score(Y[:,j],p[:,j]) for j in range(3)];auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro'));rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
 return {'n':int(len(y)),'macroAuc':auc,'macroAp':float(np.mean(aps)),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}
def align(pp,classes):return np.column_stack([pp[:,list(classes).index(c)] for c in CLASSES])
def main():
 raw=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=FORM+['market_id','t','build_now']).copy();raw.market_id=raw.market_id.astype(int)
 d=pd.read_csv(LABEL).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL+['market_id','t','management_label_5s']).copy();d.market_id=d.market_id.astype(int);d=d.sort_values(['market_id','t'])
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=18;rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)].copy();te=d[d.market_id.isin(tem)].copy();fr=raw[raw.market_id.isin(trm)].copy();fm=hgb(11000+bi).fit(fr[FORM],fr.build_now.astype(int));tr['formation_build_confidence']=fm.predict_proba(tr[FORM])[:,1];te['formation_build_confidence']=fm.predict_proba(te[FORM])[:,1]
  b={'block':bi,'testMarkets':tem,'rows':int(len(te))}
  for j,(name,feats) in enumerate([('M1_FULL',FULL),('M1_PLUS_FORMATION_BELIEF',FULL+['formation_build_confidence'])]):
   m=hgb(11100+bi*10+j).fit(tr[feats],tr.management_label_5s);b[name]=metrics(te.management_label_5s,align(m.predict_proba(te[feats]),m.classes_))
  blocks.append(b)
 def summ(k):
  x=[b[k] for b in blocks];return {'meanMacroAuc':float(np.mean([q['macroAuc'] for q in x])),'worstMacroAuc':float(np.min([q['macroAuc'] for q in x])),'stdMacroAuc':float(np.std([q['macroAuc'] for q in x])),'meanMacroAp':float(np.mean([q['macroAp'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanHandoffRecall':float(np.mean([q['perClassRecall']['HANDOFF_ALLOW'] for q in x])),'worstHandoffRecall':float(np.min([q['perClassRecall']['HANDOFF_ALLOW'] for q in x]))}
 s={k:summ(k) for k in ['M1_FULL','M1_PLUS_FORMATION_BELIEF']};a=s['M1_FULL'];q=s['M1_PLUS_FORMATION_BELIEF'];s['DELTA']={'meanMacroAuc':q['meanMacroAuc']-a['meanMacroAuc'],'worstMacroAuc':q['worstMacroAuc']-a['worstMacroAuc'],'meanMacroAp':q['meanMacroAp']-a['meanMacroAp'],'logLossImprovement':a['meanLogLoss']-q['meanLogLoss'],'worstLogLossImprovement':a['worstLogLoss']-q['worstLogLoss'],'meanBalancedAccuracy':q['meanBalancedAccuracy']-a['meanBalancedAccuracy'],'meanHandoffRecall':q['meanHandoffRecall']-a['meanHandoffRecall'],'worstHandoffRecall':q['worstHandoffRecall']-a['worstHandoffRecall']}
 art={'version':'R4_MANAGEMENT_HANDOFF_WITH_FORMATION_BELIEF_V1','researchOnly':True,'actionAuthority':False,'curriculum':'M1_HANDOFF_OBSERVE','question':'Does a separate semantic Formation current-BUILD confidence improve M1 continuation/handoff/observe arbitration without exposing raw Predict/strike context to the manager?','summary':s,'blocks':blocks,'guards':['Formation belief trained only on earlier train markets and current-state label.','M1 manager sees semantic Formation confidence, not raw Predict/strike.','No action authority, threshold sweep, winner/settlement, price/size/channel target.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':s,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
