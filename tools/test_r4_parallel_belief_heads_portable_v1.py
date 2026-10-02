from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv';FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_heads_portable_v1.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
LOGIC_FULL=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap'];LOGIC_PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit']
CTX_FULL=LOGIC_FULL+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant'];CTX_PORT=LOGIC_PORT+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant'];PREP_PORT=CTX_PORT+['placement_readiness_native_5s']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def mt(y,p):return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int);src=hgb(20265001);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=CTX_FULL+NATIVE+['first_event_ms','side','weak_side','is_add']).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1];f=f.sort_values(['market_id','first_event_ms']).copy();g=f.groupby('market_id');f['next_ms']=g.first_event_ms.shift(-1);f['next_side']=g.side.shift(-1);f=f[f.next_ms.notna()].copy();f['dt']=f.next_ms-f.first_event_ms;f['prepare_weak5']=((f.dt>0)&(f.dt<=5000)&(f.next_side==f.weak_side)).astype(int);f['build']=(f.is_add.astype(int)==0).astype(int)
 ms=f.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms').market_id.astype(int).tolist();initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(set(trm))];te=f[f.market_id.isin(set(tem))];b={'block':bi,'testMarkets':tem,'metrics':{}}
  specs=[('BUILD_FULL','build',CTX_FULL),('BUILD_PORTABLE','build',CTX_PORT),('PREPARE_FULL_CTX','prepare_weak5',CTX_FULL),('PREPARE_PORT_CTX','prepare_weak5',CTX_PORT),('PREPARE_PORT_READINESS','prepare_weak5',PREP_PORT)]
  for j,(name,target,feats) in enumerate(specs):
   m=hgb(20265100+bi*10+j);m.fit(tr[feats],tr[target]);pr=m.predict_proba(te[feats])[:,1];b['metrics'][name]=mt(te[target].to_numpy(),pr)
  blocks.append(b)
 def s(name):
  x=[b['metrics'][name] for b in blocks];return {'meanAuc':float(np.mean([q['auc'] for q in x])),'worstAuc':float(np.min([q['auc'] for q in x])),'stdAuc':float(np.std([q['auc'] for q in x])),'meanAp':float(np.mean([q['ap'] for q in x])),'worstAp':float(np.min([q['ap'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x]))}
 names=['BUILD_FULL','BUILD_PORTABLE','PREPARE_FULL_CTX','PREPARE_PORT_CTX','PREPARE_PORT_READINESS'];summary={n:s(n) for n in names};art={'version':'R4_PARALLEL_BELIEF_HEADS_PORTABLE_V1','researchOnly':True,'runtimePromotionAllowed':False,'purpose':'Verify that parallel BUILD/PREPARE beliefs remain useful after removing Target-only maker_abs_payoff_gap, enabling clean shadow transfer to OUR HFT combined state.','portableMapping':{'pre_abs_payoff_gap':'OUR absNet','pre_risk_deficit':'max(0,-OUR floor)','seconds_left/predict/strike':'strict-past public source'},'blocks':blocks,'summary':summary,'guards':['No maker-only gap approximation.','No threshold/hyperparameter sweep.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
