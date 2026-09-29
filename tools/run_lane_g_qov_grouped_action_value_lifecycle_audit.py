from __future__ import annotations
import argparse, importlib.util, json, os
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
BASE_PATH=HERE/'run_lane_g_qov_grouped_action_value_predictability_audit.py'
spec=importlib.util.spec_from_file_location('lane_g_qov_base_audit',BASE_PATH)
if spec is None or spec.loader is None: raise ImportError(BASE_PATH)
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)

SUBMIT=list(base.FEATURES)

def verdicts(reg):
 out={}
 for t,z in reg.items():
  q=[]
  for name in ('RIDGE_FIXED','EXTRATREES_FIXED'):
   m=z['models'][name]
   if m['maeImprovementVsTrainMean']>0 and m['marketMacroMaeImprovementVsTrainMean']>0 and m.get('heldMarketMaeWinsVsTrainMean',0)>=9:q.append(name)
  out[t]={'verdict':'GROUPED_PREDICTABILITY_SIGNAL' if q else 'NO_GROUPED_GENERALIZATION','qualifyingModels':q}
 return out

def compact(reg):
 o={}
 for t,z in reg.items():
  o[t]={}
  for name in ('TRAIN_MEAN','RIDGE_FIXED','EXTRATREES_FIXED'):
   m=z['models'][name];o[t][name]={k:m.get(k) for k in ('mae','marketMacroMae','maeImprovementVsTrainMean','marketMacroMaeImprovementVsTrainMean','heldMarketMaeWinsVsTrainMean','directionalAccuracyNonZero') if k in m}
 return o

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 d=json.loads(Path(a.dataset).read_text(encoding='utf-8'));rows=d['rows'];groups=np.asarray([int(r['marketId']) for r in rows],dtype=int)
 lifecycle=sorted(k for k in rows[0] if k.startswith('lc_'))
 sets={'LIFECYCLE_ONLY':lifecycle,'SUBMIT_PLUS_LIFECYCLE':list(dict.fromkeys(SUBMIT+lifecycle))}
 allr={}
 for sname,fs in sets.items():
  base.FEATURES=list(fs);X=base._matrix(rows);reg=base.regression_audit(rows,X,groups);clf=base.classification_audit(rows,X,groups);v=verdicts(reg)
  allr[sname]={'features':fs,'featureCount':len(fs),'regression':reg,'classification':clf,'targetVerdicts':v}
  print(json.dumps({'featureSet':sname,'featureCount':len(fs),'targetVerdicts':v,'regression':compact(reg)},ensure_ascii=False),flush=True)
 out={'version':'LANE_G_QOV_GROUPED_ACTION_VALUE_LIFECYCLE_AUDIT_V1_20260907','researchOnly':True,'runtimeAuthority':False,'dataset':str(a.dataset),'nRows':len(rows),'nMarkets':len(set(groups)),'grouping':'LEAVE_ONE_MARKET_OUT_ONLY','featureSets':allr,'boundary':['same fixed 67 causal decisions','no fresh/cohort replacement','lifecycle features are strict-past/current manager state only','winner/PnL/future fill/cancel/Target future action excluded from features','fixed Ridge/ExtraTrees specs inherited from V1; no tuning','marketId grouping only, never feature','no runtime promotion/no 8781']}
 op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'featureSets':{k:v['targetVerdicts'] for k,v in allr.items()}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
