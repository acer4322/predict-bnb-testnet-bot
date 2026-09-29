from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,f1_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_hierarchical_formation_controller_v0.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
CTX=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
PREP=CTX+['placement_readiness_native_5s']
CLASSES=['ALLOW_ASYMMETRY','PREPARE_WEAK_SIDE','BUILD_WEAK_SIDE']

def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def metric3(y,p):
 pred=np.argmax(p,axis=1);yy=np.eye(3)[y]
 return {'n':int(len(y)),'classRates':{CLASSES[i]:float(np.mean(y==i)) for i in range(3)},'macroAucOvr':float(roc_auc_score(y,p,multi_class='ovr',average='macro')),'macroF1':float(f1_score(y,pred,average='macro')),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'logLoss':float(log_loss(y,p,labels=[0,1,2])),'perClassAuc':{CLASSES[i]:float(roc_auc_score((y==i).astype(int),p[:,i])) for i in range(3)},'perClassAp':{CLASSES[i]:float(average_precision_score(yy[:,i],p[:,i])) for i in range(3)}}
def metric2(y,p): return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int);src=hgb(20264101);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=CTX+NATIVE+['first_event_ms','side','weak_side','is_add']).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1];f=f.sort_values(['market_id','first_event_ms']).copy();g=f.groupby('market_id');f['next_parent_ms']=g.first_event_ms.shift(-1);f['next_parent_side']=g.side.shift(-1);f['next_dt_ms']=f.next_parent_ms-f.first_event_ms;f=f[f.next_parent_ms.notna()].copy();f['next_weak_parent_5s']=((f.next_dt_ms>0)&(f.next_dt_ms<=5000)&(f.next_parent_side==f.weak_side)).astype(int);f['is_build']=(f.is_add.astype(int)==0).astype(int);f['state']=np.where(f.is_build==1,2,np.where(f.next_weak_parent_5s==1,1,0)).astype(int)
 ms=f.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms').market_id.astype(int).tolist();initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(set(trm))].copy();te=f[f.market_id.isin(set(tem))].copy();b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'metrics':{},'headMetrics':{}}
  # Monolithic comparison head.
  counts=tr.state.value_counts().to_dict();w=tr.state.map({c:len(tr)/(3*counts[c]) for c in counts}).to_numpy();mono=hgb(20264200+bi);mono.fit(tr[CTX],tr.state,sample_weight=w);pm=mono.predict_proba(te[CTX]);b['metrics']['MONOLITHIC_CTX']=metric3(te.state.to_numpy(),pm)
  # Head A: BUILD vs NOT_BUILD. Predict/strike context belongs here.
  build=hgb(20264300+bi);build.fit(tr[CTX],tr.is_build);pb=build.predict_proba(te[CTX])[:,1];b['headMetrics']['BUILD_HEAD']=metric2(te.is_build.to_numpy(),pb)
  # Head B only sees historical NOT_BUILD states. It decides PREPARE vs ALLOW. Compare context-only vs role-routed readiness.
  trn=tr[tr.is_build==0].copy();ten=te[te.is_build==0].copy()
  prep0=hgb(20264400+bi);prep0.fit(trn[CTX],trn.next_weak_parent_5s);pp0=prep0.predict_proba(te[CTX])[:,1]
  prep1=hgb(20264500+bi);prep1.fit(trn[PREP],trn.next_weak_parent_5s);pp1=prep1.predict_proba(te[PREP])[:,1]
  b['headMetrics']['PREPARE_HEAD_CTX']=metric2(ten.next_weak_parent_5s.to_numpy(),prep0.predict_proba(ten[CTX])[:,1]);b['headMetrics']['PREPARE_HEAD_WITH_READINESS']=metric2(ten.next_weak_parent_5s.to_numpy(),prep1.predict_proba(ten[PREP])[:,1])
  # Hierarchical probabilities: BUILD from head A; remaining mass split by PREPARE head.
  for name,pp in [('HIERARCHICAL_CTX',pp0),('HIERARCHICAL_ROLE_ROUTED_READINESS',pp1)]:
   ph=np.column_stack([(1-pb)*(1-pp),(1-pb)*pp,pb]);ph=ph/np.clip(ph.sum(axis=1,keepdims=True),1e-12,None);b['metrics'][name]=metric3(te.state.to_numpy(),ph)
  a=b['metrics']['HIERARCHICAL_ROLE_ROUTED_READINESS'];c=b['metrics']['HIERARCHICAL_CTX'];b['readinessDeltaWithinHierarchy']={'macroAuc':a['macroAucOvr']-c['macroAucOvr'],'macroF1':a['macroF1']-c['macroF1'],'balancedAccuracy':a['balancedAccuracy']-c['balancedAccuracy'],'logLossImprovement':c['logLoss']-a['logLoss'],'prepareAuc':a['perClassAuc']['PREPARE_WEAK_SIDE']-c['perClassAuc']['PREPARE_WEAK_SIDE'],'prepareAp':a['perClassAp']['PREPARE_WEAK_SIDE']-c['perClassAp']['PREPARE_WEAK_SIDE']}
  blocks.append(b)
 def summ(name):
  x=[b['metrics'][name] for b in blocks];return {'meanMacroAuc':float(np.mean([q['macroAucOvr'] for q in x])),'worstMacroAuc':float(np.min([q['macroAucOvr'] for q in x])),'stdMacroAuc':float(np.std([q['macroAucOvr'] for q in x])),'meanMacroF1':float(np.mean([q['macroF1'] for q in x])),'worstMacroF1':float(np.min([q['macroF1'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x])),'meanPrepareAuc':float(np.mean([q['perClassAuc']['PREPARE_WEAK_SIDE'] for q in x])),'worstPrepareAuc':float(np.min([q['perClassAuc']['PREPARE_WEAK_SIDE'] for q in x])),'meanPrepareAp':float(np.mean([q['perClassAp']['PREPARE_WEAK_SIDE'] for q in x]))}
 summary={n:summ(n) for n in ['MONOLITHIC_CTX','HIERARCHICAL_CTX','HIERARCHICAL_ROLE_ROUTED_READINESS']};wins={'roleRoutedReadinessVsHierCtx':{'macroAuc':sum(b['readinessDeltaWithinHierarchy']['macroAuc']>=0 for b in blocks),'macroF1':sum(b['readinessDeltaWithinHierarchy']['macroF1']>=0 for b in blocks),'balancedAccuracy':sum(b['readinessDeltaWithinHierarchy']['balancedAccuracy']>=0 for b in blocks),'logLoss':sum(b['readinessDeltaWithinHierarchy']['logLossImprovement']>=0 for b in blocks),'prepareAuc':sum(b['readinessDeltaWithinHierarchy']['prepareAuc']>=0 for b in blocks),'prepareAp':sum(b['readinessDeltaWithinHierarchy']['prepareAp']>=0 for b in blocks)}}
 art={'version':'R4_HIERARCHICAL_FORMATION_CONTROLLER_V0','researchOnly':True,'runtimePromotionAllowed':False,'purpose':'Test whether role-routed beliefs are more stable when Formation is decomposed hierarchically: BUILD vs NOT_BUILD first, then PREPARE vs ALLOW only inside NOT_BUILD. Placement readiness is visible only to PREPARE head.','coverage':{'rows':int(len(f)),'markets':int(f.market_id.nunique()),'stateCounts':{CLASSES[i]:int(np.sum(f.state==i)) for i in range(3)}},'architecture':{'headA':'BUILD_WEAK_SIDE vs NOT_BUILD using portfolio + Predict/strike context','headB':'among NOT_BUILD, PREPARE_WEAK_SIDE vs ALLOW_ASYMMETRY; placement-readiness belief routed only here'},'blocks':blocks,'summary':summary,'wins':wins,'guards':['Placement readiness is never visible to BUILD head.','No raw external micro forwarded.','Placement readiness expert frozen from strictly earlier cohort.','PREPARE future label is research supervision only.','No threshold/hyperparameter sweep.','No order authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'wins':wins,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
