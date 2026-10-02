from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_sqrt_route_only_v1.json'
PORT=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross'];RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s'];MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s'];FULL=PORT+RESP+MEM
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def sqrtw(y):
 y=np.asarray(y,int);n=len(y);w={c:math.sqrt(n/(2*max(1,int((y==c).sum())))) for c in [0,1]};z=np.mean([w[int(v)] for v in y]);return {k:v/z for k,v in w.items()}
def hgb(seed,cw=None):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=35,l2_regularization=1,max_iter=220,random_state=seed,class_weight=cw)
def metric(y,p):
 y=np.asarray(y);pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];Y=label_binarize(y,classes=CLASSES);aps=[average_precision_score(Y[:,j],p[:,j]) for j in range(3)];rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0);return {'macroAuc':float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro')),'macroAp':float(np.mean(aps)),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)},'predRate':{c:float(np.mean(pred==c)) for c in CLASSES}}
def probs(tr,te,seed,sqrt_route=False):
 tr=tr.copy();tr['event5']=(tr.management_label_5s!='OBSERVE_NO_EVENT').astype(int);ev=hgb(seed).fit(tr[FULL],tr.event5);pe=ev.predict_proba(te[FULL])[:,1];rt=tr[tr.event5==1].copy();rt['route_continue']=(rt.management_label_5s=='CONTINUE_WEAK').astype(int);rm=hgb(seed+1,sqrtw(rt.route_continue) if sqrt_route else None).fit(rt[FULL],rt.route_continue);pc=rm.predict_proba(te[FULL])[:,1];return np.column_stack([pe*pc,pe*(1-pc),1-pe])
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)&(d.build_now==1)&(d.management_label_5s.notna())].dropna(subset=FULL).copy();d.market_id=d.market_id.astype(int);d=d.sort_values(['market_id','t']);ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=min(200,max(120,int(len(ms)*2/3)));rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];blocks.append({'block':bi,'UNWEIGHTED':metric(te.management_label_5s,probs(tr,te,20000+bi*10,False)),'SQRT_ROUTE_ONLY':metric(te.management_label_5s,probs(tr,te,20100+bi*10,True))})
 def summ(k):
  x=[b[k] for b in blocks];return {'meanMacroAuc':float(np.mean([q['macroAuc'] for q in x])),'worstMacroAuc':float(np.min([q['macroAuc'] for q in x])),'meanMacroAp':float(np.mean([q['macroAp'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanContinueRecall':float(np.mean([q['perClassRecall']['CONTINUE_WEAK'] for q in x])),'meanHandoffRecall':float(np.mean([q['perClassRecall']['HANDOFF_ALLOW'] for q in x])),'worstHandoffRecall':float(np.min([q['perClassRecall']['HANDOFF_ALLOW'] for q in x])),'meanObserveRecall':float(np.mean([q['perClassRecall']['OBSERVE_NO_EVENT'] for q in x]))}
 s={k:summ(k) for k in ['UNWEIGHTED','SQRT_ROUTE_ONLY']};a=s['UNWEIGHTED'];q=s['SQRT_ROUTE_ONLY'];s['DELTA']={'macroAuc':q['meanMacroAuc']-a['meanMacroAuc'],'worstMacroAuc':q['worstMacroAuc']-a['worstMacroAuc'],'macroAp':q['meanMacroAp']-a['meanMacroAp'],'logLossImprovement':a['meanLogLoss']-q['meanLogLoss'],'worstLogLossImprovement':a['worstLogLoss']-q['worstLogLoss'],'balancedAccuracy':q['meanBalancedAccuracy']-a['meanBalancedAccuracy'],'continueRecall':q['meanContinueRecall']-a['meanContinueRecall'],'handoffRecall':q['meanHandoffRecall']-a['meanHandoffRecall'],'worstHandoffRecall':q['worstHandoffRecall']-a['worstHandoffRecall'],'observeRecall':q['meanObserveRecall']-a['meanObserveRecall']}
 art={'version':'R4_MANAGEMENT_SQRT_ROUTE_ONLY_V1','researchOnly':True,'actionAuthority':False,'summary':s,'blocks':blocks,'guards':['Event head unweighted; only route head receives one predefined sqrt inverse-frequency weighting.','No weight/threshold sweep, no action authority.','Corrected 300-market cache; 2026-08-16 excluded upstream.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':s},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
