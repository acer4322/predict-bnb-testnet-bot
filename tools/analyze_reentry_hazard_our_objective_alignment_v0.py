from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.metrics import average_precision_score,roc_auc_score
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1';DATA=OUT/'our_synthetic_repair_add_reentry_hazard_bridge_v0_states.csv';REPORT=OUT/'reentry_hazard_our_objective_alignment_v0_report.json';NORM_ART=OUT/'post_taker_reentry_opp_scale_norm_effect_v1.joblib'
P=ROOT/'tools'/'train_scale_normalized_effect_conditioned_reentry_v1.py';spec=importlib.util.spec_from_file_location('obj_norm',P);norm=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=norm;spec.loader.exec_module(norm)

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None}
def sp(a,b):return float(pd.Series(a).corr(pd.Series(b),method='spearman'))
def main():
 d=pd.read_csv(DATA);raw=pd.DataFrame(index=d.index)
 for c in [x[6:] for x in d.columns if x.startswith('state_')]: raw[c]=d['state_'+c]
 raw['label_effect']=d.syntheticEffect;X=norm.transform(raw);m=joblib.load(NORM_ART)['model'];d['pOppNormV1']=m.predict_proba(X)[:,1]
 # Positive delta always means SWITCH is preferable to SAME for that objective.
 d['delta_mtm']=d.switchMtm-d.sameMtm;d['delta_pair_edge']=d.switchPairEdge-d.samePairEdge;d['delta_paired_shares']=d.switchPairedShares-d.samePairedShares;d['delta_floor']=d.switchFloor-d.sameFloor;d['delta_absnet_improvement']=d.sameAbsNetDelta-d.switchAbsNetDelta
 rep={'reportVersion':'REENTRY_HAZARD_OUR_OBJECTIVE_ALIGNMENT_V0','researchOnly':True,'question':'Which OUR counterfactual objective, if any, is most aligned with Target-trained OPP re-entry probability after semantically aligned synthetic REPAIR/ADD?','effects':{}}
 for e in ('REPAIR_EFFECT','ADD_EFFECT'):
  q=d[d.syntheticEffect==e].copy();out={'n':len(q)}
  for score in ('pOpp','pOppNormV1'):
   out[score]={'mean':float(q[score].mean()),'spearman':{},'switchStrictlyBetterAuc':{}}
   for col in ('delta_mtm','delta_pair_edge','delta_paired_shares','delta_floor','delta_absnet_improvement'):
    out[score]['spearman'][col]=sp(q[score],q[col]);y=(q[col]>1e-9).astype(int);out[score]['switchStrictlyBetterAuc'][col]=met(y,q[score])
  out['meanDeltasSwitchMinusSame']={c:float(q[c].mean()) for c in ('delta_mtm','delta_pair_edge','delta_paired_shares','delta_floor','delta_absnet_improvement')}
  rep['effects'][e]=out
 rep['interpretationBoundary']='If Target OPP probability aligns with pair/risk objectives but not MTM, prior bridge truth was mis-specified. If it aligns with none, the remaining gap is policy/execution-state distribution rather than a simple objective mismatch.'
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
