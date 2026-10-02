from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';C=P/'r4_management_curriculum_v0';S=ROOT/'data/research/supervisor_options_v0'

def model(seed):return HistGradientBoostingClassifier(learning_rate=.07,max_iter=120,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1,random_state=seed)
def score(y,p):
 out={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'counts':pd.Series(y).value_counts().to_dict(),'predicted':pd.Series(p).value_counts().to_dict(),'perClass':{}}
 for c in sorted(pd.Series(y).astype(str).unique()):
  z=np.asarray(pd.Series(y).astype(str)==c);out['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(np.asarray(p).astype(str)[z]==c))}
 return out

def main():
 timing=pd.read_csv(C/'timing_4000.csv');mode=pd.read_csv(C/'mode_3000.csv');obj=pd.read_csv(C/'maker_objective_3000.csv')
 tf=[c for c in timing.columns if c not in {'market_id','checkpoint_ms','gate_act'}];mf=[c for c in mode.columns if c not in {'market_id','checkpoint_ms','option_mode_v2'}];of=[c for c in obj.columns if c not in {'market_id','t','label'}]
 mt=model(1001).fit(timing[tf],timing.gate_act);mm=model(1002).fit(mode[mf],mode.option_mode_v2);mo=model(1003).fit(obj[of],obj.label)
 # held-out timing/mode final 80 chronology
 d=pd.read_csv(S/'supervisor_target_act_states_v2.csv');order=d.groupby('market_id').market_end_ms.min().sort_values().index.astype(int).tolist();test=set(order[-80:]);te=d[d.market_id.isin(test)].dropna(subset=tf+['gate_act']);st=score(te.gate_act,mt.predict(te[tf]));te_act=te[te.gate_act.eq('ACT')].dropna(subset=mf+['option_mode_v2']);sm=score(te_act.option_mode_v2,mm.predict(te_act[mf]))
 # held-out maker objective final 20% chronology
 od=pd.read_csv(P/'r4_p0b_target_objective_topology_rows_v2.csv').sort_values(['t','market_id']); mids=sorted(od.market_id.unique(),key=lambda m:od.loc[od.market_id==m,'t'].min()); testm=set(mids[int(len(mids)*.8):]);oe=od[od.market_id.isin(testm)].copy();oe['label']=np.where(oe.objective_family.eq('PAIR_BALANCE'),'PAIR_BALANCE_WEAK','STATE_SHAPING_DOMINANT');oe=oe.dropna(subset=of+['label']);so=score(oe.label,mo.predict(oe[of]))
 gates={'timing':st['balancedAccuracy']>=.60,'mode':sm['balancedAccuracy']>=.58,'makerObjective':so['balancedAccuracy']>=.65}
 rep={'version':'R4_MANAGEMENT_STUDENT_V0_10K','researchOnly':True,'actionAuthority':False,'curriculumRows':{'timing':len(timing),'mode':len(mode),'makerObjective':len(obj),'total':len(timing)+len(mode)+len(obj)},'heldOut':{'timing':st,'mode':sm,'makerObjective':so},'checks':gates,'gatePass':all(gates.values()),'architecture':'HOLD/ACT -> if ACT MAKER/TAKER -> if MAKER PAIR_BALANCE_WEAK/STATE_SHAPING_DOMINANT','policyWorldSeparation':True}
 joblib.dump({'version':rep['version'],'timingModel':mt,'timingFeatures':tf,'modeModel':mm,'modeFeatures':mf,'makerObjectiveModel':mo,'makerObjectiveFeatures':of,'researchOnly':True,'actionAuthority':False},P/'r4_management_student_v0_10k.joblib')
 (P/'r4_management_student_v0_10k_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
