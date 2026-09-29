from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
ROWS=P/'r4_management_student_v0_fresh13_pilot_rows.csv'

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str)
 out={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{},'actualCounts':pd.Series(y).value_counts().to_dict(),'predictedCounts':pd.Series(p).value_counts().to_dict()}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;out['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return out

def cascade_predict(d,art):
 tm=art['timingModel'];mm=art['modeModel'];tf=art['timingFeatures'];mf=art['modeFeatures']
 g=tm.predict(d[tf]).astype(str);out=np.full(len(d),'HOLD',dtype=object);act=np.where(g=='ACT')[0]
 if len(act): out[act]=mm.predict(d.iloc[act][mf]).astype(str)
 return out

def main():
 d=pd.read_csv(ROWS);y=d.option_mode_v2.astype(str).to_numpy();res={}
 a=joblib.load(P/'r4_management_student_v0_10k.joblib');res['HIERARCHICAL_10K']=score(y,cascade_predict(d,a))
 for key,name in [('DIRECT_JOINT','r4_management_student_v0_joint_policy.joblib'),('CALIBRATED_JOINT','r4_management_student_v0_joint_calibrated.joblib')]:
  z=joblib.load(P/name);res[key]=score(y,z['model'].predict(d[z['features']]))
 rep={'version':'R4_MANAGEMENT_STUDENT_V0_FRESH13_COMPARE','researchOnly':True,'markets':int(d.market_id.nunique()),'rows':len(d),'variants':res,'interpretation':'Same fresh13 rows and labels for all frozen variants. Used only to localize whether fresh degradation is composition-specific or broad distribution shift.'}
 (P/'r4_management_student_v0_fresh13_compare_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
