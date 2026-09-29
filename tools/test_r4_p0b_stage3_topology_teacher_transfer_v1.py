from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FUT=P/'r4_p0b_target_objective_topology_future_rows_v2.csv'
ST=P/'r4_p0b_stage3_group_context_dataset_v1.csv'
OUT=P/'r4_p0b_stage3_topology_teacher_transfer_v1.json'
TF=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s','events_5s','events_15s','transitions_15s','mode_age_s']
MAP={'seconds_left':'seconds_left','abs_gap':'abs_gap','risk_deficit':'risk_deficit','floor':'floor','upside':'candidateUpside','absNet':'absNet','coverage':'coverage','floor_per_gross':'floor_per_gross','weak_active_roots':'weakResponsibilityCount','dominant_active_roots':'dominantResponsibilityCount','weak_unresolved_shares':'weak_unresolved_shares','dominant_unresolved_shares':'dominant_unresolved_shares','weak_progress_ratio':'weak_progress_ratio','dominant_progress_ratio':'dominant_progress_ratio','weak_fill_shares_5s':'weak_fill_shares_5s','dominant_fill_shares_5s':'dominant_fill_shares_5s','events_5s':'events_5s','events_15s':'events_15s','transitions_15s':'transitions_15s','mode_age_s':'current_mode_age_s'}
def psup(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float);return float(np.mean(a[:,None]>b[None,:])+.5*np.mean(a[:,None]==b[None,:]))
def main():
 f=pd.read_csv(FUT).replace([np.inf,-np.inf],np.nan).dropna(subset=TF+['future_different_objective_5s']).copy(); s=pd.read_csv(ST).replace([np.inf,-np.inf],np.nan).copy()
 overlap=sorted(set(f.market_id.astype(int)) & set(s.marketId.astype(int)))
 assert not overlap,overlap
 m=HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=220,random_state=38000).fit(f[TF],f.future_different_objective_5s.astype(int))
 X=pd.DataFrame({k:s[v] for k,v in MAP.items()}); s['topologyFutureDifferentBelief']=m.predict_proba(X)[:,1]
 stats={}
 for role,g in s.groupby('knownRole'):
  q=g.topologyFutureDifferentBelief.to_numpy(float);stats[str(role)]={'n':len(g),'median':float(np.median(q)),'mean':float(np.mean(q)),'min':float(np.min(q)),'max':float(np.max(q))}
 b=s[s.knownRole.isin(['REJECT_NO_ACTION','PARALLEL_STATE_SHAPING'])].copy(); y=(b.knownRole=='PARALLEL_STATE_SHAPING').astype(int); p=b.topologyFutureDifferentBelief
 out={'version':'R4_P0B_STAGE3_TOPOLOGY_TEACHER_TRANSFER_V1','researchOnly':True,'actionAuthority':False,'teacherRows':len(f),'teacherMarkets':int(f.market_id.nunique()),'stage3Rows':len(s),'stage3Markets':int(s.marketId.nunique()),'marketOverlap':overlap,'teacherTarget':'future_different_objective_5s','teacherFeatures':TF,'runtimeFeatureMapping':MAP,'roleBeliefStats':stats,'rejectVsStateShaping':{'n':len(b),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'stateGreaterRejectProbability':psup(b.loc[y==1,'topologyFutureDifferentBelief'],b.loc[y==0,'topologyFutureDifferentBelief'])},'strictPastGuard':'Teacher label uses future 5s only during Target teacher training. Stage3 inference receives only candidate-time strict-past state. No Stage3 market overlaps teacher markets.'}
 OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
