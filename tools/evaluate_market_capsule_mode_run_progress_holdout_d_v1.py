from __future__ import annotations
import argparse,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))
try:
 from train_market_capsule_mode_run_progress_efficiency_v1 import build,evaluate,FEATURES
except ImportError:
 ROOT=Path(__file__).resolve().parents[1]
 if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
 from tools.train_market_capsule_mode_run_progress_efficiency_v1 import build,evaluate,FEATURES
from sklearn.ensemble import ExtraTreesClassifier

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--a',required=True);ap.add_argument('--d',required=True);ap.add_argument('--output',required=True);ns=ap.parse_args()
 A=build(ns.a);D=build(ns.d);p0=float(A.y.mean())
 model=ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None)
 res=evaluate(model,A,D,p0)
 out={'version':'MARKET_CAPSULE_MODE_RUN_PROGRESS_EFFICIENCY_HOLDOUT_D_V1_RESULT_20260907','researchOnly':True,'features':FEATURES,'coverage':{'A':{'rows':len(A),'markets':int(A.market_id.nunique()),'stayRate':float(A.y.mean())},'D':{'rows':len(D),'markets':int(D.market_id.nunique()),'stayRate':float(D.y.mean())}},'baselineStayProbabilityTrain':p0,'EXTRA_TREES':res,'promotionGate':res['gate']}
 p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
