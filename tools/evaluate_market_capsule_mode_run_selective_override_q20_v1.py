from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.model_selection import GroupKFold
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))
try:
 from train_market_capsule_mode_run_progress_efficiency_v1 import build,FEATURES
except ImportError:
 ROOT=Path(__file__).resolve().parents[1]
 if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
 from tools.train_market_capsule_mode_run_progress_efficiency_v1 import build,FEATURES
def model(): return ExtraTreesClassifier(n_estimators=300,min_samples_leaf=20,max_features='sqrt',random_state=1,n_jobs=1,class_weight=None)
def evalset(df,p,cut):
 y=df.y.to_numpy();pred=(p>cut).astype(int);base=np.ones(len(y),dtype=int);acc=float((pred==y).mean());bacc=float((base==y).mean());share=float((pred==0).mean());tp=int(((pred==0)&(y==0)).sum());prec=tp/max(1,int((pred==0).sum()));rec=tp/max(1,int((y==0).sum()));per=[]
 for mid,g in df.assign(pred=pred).groupby('market_id'):
  yy=g.y.to_numpy();pp=g.pred.to_numpy();a=float((pp==yy).mean());ba=float((np.ones(len(yy),dtype=int)==yy).mean());per.append({'marketId':int(mid),'delta':a-ba,'modelAccuracy':a,'baselineAccuracy':ba,'nonWorse':a>=ba,'improved':a>ba})
 nw=sum(x['nonWorse'] for x in per);worst=min(per,key=lambda x:x['delta']);gate={'overallAccuracyHigher':acc>bacc,'switchPrecisionAbove50pct':prec>.50,'switchRecallAtLeast20pct':rec>=.20,'marketNonWorseAtLeast70pct':nw/len(per)>=.70,'worstRegressionAtLeastMinus15pp':worst['delta']>=-.15};gate['pass']=all(gate.values())
 return {'overallAccuracy':acc,'baselineAlwaysStayAccuracy':bacc,'accuracyLift':acc-bacc,'predictedSwitchShare':share,'switchPrecision':prec,'switchRecall':rec,'marketImproved':sum(x['improved'] for x in per),'marketNonWorse':nw,'marketTotal':len(per),'marketNonWorseRate':nw/len(per),'worstRegression':worst,'gate':gate}
def main():
 ap=argparse.ArgumentParser();[ap.add_argument('--'+x,required=True) for x in ['a','b','c','d','output']];ns=ap.parse_args();A=build(ns.a);sets={'B':build(ns.b),'C':build(ns.c),'D':build(ns.d)};g=A.market_id.to_numpy();oof=np.zeros(len(A));kf=GroupKFold(n_splits=5)
 for tr,va in kf.split(A[FEATURES],A.y,g):
  m=model();m.fit(A.iloc[tr][FEATURES],A.iloc[tr].y);oof[va]=m.predict_proba(A.iloc[va][FEATURES])[:,1]
 cut=float(np.quantile(oof,.20));full=model();full.fit(A[FEATURES],A.y);res={}
 for k,df in sets.items():res[k]=evalset(df,full.predict_proba(df[FEATURES])[:,1],cut)
 out={'version':'MARKET_CAPSULE_MODE_RUN_SELECTIVE_OVERRIDE_Q20_V1_RESULT_20260907','researchOnly':True,'features':FEATURES,'oofCutoffQ20':cut,'oofRange':{'min':float(oof.min()),'median':float(np.median(oof)),'max':float(oof.max())},'tests':res,'promotionGate':{'allExternalPass':all(v['gate']['pass'] for v in res.values())}};p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
