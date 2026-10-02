from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_weak_completion_economics_pilot_v2_rep2.json'
def met(y,p): return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def fit(tr,te,fs,seed):
 m=HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=100,l2_regularization=5.,random_state=seed).fit(tr[fs],tr.y_continue_repair_5s)
 return met(te.y_continue_repair_5s,m.predict_proba(te[fs])[:,1])
def main():
 d=pd.read_csv(SRC).sort_values(['market_id','first_event_ms','parent_id']).copy()
 d['parent_predict_mid']=np.where(d.side.eq('UP'),d.predict_up_mid,d.predict_down_mid)
 d['cheapness_vs_predict']=d.parent_predict_mid-d.price
 d['completion_payoff_per_share']=1-d.price
 # label from subsequent Target behavior; current row is REPAIR only
 labels=[]
 for mid,g in d.groupby('market_id',sort=False):
  ts=g.first_event_ms.to_numpy(); modes=g['mode'].to_numpy();
  for i,(idx,row) in enumerate(g.iterrows()):
   if row['mode']!='REPAIR': continue
   t=int(row.first_event_ms); hit=False
   j=i+1
   while j<len(g) and int(ts[j])<=t+5000:
    if modes[j]=='REPAIR': hit=True; break
    j+=1
   labels.append((idx,int(hit)))
 idx=[x[0] for x in labels]; y={i:v for i,v in labels}
 r=d.loc[idx].copy(); r['y_continue_repair_5s']=[y[i] for i in idx]
 order=r.groupby('market_id').first_event_ms.min().sort_values().index.tolist()[48:96]
 x=r[r.market_id.isin(order)].copy(); tr=x[x.market_id.isin(order[:36])]; te=x[x.market_id.isin(order[36:48])]
 base=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
 context=base+['parent_predict_mid']
 price=context+['price','cheapness_vs_predict','completion_payoff_per_share']
 res={'BASE':fit(tr,te,base,20),'BASE_PLUS_SIDE_CONTEXT':fit(tr,te,context,21),'BASE_PLUS_ACTION_PRICE_DIAGNOSTIC':fit(tr,te,price,22)}
 b=res['BASE_PLUS_SIDE_CONTEXT']; a=res['BASE_PLUS_ACTION_PRICE_DIAGNOSTIC']; res['priceIncrementOverSideContext']={'deltaAuc':a['auc']-b['auc'],'deltaAp':a['ap']-b['ap'],'logLossImprovement':b['logLoss']-a['logLoss']}
 # fixed semantic slices; current rows are all REPAIR so no role leakage
 x['conf']=pd.cut(x.predict_edge,[-1,.10,.30,1],labels=['LOW','MOD','EXTREME']);x['phase']=pd.cut(x.seconds_left,[-1,60,180,301],labels=['LATE','MID','EARLY'])
 desc=[]
 for (c,p,yv),g in x.groupby(['conf','phase','y_continue_repair_5s'],observed=True):
  desc.append({'conf':str(c),'phase':str(p),'continued':int(yv),'n':int(len(g)),'medianPrice':float(g.price.median()),'medianParentPredict':float(g.parent_predict_mid.median()),'medianCheapness':float(g.cheapness_vs_predict.median()),'medianCompletionPayoff':float(g.completion_payoff_per_share.median()),'medianGap':float(g.pre_abs_payoff_gap.median())})
 art={'version':'R4_TARGET_WEAK_COMPLETION_ECONOMICS_PILOT_V2','researchOnly':True,'smallPilot':True,'runtimePromotionAllowed':False,'coverage':{'pilotMarkets':48,'trainMarkets':36,'testMarkets':12,'repairRows':int(len(x)),'trainRows':int(len(tr)),'testRows':int(len(te))},'label':'Among current Target REPAIR/weak-side Maker parents only: whether another REPAIR parent appears within next 5s. This removes ADD-vs-REPAIR side-role leakage.','guard':'Current Target chosen Maker price is still action output; price is retrospective/oracle diagnostic only. Positive incremental value would justify reconstructing strict-past public weak-side quote economics later.','results':res,'descriptive':desc}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'results':res,'desc':desc},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
