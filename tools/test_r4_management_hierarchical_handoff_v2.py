from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_observe_v0_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_hierarchical_handoff_v2.json'
PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
EVENT=PORT+RESP
FULL=EVENT+MEM
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def metrics(y,p):
 y=np.asarray(y);pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];Y=label_binarize(y,classes=CLASSES);aps=[average_precision_score(Y[:,j],p[:,j]) for j in range(3)];auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro'));rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
 return {'n':int(len(y)),'macroAuc':auc,'macroAp':float(np.mean(aps)),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}
def hier(tr,te,seed):
 tr=tr.copy();te=te.copy();tr['event5']=(tr.management_label_5s!='OBSERVE_NO_EVENT').astype(int);ev=hgb(seed).fit(tr[EVENT],tr.event5);pe=ev.predict_proba(te[EVENT])[:,1];rt=tr[tr.event5==1].copy();rt['route_continue']=(rt.management_label_5s=='CONTINUE_WEAK').astype(int);rm=hgb(seed+1).fit(rt[FULL],rt.route_continue);pc=rm.predict_proba(te[FULL])[:,1];p=np.column_stack([pe*pc,pe*(1-pc),1-pe]);return p
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL+['market_id','t','management_label_5s']).copy();d.market_id=d.market_id.astype(int);d=d.sort_values(['market_id','t']);ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=18;rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];mono=hgb(10000+bi).fit(tr[FULL],tr.management_label_5s);pp=mono.predict_proba(te[FULL]);order=list(mono.classes_);pm=np.column_stack([pp[:,order.index(c)] for c in CLASSES]);ph=hier(tr,te,10100+bi*10);blocks.append({'block':bi,'testMarkets':tem,'MONOLITHIC_FULL':metrics(te.management_label_5s,pm),'HIER_EVENT_RESP_ROUTE_FULL':metrics(te.management_label_5s,ph)})
 def summ(k):
  x=[b[k] for b in blocks];return {'meanMacroAuc':float(np.mean([q['macroAuc'] for q in x])),'worstMacroAuc':float(np.min([q['macroAuc'] for q in x])),'stdMacroAuc':float(np.std([q['macroAuc'] for q in x])),'meanMacroAp':float(np.mean([q['macroAp'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanHandoffRecall':float(np.mean([q['perClassRecall']['HANDOFF_ALLOW'] for q in x])),'worstHandoffRecall':float(np.min([q['perClassRecall']['HANDOFF_ALLOW'] for q in x]))}
 s={k:summ(k) for k in ['MONOLITHIC_FULL','HIER_EVENT_RESP_ROUTE_FULL']};a=s['MONOLITHIC_FULL'];r=s['HIER_EVENT_RESP_ROUTE_FULL'];s['DELTA']={'meanMacroAuc':r['meanMacroAuc']-a['meanMacroAuc'],'worstMacroAuc':r['worstMacroAuc']-a['worstMacroAuc'],'meanMacroAp':r['meanMacroAp']-a['meanMacroAp'],'logLossImprovement':a['meanLogLoss']-r['meanLogLoss'],'worstLogLossImprovement':a['worstLogLoss']-r['worstLogLoss'],'meanBalancedAccuracy':r['meanBalancedAccuracy']-a['meanBalancedAccuracy'],'meanHandoffRecall':r['meanHandoffRecall']-a['meanHandoffRecall'],'worstHandoffRecall':r['worstHandoffRecall']-a['worstHandoffRecall']}
 art={'version':'R4_MANAGEMENT_HIERARCHICAL_HANDOFF_V2','researchOnly':True,'actionAuthority':False,'curriculum':'M1_HANDOFF_OBSERVE','architecture':{'eventHead':'portfolio/readiness + responsibility occupancy/age','routeHead':'event features + lifecycle memory'},'summary':s,'blocks':blocks,'guards':['No raw Predict/strike or direct action authority.','No threshold sweep; this is a routing architecture ablation.','Future event labels are teacher-only; OBSERVE is inactivity, not private termination truth.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':s,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
