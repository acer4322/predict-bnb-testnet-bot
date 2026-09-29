from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))
try:
 from train_market_capsule_mode_run_progress_efficiency_v1 import build,FEATURES
except ImportError:
 ROOT=Path(__file__).resolve().parents[1]
 if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
 from tools.train_market_capsule_mode_run_progress_efficiency_v1 import build,FEATURES

def eval_set(model,df):
 p=model.predict_proba(df[FEATURES])[:,1]; pred=(p>=0.5).astype(int); y=df.y.to_numpy(); base=np.ones(len(y),dtype=int)
 acc=float((pred==y).mean()); bacc=float((base==y).mean()); sw=pred==0; actual_sw=y==0
 prec=float(((pred==0)&(y==0)).sum()/max(1,(pred==0).sum())); rec=float(((pred==0)&(y==0)).sum()/max(1,(y==0).sum())); share=float(sw.mean())
 per=[]
 for mid,g in df.assign(pred=pred).groupby('market_id'):
  yy=g.y.to_numpy();pp=g.pred.to_numpy();a=float((pp==yy).mean());ba=float((np.ones(len(yy),dtype=int)==yy).mean());per.append({'marketId':int(mid),'n':len(g),'modelAccuracy':a,'baselineAccuracy':ba,'delta':a-ba,'improved':a>ba,'tied':a==ba,'nonWorse':a>=ba})
 nw=sum(x['nonWorse'] for x in per);imp=sum(x['improved'] for x in per);worst=min(per,key=lambda x:x['delta'])
 gate={'overallAccuracyHigher':acc>bacc,'predictedSwitchShareAtLeast10pct':share>=.10,'switchRecallAtLeast20pct':rec>=.20,'switchPrecisionAbove50pct':prec>.50,'marketNonWorseAtLeast70pct':nw/len(per)>=.70,'worstRegressionAtLeastMinus15pp':worst['delta']>=-.15};gate['pass']=all(gate.values())
 return {'rows':len(df),'markets':int(df.market_id.nunique()),'overallAccuracy':acc,'baselineAlwaysStayAccuracy':bacc,'accuracyLift':acc-bacc,'predictedSwitchShare':share,'switchPrecision':prec,'switchRecall':rec,'marketImproved':imp,'marketNonWorse':nw,'marketTotal':len(per),'marketNonWorseRate':nw/len(per),'worstRegression':worst,'gate':gate}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--a',required=True);ap.add_argument('--b',required=True);ap.add_argument('--c',required=True);ap.add_argument('--d',required=True);ap.add_argument('--output',required=True);ns=ap.parse_args()
 A=build(ns.a);sets={'B':build(ns.b),'C':build(ns.c),'D':build(ns.d)}
 model=ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None);model.fit(A[FEATURES],A.y)
 res={k:eval_set(model,v) for k,v in sets.items()}
 out={'version':'MARKET_CAPSULE_MODE_RUN_ROUTER_FIXED05_V1_RESULT_20260907','researchOnly':True,'threshold':0.5,'features':FEATURES,'train':{'rows':len(A),'markets':int(A.market_id.nunique()),'stayRate':float(A.y.mean())},'tests':res,'promotionGate':{'allExternalDiagnosticGatesPass':all(x['gate']['pass'] for x in res.values())}}
 p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
