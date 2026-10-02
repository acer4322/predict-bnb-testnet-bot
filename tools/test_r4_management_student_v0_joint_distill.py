from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';C=P/'r4_management_curriculum_v0';S=ROOT/'data/research/supervisor_options_v0'

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);classes=['HOLD','MAKER','TAKER'];out={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{},'actualCounts':pd.Series(y).value_counts().to_dict(),'predictedCounts':pd.Series(p).value_counts().to_dict()}
 for c in classes:
  z=y==c;out['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return out

def gate(s):
 return bool(s['balancedAccuracy']>=.58 and s['perClass']['MAKER']['recall']>=.45 and s['perClass']['HOLD']['recall']>=.60 and s['perClass']['TAKER']['recall']>=.45)

def main():
 art=joblib.load(P/'r4_management_student_v0_10k.joblib');mt=art['timingModel'];tf=art['timingFeatures'];mm=art['modeModel'];mf=art['modeFeatures']
 d=pd.read_csv(S/'supervisor_target_act_states_v2.csv');order=d.groupby('market_id').market_end_ms.min().sort_values().index.astype(int).tolist();test=set(order[-80:]);te=d[d.market_id.isin(test)].dropna(subset=list(set(tf+mf))+['gate_act','option_mode_v2']).copy();y=np.where(te.gate_act.eq('HOLD'),'HOLD',te.option_mode_v2).astype(str)
 # baseline hard cascade
 pa=mt.predict(te[tf]);pm=mm.predict(te[mf]);pred=np.where(pa=='HOLD','HOLD',pm);base=score(y,pred)
 # soft probability composition
 tc=list(mt.classes_);mc=list(mm.classes_);pt=mt.predict_proba(te[tf]);pu=mm.predict_proba(te[mf]);ia=tc.index('ACT');ih=tc.index('HOLD');im=mc.index('MAKER');it=mc.index('TAKER');scores=np.column_stack([pt[:,ih],pt[:,ia]*pu[:,im],pt[:,ia]*pu[:,it]]);labs=np.array(['HOLD','MAKER','TAKER']);soft=score(y,labs[np.argmax(scores,axis=1)])
 # joint distill on timing curriculum only: HOLD true; ACT split by conditional mode teacher
 tr=pd.read_csv(C/'timing_4000.csv').dropna(subset=list(set(tf+mf))+['gate_act']).copy();m_pred=mm.predict(tr[mf]);jy=np.where(tr.gate_act.eq('HOLD'),'HOLD',m_pred);joint=HistGradientBoostingClassifier(learning_rate=.06,max_iter=150,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.5,random_state=1107).fit(tr[tf],jy);dist=score(y,joint.predict(te[tf]))
 variants={'NAIVE_CASCADE_BASELINE':base,'SOFT_PROBABILITY_COMPOSITION':soft,'JOINT_DISTILL_THREE_STATE':dist};checks={k:gate(v) for k,v in variants.items()};passing=[k for k,v in checks.items() if v]
 rep={'version':'R4_MANAGEMENT_STUDENT_V0_JOINT_DISTILL','researchOnly':True,'evaluationMarkets':len(test),'evaluationRows':len(te),'variants':variants,'checks':checks,'gatePass':bool(passing),'winner':max(passing,key=lambda k:variants[k]['balancedAccuracy']) if passing else None}
 (P/'r4_management_student_v0_joint_distill_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
