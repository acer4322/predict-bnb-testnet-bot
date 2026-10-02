from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_belief_readiness_stability_v01.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
BASE=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
ALT=BASE+['placement_readiness_native_5s']

def m(seed): return HistGradientBoostingClassifier(learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=240,random_state=seed)
def mt(y,p): return {'n':int(len(y)),'repairRate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def markets(df,col): return df.groupby('market_id',as_index=False)[col].min().sort_values(col).market_id.astype(int).tolist()

def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int)
 src=m(20263201);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f['repair']=1-f.is_add.astype(int);f=f.dropna(subset=BASE+NATIVE+['first_event_ms']).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1]
 ms=markets(f,'first_event_ms');initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 out=[];cur=initial;score_rows=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(set(trm))];te=f[f.market_id.isin(set(tem))].copy()
  block={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'metrics':{},'readinessQuartiles':[]}
  for j,(name,feats) in enumerate([('BASE',BASE),('BASE_PLUS_READINESS',ALT)]):
   mod=m(20263300+10*bi+j);mod.fit(tr[feats],tr.repair);pr=mod.predict_proba(te[feats])[:,1];te['pred_'+name]=pr;block['metrics'][name]=mt(te.repair.to_numpy(),pr)
  b=block['metrics']['BASE'];a=block['metrics']['BASE_PLUS_READINESS'];block['delta']={'auc':a['auc']-b['auc'],'ap':a['ap']-b['ap'],'logLossImprovement':b['logLoss']-a['logLoss']}
  q=pd.qcut(te.placement_readiness_native_5s.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4'])
  for qi in ['Q1','Q2','Q3','Q4']:
   z=te[q==qi];block['readinessQuartiles'].append({'q':qi,'n':int(len(z)),'meanReadiness':float(z.placement_readiness_native_5s.mean()),'repairRate':float(z.repair.mean())})
  out.append(block)
  z=te[['market_id','first_event_ms','repair','placement_readiness_native_5s','pred_BASE','pred_BASE_PLUS_READINESS']].copy();z['block']=bi;score_rows.append(z)
 def summ(name):
  x=[b['metrics'][name] for b in out];return {'meanAuc':float(np.mean([a['auc'] for a in x])),'worstAuc':float(np.min([a['auc'] for a in x])),'stdAuc':float(np.std([a['auc'] for a in x])),'meanAp':float(np.mean([a['ap'] for a in x])),'worstAp':float(np.min([a['ap'] for a in x])),'meanLogLoss':float(np.mean([a['logLoss'] for a in x])),'worstLogLoss':float(np.max([a['logLoss'] for a in x]))}
 s={'BASE':summ('BASE'),'BASE_PLUS_READINESS':summ('BASE_PLUS_READINESS')}
 wins={'auc':sum(b['delta']['auc']>=0 for b in out),'ap':sum(b['delta']['ap']>=0 for b in out),'logLoss':sum(b['delta']['logLossImprovement']>=0 for b in out),'all3':sum(b['delta']['auc']>=0 and b['delta']['ap']>=0 and b['delta']['logLossImprovement']>=0 for b in out)}
 art={'version':'R4_BELIEF_READINESS_STABILITY_V01','researchOnly':True,'runtimePromotionAllowed':False,'purpose':'Forward-stability test of adding one semantically transferred placement-readiness belief to the established logic+Predict+strike Formation context.','coverage':{'formationRows':int(len(f)),'formationMarkets':int(f.market_id.nunique()),'placementRows':int(len(p)),'placementMarkets':int(p.market_id.nunique())},'temporalGuard':{'sourceMaxMs':int(p.decision_sampled_at_ms.max()),'formationMinMs':int(f.first_event_ms.min()),'strictlyEarlier':bool(p.decision_sampled_at_ms.max()<f.first_event_ms.min())},'forwardDesign':{'initialHistoryMarkets':initial,'blocks':4,'expandingWindow':True},'blocks':out,'summary':s,'winsVsBase':wins,'guards':['No raw spot/futures micro forwarded.','Placement readiness learned only on strictly earlier Target placement cohort.','No threshold/hyperparameter sweep.','Belief is not direct action authority.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');pd.concat(score_rows,ignore_index=True).to_csv(OUT.with_name('r4_belief_readiness_stability_v01_scores.csv'),index=False)
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':s,'winsVsBase':wins,'blocks':out},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
