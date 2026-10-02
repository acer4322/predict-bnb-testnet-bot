from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
OWN=OUT/'taker_students_on_our_own_state_fast_v1_event_predictions.csv'
HIST=OUT/'taker_event_states_v1.csv'
FRESH=OUT/'forward_taker_states_v1.csv'
CONTRACT=OUT/'forward_contract_v1.json'
REPORT=OUT/'our_state_feature_swap_oracle_v0_report.json'
CSV=OUT/'our_state_feature_swap_oracle_v0_predictions.csv'
P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py'
spec=importlib.util.spec_from_file_location('swap_coord',P);coord=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=coord;spec.loader.exec_module(coord)
PORT=[x for x in coord.CORE if x!='seconds_left'];BOOK=list(coord.BOOK);LIFE=list(coord.LIFE);ECON=list(coord.ECON)
GROUPS={'PORTFOLIO':PORT,'BOOK':BOOK,'LIFECYCLE':LIFE,'ECONOMICS':ECON}

def metric(y,p):
 return {'n':len(y),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p)),'macroF1':float(f1_score(y,p,average='macro',zero_division=0)),'predictedDistribution':pd.Series(p).value_counts().to_dict()}
def numeric(d,fs):return d.reindex(columns=fs).apply(pd.to_numeric,errors='coerce')
def teacher():
 parts=[]
 for p in (HIST,FRESH):
  if p.exists():parts.append(pd.read_csv(p))
 d=pd.concat(parts,ignore_index=True,sort=False).drop_duplicates('parent_id',keep='last');return d

def main():
 own=pd.read_csv(OWN);tar=teacher();j=own.merge(tar,on='parent_id',how='inner',suffixes=('_our','_target')) if 'parent_id' in own.columns else own.merge(tar,left_on='target_parent_id',right_on='parent_id',how='inner',suffixes=('_our','_target'))
 contract=json.loads(CONTRACT.read_text(encoding='utf-8'));arts={k:joblib.load(contract['artifacts'][k]) for k in ('hazard_1s','side','effect')}
 # Canonical runtime column names live on OWN without suffix where TARGET didn't collide after merge; normalize explicitly.
 def base_frame():
  x=pd.DataFrame(index=j.index)
  allf=set(arts['hazard_1s']['features'])|set(arts['side']['features'])|set(arts['effect']['features'])
  for f in allf:
   if f+'_our' in j.columns:x[f]=j[f+'_our']
   elif f in j.columns:x[f]=j[f]
   else:x[f]=np.nan
  return x
 def target_col(f):
  if f+'_target' in j.columns:return j[f+'_target']
  # target-only columns may retain canonical name if OWN lacked them.
  if f in tar.columns and f in j.columns:return j[f]
  return None
 b=base_frame();truth_side=j['truth_side'].astype(str).tolist();truth_eff=j['truth_effect'].astype(str).tolist();variants={}
 specs={'OUR_ALL':[],**{f'TARGET_{k}':[k] for k in GROUPS},'TARGET_PORTFOLIO_LIFECYCLE':['PORTFOLIO','LIFECYCLE'],'TARGET_LIFE_ECON':['LIFECYCLE','ECONOMICS'],'TARGET_PORT_LIFE_ECON':['PORTFOLIO','LIFECYCLE','ECONOMICS'],'TARGET_ALL':['PORTFOLIO','BOOK','LIFECYCLE','ECONOMICS']}
 predrows=pd.DataFrame({'targetParentId':j['target_parent_id'] if 'target_parent_id' in j else j['parent_id'],'truthSide':truth_side,'truthEffect':truth_eff})
 for name,gs in specs.items():
  x=b.copy();swapped=[]
  for g in gs:
   for f in GROUPS[g]:
    v=target_col(f)
    if v is not None:x[f]=v;swapped.append(f)
  hp=arts['hazard_1s']['model'].predict_proba(numeric(x,list(arts['hazard_1s']['features'])))[:,1]
  sp=arts['side']['model'].predict(numeric(x,list(arts['side']['features']))).astype(str)
  ep=arts['effect']['model'].predict(numeric(x,list(arts['effect']['features']))).astype(str)
  variants[name]={'swappedGroups':gs,'swappedFeatureCount':len(set(swapped)),'meanHazardP1AtTrueTaker':float(np.mean(hp)),'medianHazardP1AtTrueTaker':float(np.median(hp)),'side':metric(truth_side,sp.tolist()),'effect':metric(truth_eff,ep.tolist())}
  predrows[f'{name}_p1']=hp;predrows[f'{name}_side']=sp;predrows[f'{name}_effect']=ep
 predrows.to_csv(CSV,index=False)
 # paired feature-shift audit for the most important blocks
 shifts=[]
 for g,fs in GROUPS.items():
  for f in fs:
   tc=target_col(f)
   if tc is None or f not in b:continue
   a=pd.to_numeric(b[f],errors='coerce');t=pd.to_numeric(tc,errors='coerce');mask=a.notna()&t.notna()
   if mask.sum()<30:continue
   delta=(a[mask]-t[mask]).abs();scale=max(float((t[mask].quantile(.75)-t[mask].quantile(.25))),1e-9)
   shifts.append({'group':g,'feature':f,'n':int(mask.sum()),'targetMedian':float(t[mask].median()),'ourMedian':float(a[mask].median()),'medianAbsPairedDiff':float(delta.median()),'medianAbsDiffInTargetIQR':float(delta.median()/scale)})
 shifts.sort(key=lambda z:z['medianAbsDiffInTargetIQR'],reverse=True)
 rep={'reportVersion':'OUR_STATE_FEATURE_SWAP_ORACLE_V0','researchOnly':True,'teacherOnlyOracle':True,'question':'At the same true Target Taker timestamps, which Target state block must replace OUR endogenous state to recover frozen Target SIDE/EFFECT predictions?','coverage':{'joinedEvents':len(j),'markets':int(j['target_market_id'].nunique()) if 'target_market_id' in j else int(j['market_id_target'].nunique())},'groups':GROUPS,'variants':variants,'topPairedStateShifts':shifts[:30],'interpretationBoundary':'Target feature swaps are diagnostic oracle only and cannot be runtime inputs. A large recovery from one block identifies the state distribution that OUR must learn to reproduce/normalize before closed-loop; BOOK should act as a same-public-tape sanity control.' ,'artifact':str(CSV)}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
