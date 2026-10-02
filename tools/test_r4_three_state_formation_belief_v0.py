from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,f1_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_three_state_formation_belief_v0.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
CTX=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
ALT=CTX+['placement_readiness_native_5s']
CLASSES=['ALLOW_ASYMMETRY','PREPARE_WEAK_SIDE','BUILD_WEAK_SIDE']

def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def metric(y,p):
 pred=np.argmax(p,axis=1);yy=np.eye(3)[y]
 return {'n':int(len(y)),'classRates':{CLASSES[i]:float(np.mean(y==i)) for i in range(3)},'macroAucOvr':float(roc_auc_score(y,p,multi_class='ovr',average='macro')),'macroF1':float(f1_score(y,pred,average='macro')),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'logLoss':float(log_loss(y,p,labels=[0,1,2])),'perClassAuc':{CLASSES[i]:float(roc_auc_score((y==i).astype(int),p[:,i])) for i in range(3)},'perClassAp':{CLASSES[i]:float(average_precision_score(yy[:,i],p[:,i])) for i in range(3)}}
def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int);src=hgb(20263901);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=CTX+NATIVE+['first_event_ms','side','weak_side','is_add']).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1];f=f.sort_values(['market_id','first_event_ms']).copy();g=f.groupby('market_id');f['next_parent_ms']=g.first_event_ms.shift(-1);f['next_parent_side']=g.side.shift(-1);f['next_dt_ms']=f.next_parent_ms-f.first_event_ms;f=f[f.next_parent_ms.notna()].copy();f['next_weak_parent_5s']=((f.next_dt_ms>0)&(f.next_dt_ms<=5000)&(f.next_parent_side==f.weak_side)).astype(int)
 # Retrospective research state label: BUILD if current parent is REPAIR; otherwise PREPARE if a weak-side parent follows within 5s; else ALLOW.
 f['state']=np.where(f.is_add.astype(int)==0,2,np.where(f.next_weak_parent_5s==1,1,0)).astype(int)
 ms=f.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms').market_id.astype(int).tolist();initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(set(trm))].copy();te=f[f.market_id.isin(set(tem))].copy();b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'metrics':{}}
  # Fixed balanced sample weights from each historical train block; not tuned on forward block.
  counts=tr.state.value_counts().to_dict();w=tr.state.map({c:len(tr)/(3*counts[c]) for c in counts}).to_numpy()
  for j,(name,feats) in enumerate([('LOGIC_ONLY',LOGIC),('CTX_PREDICT_STRIKE',CTX),('CTX_PLUS_READINESS',ALT)]):
   mod=hgb(20264000+bi*10+j);mod.fit(tr[feats],tr.state,sample_weight=w);pr=mod.predict_proba(te[feats]);b['metrics'][name]=metric(te.state.to_numpy(),pr)
  for name in ['CTX_PREDICT_STRIKE','CTX_PLUS_READINESS']:
   base=b['metrics']['LOGIC_ONLY'];a=b['metrics'][name];b['metrics'][name]['deltaVsLogic']={'macroAuc':a['macroAucOvr']-base['macroAucOvr'],'macroF1':a['macroF1']-base['macroF1'],'balancedAccuracy':a['balancedAccuracy']-base['balancedAccuracy'],'logLossImprovement':base['logLoss']-a['logLoss']}
  a=b['metrics']['CTX_PLUS_READINESS'];c=b['metrics']['CTX_PREDICT_STRIKE'];b['readinessDeltaVsCtx']={'macroAuc':a['macroAucOvr']-c['macroAucOvr'],'macroF1':a['macroF1']-c['macroF1'],'balancedAccuracy':a['balancedAccuracy']-c['balancedAccuracy'],'logLossImprovement':c['logLoss']-a['logLoss'],'prepareAuc':a['perClassAuc']['PREPARE_WEAK_SIDE']-c['perClassAuc']['PREPARE_WEAK_SIDE'],'prepareAp':a['perClassAp']['PREPARE_WEAK_SIDE']-c['perClassAp']['PREPARE_WEAK_SIDE']};blocks.append(b)
 def summ(name):
  x=[b['metrics'][name] for b in blocks];return {'meanMacroAuc':float(np.mean([q['macroAucOvr'] for q in x])),'worstMacroAuc':float(np.min([q['macroAucOvr'] for q in x])),'stdMacroAuc':float(np.std([q['macroAucOvr'] for q in x])),'meanMacroF1':float(np.mean([q['macroF1'] for q in x])),'worstMacroF1':float(np.min([q['macroF1'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x])),'meanPrepareAuc':float(np.mean([q['perClassAuc']['PREPARE_WEAK_SIDE'] for q in x])),'worstPrepareAuc':float(np.min([q['perClassAuc']['PREPARE_WEAK_SIDE'] for q in x])),'meanPrepareAp':float(np.mean([q['perClassAp']['PREPARE_WEAK_SIDE'] for q in x]))}
 summary={n:summ(n) for n in ['LOGIC_ONLY','CTX_PREDICT_STRIKE','CTX_PLUS_READINESS']};wins={'readinessVsCtx':{'macroAuc':sum(b['readinessDeltaVsCtx']['macroAuc']>=0 for b in blocks),'macroF1':sum(b['readinessDeltaVsCtx']['macroF1']>=0 for b in blocks),'balancedAccuracy':sum(b['readinessDeltaVsCtx']['balancedAccuracy']>=0 for b in blocks),'logLoss':sum(b['readinessDeltaVsCtx']['logLossImprovement']>=0 for b in blocks),'prepareAuc':sum(b['readinessDeltaVsCtx']['prepareAuc']>=0 for b in blocks),'prepareAp':sum(b['readinessDeltaVsCtx']['prepareAp']>=0 for b in blocks)}}
 art={'version':'R4_THREE_STATE_FORMATION_BELIEF_V0','researchOnly':True,'runtimePromotionAllowed':False,'purpose':'Test a three-state Target Formation lifecycle teacher: ALLOW_ASYMMETRY vs PREPARE_WEAK_SIDE vs BUILD_WEAK_SIDE, with Predict/strike context and a semantically separate placement-readiness belief.','labelDefinition':{'ALLOW_ASYMMETRY':'current Target Maker parent is ADD and no current-weak-side parent follows within 5s','PREPARE_WEAK_SIDE':'current parent is ADD but a current-weak-side Target Maker parent follows within 5s','BUILD_WEAK_SIDE':'current Target Maker parent is REPAIR'},'coverage':{'rows':int(len(f)),'markets':int(f.market_id.nunique()),'classCounts':{CLASSES[i]:int(np.sum(f.state==i)) for i in range(3)}},'temporalGuard':{'placementSourceMaxMs':int(p.decision_sampled_at_ms.max()),'formationMinMs':int(f.first_event_ms.min()),'strictlyEarlier':bool(p.decision_sampled_at_ms.max()<f.first_event_ms.min())},'blocks':blocks,'summary':summary,'wins':wins,'guards':['PREPARE label is retrospective research supervision only; never runtime future input.','Placement readiness expert is frozen from a strictly earlier cohort.','No raw external micro forwarded.','No threshold/hyperparameter sweep.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'wins':wins,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
