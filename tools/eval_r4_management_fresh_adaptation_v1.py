from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';RET=ROOT/'data/research/lan_worker_returns/r4-mgmt-fresh-adapt-v1c'
ROWS=P/'r4_management_fresh_adaptation_v1_rows.csv'

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);o={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{},'actualCounts':pd.Series(y).value_counts().to_dict(),'predictedCounts':pd.Series(p).value_counts().to_dict()}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;o['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return o

def main():
 d=pd.read_csv(ROWS);v=d[d.split.eq('VALIDATION')].copy();y=v.option_mode_v2.astype(str).to_numpy();res={}
 h=joblib.load(P/'r4_management_student_v0_10k.joblib');pt=h['timingModel'].predict(v[h['timingFeatures']]);pm=h['modeModel'].predict(v[h['modeFeatures']]);pred=np.where(pt.astype(str)=='HOLD','HOLD',pm.astype(str));res['HIERARCHICAL_10K']=score(y,pred)
 for name,path in [('DIRECT_JOINT',P/'r4_management_student_v0_joint_policy.joblib'),('CALIBRATED_JOINT',P/'r4_management_student_v0_joint_calibrated.joblib')]:
  a=joblib.load(path);res[name]=score(y,a['model'].predict(v[a['features']]))
 a=joblib.load(RET/'r4_management_fresh_adaptation_v1.joblib');res['FRESH_ADAPTED_V1']=score(y,a['model'].predict(v[a['features']]))
 frozen=max(res[k]['balancedAccuracy'] for k in ['HIERARCHICAL_10K','DIRECT_JOINT','CALIBRATED_JOINT']);s=res['FRESH_ADAPTED_V1'];checks={'balancedAccuracy':s['balancedAccuracy']>=.58,'perClassRecall':all(s['perClass'][c]['recall']>=.40 for c in ['HOLD','MAKER','TAKER']),'beatBestFrozenBy05':s['balancedAccuracy']-frozen>=.05}
 out={'version':'R4_MANAGEMENT_FRESH_ADAPTATION_V1_VALIDATION','researchOnly':True,'actionAuthority':False,'untouchedValidationMarkets':int(v.market_id.nunique()),'rows':len(v),'variants':res,'bestFrozenBalancedAccuracy':frozen,'adaptedDeltaVsBestFrozen':s['balancedAccuracy']-frozen,'checks':checks,'gatePass':all(checks.values())};(P/'r4_management_fresh_adaptation_v1_validation_report.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
