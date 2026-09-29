from __future__ import annotations
import json, joblib, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_target_teacher_economic_progress_v1 as lane
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_stage3_strictpast_role_dataset_v1.csv'
OUT=P/'r4_p0b_stage3_teacher_augmented_role_dataset_v1.csv'
REPORT=P/'r4_p0b_stage3_teacher_augmented_role_dataset_v1.json'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
TRANS=ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib'

def prob1(m,X):
 p=m.predict_proba(X); cls=list(m.classes_); return p[:,cls.index(1)]

def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan)
 missing=[f for f in lane.FULL if f not in d.columns]
 if missing: raise RuntimeError(f'missing lane FULL features: {missing}')
 teacher=pd.read_csv(lane.SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=lane.FULL+['market_id','t','seconds_left','abs_gap','risk_deficit']).copy()
 e=lane.add_future_labels(teacher).replace([np.inf,-np.inf],np.nan).dropna(subset=lane.FULL+['future_risk_deficit','future_abs_gap']).copy()
 pair=e[e.abs_gap>lane.EPS]
 pair_model=lane.model(31992).fit(pair[lane.FULL],pair.pair_balance_progress.astype(int))
 stack=joblib.load(STACK); m0=stack['M0_model']; m0f=list(stack['features']['full'])
 tr=joblib.load(TRANS); tm=tr['model']; tf=list(tr['features'])
 for f in set(m0f+tf):
  if f not in d.columns: raise RuntimeError(f'missing frozen teacher feature {f}')
 Xpair=d[lane.FULL].astype(float).to_numpy(); Xm0=d[m0f].astype(float).to_numpy(); Xtr=d[tf].astype(float).to_numpy()
 d['pairBalanceProgress']=prob1(pair_model,Xpair); d['m0EconomicProgress']=prob1(m0,Xm0); d['transitionNonprogress']=prob1(tm,Xtr)
 d.to_csv(OUT,index=False)
 rep={'version':'R4_P0B_STAGE3_TEACHER_AUGMENTED_ROLE_DATASET_V1','researchOnly':True,'actionAuthority':False,'rows':int(len(d)),'markets':int(d.marketId.nunique()),'roleCounts':d.knownRole.value_counts().to_dict(),'teacherSignals':['pairBalanceProgress','m0EconomicProgress','transitionNonprogress'],'strictPastRuntimeGuard':'All three teacher inference inputs are candidate-time strict-past features. Future labels are used only inside the already-frozen semantic teacher construction, never as Stage3 runtime inputs.','sources':{'pairBalanceTeacher':'R4 P0-B Target Teacher Economic Progress / PAIR_BALANCE_PROGRESS_DIRECT','m0':str(STACK.relative_to(ROOT)).replace('\\','/'),'transition':str(TRANS.relative_to(ROOT)).replace('\\','/')}}
 REPORT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
