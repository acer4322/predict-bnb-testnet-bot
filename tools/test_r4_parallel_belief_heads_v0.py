from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_heads_v0.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
CTX=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
PREP=CTX+['placement_readiness_native_5s']

def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def metric(y,p): return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int);src=hgb(20264601);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=CTX+NATIVE+['first_event_ms','side','weak_side','is_add']).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1];f=f.sort_values(['market_id','first_event_ms']).copy();g=f.groupby('market_id');f['next_ms']=g.first_event_ms.shift(-1);f['next_side']=g.side.shift(-1);f=f[f.next_ms.notna()].copy();f['dt']=f.next_ms-f.first_event_ms;f['prepare_weak5']=((f.dt>0)&(f.dt<=5000)&(f.next_side==f.weak_side)).astype(int);f['build']=(f.is_add.astype(int)==0).astype(int)
 ms=f.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms').market_id.astype(int).tolist();initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(set(trm))];te=f[f.market_id.isin(set(tem))];b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem}
  mb=hgb(20264700+bi);mb.fit(tr[CTX],tr.build);pb=mb.predict_proba(te[CTX])[:,1];b['buildHead']=metric(te.build.to_numpy(),pb)
  p0=hgb(20264800+bi);p0.fit(tr[CTX],tr.prepare_weak5);q0=p0.predict_proba(te[CTX])[:,1]
  p1=hgb(20264900+bi);p1.fit(tr[PREP],tr.prepare_weak5);q1=p1.predict_proba(te[PREP])[:,1]
  b['prepareHeadCtx']=metric(te.prepare_weak5.to_numpy(),q0);b['prepareHeadRoleRoutedReadiness']=metric(te.prepare_weak5.to_numpy(),q1)
  b['prepareDelta']={'auc':b['prepareHeadRoleRoutedReadiness']['auc']-b['prepareHeadCtx']['auc'],'ap':b['prepareHeadRoleRoutedReadiness']['ap']-b['prepareHeadCtx']['ap'],'logLossImprovement':b['prepareHeadCtx']['logLoss']-b['prepareHeadRoleRoutedReadiness']['logLoss']}
  # Parallel beliefs may overlap. Joint score is diagnostic only: average of task AUCs and sum of binary log losses.
  b['parallelCtx']={'meanHeadAuc':float((b['buildHead']['auc']+b['prepareHeadCtx']['auc'])/2),'meanHeadAp':float((b['buildHead']['ap']+b['prepareHeadCtx']['ap'])/2),'jointLogLoss':float(b['buildHead']['logLoss']+b['prepareHeadCtx']['logLoss'])}
  b['parallelRoleRouted']={'meanHeadAuc':float((b['buildHead']['auc']+b['prepareHeadRoleRoutedReadiness']['auc'])/2),'meanHeadAp':float((b['buildHead']['ap']+b['prepareHeadRoleRoutedReadiness']['ap'])/2),'jointLogLoss':float(b['buildHead']['logLoss']+b['prepareHeadRoleRoutedReadiness']['logLoss'])}
  blocks.append(b)
 def s(head):
  x=[b[head] for b in blocks];return {'meanAuc':float(np.mean([q['auc'] for q in x])),'worstAuc':float(np.min([q['auc'] for q in x])),'stdAuc':float(np.std([q['auc'] for q in x])),'meanAp':float(np.mean([q['ap'] for q in x])),'worstAp':float(np.min([q['ap'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x]))}
 summary={'BUILD_HEAD':s('buildHead'),'PREPARE_CTX':s('prepareHeadCtx'),'PREPARE_ROLE_ROUTED':s('prepareHeadRoleRoutedReadiness')}
 for name in ['parallelCtx','parallelRoleRouted']:
  x=[b[name] for b in blocks];summary[name]={'meanHeadAuc':float(np.mean([q['meanHeadAuc'] for q in x])),'worstHeadAuc':float(np.min([q['meanHeadAuc'] for q in x])),'stdHeadAuc':float(np.std([q['meanHeadAuc'] for q in x])),'meanHeadAp':float(np.mean([q['meanHeadAp'] for q in x])),'meanJointLogLoss':float(np.mean([q['jointLogLoss'] for q in x])),'worstJointLogLoss':float(np.max([q['jointLogLoss'] for q in x]))}
 wins={'prepareReadinessVsCtx':{'auc':sum(b['prepareDelta']['auc']>=0 for b in blocks),'ap':sum(b['prepareDelta']['ap']>=0 for b in blocks),'logLoss':sum(b['prepareDelta']['logLossImprovement']>=0 for b in blocks),'all3':sum(b['prepareDelta']['auc']>=0 and b['prepareDelta']['ap']>=0 and b['prepareDelta']['logLossImprovement']>=0 for b in blocks)}}
 overlap={'buildRate':float(f.build.mean()),'prepareWeak5Rate':float(f.prepare_weak5.mean()),'overlapRateAll':float(((f.build==1)&(f.prepare_weak5==1)).mean()),'prepareGivenBuild':float(f.loc[f.build==1,'prepare_weak5'].mean()),'prepareGivenNotBuild':float(f.loc[f.build==0,'prepare_weak5'].mean()),'buildGivenPrepare':float(f.loc[f.prepare_weak5==1,'build'].mean())}
 art={'version':'R4_PARALLEL_BELIEF_HEADS_V0','researchOnly':True,'runtimePromotionAllowed':False,'purpose':'Test non-exclusive parallel beliefs: current Formation BUILD mode and independent weak-side PREPARE/continuation hazard. Placement readiness is routed only to the PREPARE head.','coverage':{'rows':int(len(f)),'markets':int(f.market_id.nunique())},'overlapEvidence':overlap,'architecture':{'buildHead':'portfolio + Predict/strike context -> P(BUILD_WEAK_SIDE now)','prepareHead':'portfolio + Predict/strike context + frozen placement readiness -> P(weak-side Maker parent within 5s)','combination':'parallel non-exclusive beliefs; controller resolution deferred'},'blocks':blocks,'summary':summary,'wins':wins,'guards':['BUILD and PREPARE are not forced mutually exclusive.','Placement readiness only enters PREPARE head.','No raw external micro forwarded.','No threshold/hyperparameter sweep.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'overlapEvidence':overlap,'summary':summary,'wins':wins,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
