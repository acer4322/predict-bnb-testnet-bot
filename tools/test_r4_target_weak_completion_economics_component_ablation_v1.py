from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]; SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'; OUT=ROOT/'data/research/r4_v0/hourly/r4_target_weak_completion_economics_component_ablation_v1.json'
def met(y,p): return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'ll':float(log_loss(y,p,labels=[0,1]))}
def one(offset):
 d=pd.read_csv(SRC).sort_values(['market_id','first_event_ms','parent_id']).copy(); d['parent_predict_mid']=np.where(d.side.eq('UP'),d.predict_up_mid,d.predict_down_mid); d['cheapness']=d.parent_predict_mid-d.price; d['payoff_per_share']=1-d.price; d['price_x_gap']=d.price*d.pre_abs_payoff_gap; d['cheap_x_gap']=d.cheapness*d.pre_abs_payoff_gap; d['cheap_x_conf']=d.cheapness*d.predict_edge
 lab=[]
 for mid,g in d.groupby('market_id',sort=False):
  ts=g.first_event_ms.to_numpy(); modes=g['mode'].to_numpy(); ids=list(g.index)
  for i,idx in enumerate(ids):
   if modes[i]!='REPAIR': continue
   t=int(ts[i]); hit=any(modes[j]=='REPAIR' for j in range(i+1,len(g)) if int(ts[j])<=t+5000)
   lab.append((idx,int(hit)))
 r=d.loc[[i for i,_ in lab]].copy(); r['y']=[v for _,v in lab]
 ms=r.groupby('market_id').first_event_ms.min().sort_values().index.tolist()[offset:offset+48]; tr=r[r.market_id.isin(ms[:36])]; te=r[r.market_id.isin(ms[36:48])]
 base=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','parent_predict_mid']
 sets={'BASE':base,'PLUS_RAW_PRICE':base+['price'],'PLUS_CHEAPNESS':base+['cheapness'],'PLUS_RAW_AND_CHEAP':base+['price','cheapness'],'PLUS_INTERACTIONS':base+['price','cheapness','cheap_x_gap','cheap_x_conf','price_x_gap']}
 out={}
 for k,fs in sets.items():
  m=HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=100,l2_regularization=5.,random_state=31+len(out)).fit(tr[fs],tr.y); out[k]=met(te.y,m.predict_proba(te[fs])[:,1])
 b=out['BASE'];
 for k in list(out):
  out[k]['deltaAucVsBase']=out[k]['auc']-b['auc'];out[k]['deltaApVsBase']=out[k]['ap']-b['ap'];out[k]['llImprovementVsBase']=b['ll']-out[k]['ll']
 return {'offset':offset,'markets':len(ms),'trainRows':len(tr),'testRows':len(te),'testRate':float(te.y.mean()),'results':out}
def main():
 reps=[one(0),one(48)]; names=['PLUS_RAW_PRICE','PLUS_CHEAPNESS','PLUS_RAW_AND_CHEAP','PLUS_INTERACTIONS']; stable={k:{'deltaAucBothPositive':all(r['results'][k]['deltaAucVsBase']>0 for r in reps),'deltaApBothPositive':all(r['results'][k]['deltaApVsBase']>0 for r in reps),'llBothPositive':all(r['results'][k]['llImprovementVsBase']>0 for r in reps),'meanDeltaAuc':float(np.mean([r['results'][k]['deltaAucVsBase'] for r in reps])),'meanDeltaAp':float(np.mean([r['results'][k]['deltaApVsBase'] for r in reps])),'meanLlImprovement':float(np.mean([r['results'][k]['llImprovementVsBase'] for r in reps]))} for k in names}
 art={'version':'R4_TARGET_WEAK_COMPLETION_ECONOMICS_COMPONENT_ABLATION_V1','researchOnly':True,'smallPilotOnly':True,'runtimePromotionAllowed':False,'label':'Among current REPAIR parents: another REPAIR within 5s','guard':'Target chosen price remains action-output diagnostic only; this ablation only decides what strict-past public quote economics would be worth reconstructing.','replications':reps,'crossReplication':stable}; OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'crossReplication':stable,'replications':reps},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
