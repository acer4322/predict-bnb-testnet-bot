from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_prepare_weak_side_readiness_ordinal_v1.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
CTX=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
CONT=CTX+['placement_readiness_native_5s']
ORD=CTX+['placement_readiness_ordinal']

def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=240,random_state=seed)
def metric(y,p): return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int);src=hgb(20263701);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=CTX+NATIVE+['first_event_ms','side','weak_side']).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1];f=f.sort_values(['market_id','first_event_ms']).copy();g=f.groupby('market_id');f['next_parent_ms']=g.first_event_ms.shift(-1);f['next_parent_side']=g.side.shift(-1);f['next_dt_ms']=f.next_parent_ms-f.first_event_ms;f=f[f.next_parent_ms.notna()].copy();f['next_weak_parent_5s']=((f.next_dt_ms>0)&(f.next_dt_ms<=5000)&(f.next_parent_side==f.weak_side)).astype(int)
 ms=f.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms').market_id.astype(int).tolist();initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(set(trm))].copy();te=f[f.market_id.isin(set(tem))].copy()
  qs=[float(tr.placement_readiness_native_5s.quantile(q)) for q in (.25,.5,.75)]
  def ordinal(s): return np.digitize(s.to_numpy(),qs,right=False).astype(float)
  tr['placement_readiness_ordinal']=ordinal(tr.placement_readiness_native_5s);te['placement_readiness_ordinal']=ordinal(te.placement_readiness_native_5s)
  b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'historicalReadinessQuartileCuts':qs,'metrics':{}}
  for j,(name,feats) in enumerate([('CTX_ONLY',CTX),('CONTINUOUS_READINESS',CONT),('ORDINAL_READINESS',ORD)]):
   mod=hgb(20263800+bi*10+j);mod.fit(tr[feats],tr.next_weak_parent_5s);pr=mod.predict_proba(te[feats])[:,1];b['metrics'][name]=metric(te.next_weak_parent_5s.to_numpy(),pr)
  for name in ['CONTINUOUS_READINESS','ORDINAL_READINESS']:
   base=b['metrics']['CTX_ONLY'];alt=b['metrics'][name];b['metrics'][name]['deltaVsCtx']={'auc':alt['auc']-base['auc'],'ap':alt['ap']-base['ap'],'logLossImprovement':base['logLoss']-alt['logLoss']}
  blocks.append(b)
 def summ(name):
  x=[b['metrics'][name] for b in blocks];return {'meanAuc':float(np.mean([q['auc'] for q in x])),'worstAuc':float(np.min([q['auc'] for q in x])),'stdAuc':float(np.std([q['auc'] for q in x])),'meanAp':float(np.mean([q['ap'] for q in x])),'worstAp':float(np.min([q['ap'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x]))}
 summary={n:summ(n) for n in ['CTX_ONLY','CONTINUOUS_READINESS','ORDINAL_READINESS']}
 wins={}
 for name in ['CONTINUOUS_READINESS','ORDINAL_READINESS']:
  wins[name]={'auc':sum(b['metrics'][name]['deltaVsCtx']['auc']>=0 for b in blocks),'ap':sum(b['metrics'][name]['deltaVsCtx']['ap']>=0 for b in blocks),'logLoss':sum(b['metrics'][name]['deltaVsCtx']['logLossImprovement']>=0 for b in blocks),'all3':sum(b['metrics'][name]['deltaVsCtx']['auc']>=0 and b['metrics'][name]['deltaVsCtx']['ap']>=0 and b['metrics'][name]['deltaVsCtx']['logLossImprovement']>=0 for b in blocks)}
 art={'version':'R4_PREPARE_WEAK_SIDE_READINESS_ORDINAL_V1','researchOnly':True,'runtimePromotionAllowed':False,'question':'Can placement readiness be represented as an historical-distribution ordinal PREPARE state to reduce calibration/regime drift while retaining weak-side anticipation value?','coverage':{'rows':int(len(f)),'markets':int(f.market_id.nunique())},'blocks':blocks,'summary':summary,'winsVsCtx':wins,'guards':['Quartile cuts are fitted on historical readiness distribution only; no Target action/label used to set cuts.','No threshold/hyperparameter sweep.','No raw external micro forwarded.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'winsVsCtx':wins,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
