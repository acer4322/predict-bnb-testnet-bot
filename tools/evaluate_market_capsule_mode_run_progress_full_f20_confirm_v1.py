from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from sklearn.ensemble import ExtraTreesClassifier
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))
try:
 from train_market_capsule_mode_run_progress_efficiency_v1 import build,evaluate,FEATURES
except ImportError:
 ROOT=Path(__file__).resolve().parents[1]
 if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
 from tools.train_market_capsule_mode_run_progress_efficiency_v1 import build,evaluate,FEATURES
def main():
 ap=argparse.ArgumentParser();[ap.add_argument('--'+x,required=True) for x in ['a','f','output']];ns=ap.parse_args();A=build(ns.a);F=build(ns.f);p0=float(A.y.mean());m=ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None);res=evaluate(m,A,F,p0);out={'version':'MARKET_CAPSULE_MODE_RUN_PROGRESS_FULL_F20_CONFIRM_V1_RESULT_20260907','researchOnly':True,'features':FEATURES,'trainRows':len(A),'trainMarkets':int(A.market_id.nunique()),'testRows':len(F),'testMarkets':int(F.market_id.nunique()),'baselineStayProbabilityTrain':p0,'F':res,'promotionGate':{'pass':res['gate']['pass']}};p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
