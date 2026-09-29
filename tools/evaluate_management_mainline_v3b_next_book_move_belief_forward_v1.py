from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss,accuracy_score

FOLDS=[(0,40,40,60),(0,60,60,80),(0,80,80,100)]
BASE=["alignedBookImbalance"]

def mat(rows,names):
 return np.array([[float((r.get("features") or {}).get(k)) if (r.get("features") or {}).get(k) is not None else np.nan for k in names] for r in rows],dtype=float)
def yvec(rows): return np.array([1 if bool((r.get("label") or {}).get("towardExpand")) else 0 for r in rows],dtype=int)
def score(y,p):
 pred=(p>=.5).astype(int)
 return {"auc":float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,"logloss":float(log_loss(y,p,labels=[0,1])),"brier":float(brier_score_loss(y,p)),"accuracy":float(accuracy_score(y,pred))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--output",required=True);a=ap.parse_args()
 d=json.loads(Path(a.input).read_text(encoding="utf-8")); rows=sorted(d["rows"],key=lambda r:(int(r["t"]),int(r["marketId"])))
 feats=sorted(rows[0]["features"].keys())
 allpred={k:[] for k in ["IMBALANCE_LOGIT","FULL_LOGIT","FULL_EXTRATREES"]}; yy=[]; mids=[]; folds=[]
 for fi,(a0,a1,b0,b1) in enumerate(FOLDS,1):
  tr=rows[a0:a1];te=rows[b0:b1]; yt=yvec(tr); y=yvec(te)
  models={
   "IMBALANCE_LOGIT":(make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=1.0,max_iter=2000,random_state=20260907)),BASE),
   "FULL_LOGIT":(make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(C=1.0,max_iter=2000,random_state=20260907)),feats),
   "FULL_EXTRATREES":(make_pipeline(SimpleImputer(strategy="median"),ExtraTreesClassifier(n_estimators=300,min_samples_leaf=4,max_features="sqrt",random_state=20260907,n_jobs=1,class_weight="balanced")),feats),
  }
  fs={"fold":fi,"train":[a0,a1],"test":[b0,b1],"trainExpand":int(yt.sum()),"testExpand":int(y.sum()),"models":{}}
  for name,(m,names) in models.items():
   m.fit(mat(tr,names),yt); p=m.predict_proba(mat(te,names))[:,1]; allpred[name].extend(p.tolist()); fs["models"][name]=score(y,p)
  yy.extend(y.tolist()); mids.extend([int(r["marketId"]) for r in te]); folds.append(fs)
 y=np.array(yy,dtype=int); overall={k:score(y,np.array(v)) for k,v in allpred.items()}
 b=np.array(allpred["IMBALANCE_LOGIT"]); per={}
 for name in ["FULL_LOGIT","FULL_EXTRATREES"]:
  p=np.array(allpred[name]); eb=-(y*np.log(np.clip(b,1e-12,1-1e-12))+(1-y)*np.log(np.clip(1-b,1e-12,1-1e-12)))
  ep=-(y*np.log(np.clip(p,1e-12,1-1e-12))+(1-y)*np.log(np.clip(1-p,1e-12,1-1e-12)))
  imp=eb-ep
  per[name]={"improvedMarkets":int((imp>1e-12).sum()),"worsenedMarkets":int((imp<-1e-12).sum()),"improvedRate":float((imp>1e-12).mean()),"meanLoglossImprovement":float(imp.mean()),"medianImprovement":float(np.median(imp)),"p10Improvement":float(np.quantile(imp,.1))}
 out={"version":"MANAGEMENT_MAINLINE_V3B_NEXT_BOOK_MOVE_BELIEF_FORWARD_V1_20260907","researchOnly":True,"runtimeAuthority":False,"rows":len(rows),"forwardTestRows":len(y),"features":feats,"folds":folds,"overall":overall,"perMarketVsImbalance":per,"decision":"PASS_EVENT_BELIEF_FORWARD_INCREMENT" if overall["FULL_EXTRATREES"]["logloss"]<overall["IMBALANCE_LOGIT"]["logloss"] and overall["FULL_EXTRATREES"]["brier"]<overall["IMBALANCE_LOGIT"]["brier"] and overall["FULL_EXTRATREES"]["auc"]>overall["IMBALANCE_LOGIT"]["auc"] else "NO_ROBUST_FORWARD_INCREMENT","boundary":["chronological expanding train 40/60/80, disjoint next20 tests","no threshold/model sweep","current V3B aligned imbalance is frozen baseline","all features strict-past event history","label first subsequent structural mid move only","no winner/Target/public future"]}
 Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({"ok":True,"decision":out["decision"],"overall":overall,"perMarketVsImbalance":per},ensure_ascii=False))
if __name__=="__main__":main()
