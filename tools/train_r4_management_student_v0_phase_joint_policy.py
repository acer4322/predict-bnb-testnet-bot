from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0'
PHASES={'EARLY':lambda x:x>180,'MID':lambda x:(x>60)&(x<=180),'LATE':lambda x:x<=60}

def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);out={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{},'actualCounts':pd.Series(y).value_counts().to_dict(),'predictedCounts':pd.Series(p).value_counts().to_dict()}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;out['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return out

def main():
 d=pd.read_csv(S/'supervisor_target_act_states_v2.csv');order=d.groupby('market_id').market_end_ms.min().sort_values().index.astype(int).tolist();train=set(order[:-80]);test=set(order[-80:]);label='option_mode_v2';features=[c for c in d.columns if c not in {'market_id','market_end_ms','checkpoint_ms','gate_act','option_mode_v2','taker_next3s_raw'}]
 tr=d[d.market_id.isin(train)].dropna(subset=features+[label]).copy();te=d[d.market_id.isin(test)].dropna(subset=features+[label]).copy();models={};train_info={};pred=np.empty(len(te),dtype=object)
 for pi,(name,fn) in enumerate(PHASES.items()):
  a=tr[fn(tr.seconds_left)];z=te[fn(te.seconds_left)];counts=a[label].value_counts();n=int(counts.min());parts=[a[a[label].eq(c)].sample(n=n,random_state=1300+pi*10+j) for j,c in enumerate(['HOLD','MAKER','TAKER'])];bal=pd.concat(parts).sample(frac=1,random_state=1390+pi);m=HistGradientBoostingClassifier(learning_rate=.06,max_iter=150,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.5,random_state=1400+pi).fit(bal[features],bal[label]);models[name]=m;train_info[name]={'rowsBalanced':len(bal),'perClass':n,'rawCounts':counts.to_dict(),'testRows':len(z)};pred[te.index.get_indexer(z.index)]=m.predict(z[features])
 sc=score(te[label],pred);checks={'balancedAccuracy':sc['balancedAccuracy']>=.58,'holdRecall':sc['perClass']['HOLD']['recall']>=.60,'makerRecall':sc['perClass']['MAKER']['recall']>=.45,'takerRecall':sc['perClass']['TAKER']['recall']>=.45};rep={'version':'R4_MANAGEMENT_STUDENT_V0_PHASE_JOINT_POLICY','researchOnly':True,'trainMarkets':len(train),'futureMarkets':len(test),'futureRows':len(te),'phaseDefinition':{'EARLY':'>180','MID':'60<sec<=180','LATE':'<=60'},'training':train_info,'score':sc,'checks':checks,'gatePass':all(checks.values()),'deltaBalancedAccuracyVsDirectJoint':sc['balancedAccuracy']-0.5568227452163533}
 joblib.dump({'version':rep['version'],'models':models,'features':features,'phaseDefinition':rep['phaseDefinition'],'researchOnly':True,'actionAuthority':False},P/'r4_management_student_v0_phase_joint_policy.joblib');(P/'r4_management_student_v0_phase_joint_policy_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
