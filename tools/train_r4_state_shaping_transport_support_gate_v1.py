from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import IsolationForest
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';CON=P/'r4_state_shaping_transport_support_gate_v1_contract.json';SRC=P/'r4_p0b_target_objective_topology_future_rows_v2.csv';EXT=P/'r4_state_shaping_authorization_v11_external40_rows.csv';HEAD=P/'r4_state_shaping_authorization_v11_portable_shadow.joblib';OUT=P/'r4_state_shaping_transport_support_gate_v1_score.json';MOD=P/'r4_state_shaping_transport_support_gate_v1.joblib'
F=['seconds_left_norm','risk_deficit_gap_ratio','floor_gap_ratio','upside_gap_ratio','coverage','floor_per_gross','events_5s_rate','events_15s_rate','transitions_15s_rate','mode_age_log','weak_active_roots','dominant_active_roots','weak_unresolved_gap_ratio','dominant_unresolved_gap_ratio','weak_progress_ratio','dominant_progress_ratio','weak_fill5_gap_ratio','dominant_fill5_gap_ratio']
def prep(x):
 x=x.copy();g=np.maximum(x.abs_gap.to_numpy(float),18.0);x['seconds_left_norm']=x.seconds_left/300.;x['risk_deficit_gap_ratio']=x.risk_deficit/g;x['floor_gap_ratio']=x.floor/g;x['upside_gap_ratio']=x.upside/g;x['events_5s_rate']=x.events_5s/5.;x['events_15s_rate']=x.events_15s/15.;x['transitions_15s_rate']=x.transitions_15s/15.;x['mode_age_log']=np.log1p(np.maximum(x.mode_age_s,0));x['weak_unresolved_gap_ratio']=x.weak_unresolved_shares/g;x['dominant_unresolved_gap_ratio']=x.dominant_unresolved_shares/g;x['weak_fill5_gap_ratio']=x.weak_fill_shares_5s/g;x['dominant_fill5_gap_ratio']=x.dominant_fill_shares_5s/g;return x
def scale_fit(x):
 med=x.median();iqr=x.quantile(.75)-x.quantile(.25);iqr=iqr.clip(lower=1e-6);return med,iqr
def scale(x,med,iqr):return (x-med)/iqr
def runtime_rows():
 rr=[]
 for fp in sorted(P.glob('r4_state_shaping_runtime_same30_chunk*.json')):
  try:d=json.loads(fp.read_text())
  except Exception:continue
  if not isinstance(d,dict) or 'markets' not in d:continue
  for r in d['markets']:
   for z in r.get('records',[]):
    if z.get('phase')=='FORMATION_180_300' and z.get('made'):
     rr.append({'marketId':z['marketId'],'atMs':z['atMs'],'pStateShaping':z['pStateShaping'],**z['portable']})
 if not rr:return pd.DataFrame(columns=['marketId','atMs','pStateShaping']+F)
 x=pd.DataFrame(rr).drop_duplicates(['marketId','atMs']).copy();return x
def main():
 con=json.loads(CON.read_text());assert con['status']=='FROZEN_BEFORE_SCORING';tr=prep(pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan));tr=tr[(tr.current_pair_balance==1)&(tr.current_state_shaping==0)&(tr.seconds_left>180)&(tr.seconds_left<=300)].dropna(subset=F).copy();ext=pd.read_csv(EXT).replace([np.inf,-np.inf],np.nan);ext=ext[(ext.seconds_left>180)&(ext.seconds_left<=300)].dropna(subset=F).copy();run=runtime_rows().dropna(subset=F).copy();med,iqr=scale_fit(tr[F]);m=IsolationForest(n_estimators=300,max_samples='auto',contamination=.05,random_state=50510,n_jobs=-1).fit(scale(tr[F],med,iqr));trainScore=m.score_samples(scale(tr[F],med,iqr));thr=float(np.quantile(trainScore,.05));extScore=m.score_samples(scale(ext[F],med,iqr));runScore=m.score_samples(scale(run[F],med,iqr)) if len(run) else np.array([]);extIn=extScore>=thr;runIn=runScore>=thr if len(runScore) else np.array([],dtype=bool);head=joblib.load(HEAD);metrics={'train':{'rows':len(tr),'supportRate':float(np.mean(trainScore>=thr)),'scoreP05':thr,'scoreMedian':float(np.median(trainScore))},'external40':{'rows':len(ext),'supportRate':float(np.mean(extIn)),'scoreMedian':float(np.median(extScore)),'meanHeadP':float(ext.p_state_shaping_authorization.mean()),'meanHeadPInSupport':float(ext.loc[extIn,'p_state_shaping_authorization'].mean()) if extIn.any() else None,'truePositiveRate':float(ext.future_different_objective_5s.mean()),'truePositiveRateInSupport':float(ext.loc[extIn,'future_different_objective_5s'].mean()) if extIn.any() else None},'runtimeSame30Made':{'rows':len(run),'supportRate':float(np.mean(runIn)) if len(runIn) else None,'inSupportRows':int(runIn.sum()) if len(runIn) else 0,'scoreMedian':float(np.median(runScore)) if len(runScore) else None,'meanHeadP':float(run.pStateShaping.mean()) if len(run) else None,'meanHeadPInSupport':float(run.loc[runIn,'pStateShaping'].mean()) if len(runIn) and runIn.any() else None,'triggerRateAt05':float(np.mean(run.pStateShaping>=.5)) if len(run) else None,'triggerRateAt05InSupport':float(np.mean(run.loc[runIn,'pStateShaping']>=.5)) if len(runIn) and runIn.any() else None}};externalPass=metrics['external40']['supportRate']>=float(con['targetExternalCoverageMin']);out={'version':'R4_STATE_SHAPING_TRANSPORT_SUPPORT_GATE_V1_SCORE','researchOnly':True,'actionAuthority':False,'candidateHeadChanged':False,'contract':CON.name,'features':F,'metrics':metrics,'targetExternalCoverageGatePassed':bool(externalPass),'runtimeDecision':'SUPPORT_GATE_RECOMMENDED_FOR_SHADOW' if externalPass else 'SUPPORT_GATE_REJECTED','interpretation':'OOD support only. No policy threshold or PnL tuning. Runtime rows are post-admission successful Pair-Balance parents only.'};OUT.write_text(json.dumps(out,indent=2));
 if externalPass:joblib.dump({'version':'R4_STATE_SHAPING_TRANSPORT_SUPPORT_GATE_V1','researchOnly':True,'actionAuthority':False,'features':F,'median':med.to_dict(),'iqr':iqr.to_dict(),'isolationForest':m,'supportThreshold':thr,'guards':con['guards']},MOD)
 print(json.dumps(out,indent=2))
if __name__=='__main__':main()
