from __future__ import annotations
import argparse,json,sqlite3,math
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score
from scipy.stats import spearmanr

FOLDS=[(0,40,40,60),(0,60,60,80),(0,80,80,100)]

def X(rows,names):
 return np.array([[float((r.get("features") or {}).get(k)) if (r.get("features") or {}).get(k) is not None else np.nan for k in names] for r in rows],float)
def yb(rows): return np.array([1 if bool((r.get("label") or {}).get("towardExpand")) else 0 for r in rows],int)
def maxdd(vals):
 s=0.;peak=0.;dd=0.
 for v in vals:s+=v;peak=max(peak,s);dd=max(dd,peak-s)
 return dd
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--belief",required=True);ap.add_argument("--fork",required=True);ap.add_argument("--winner-db",default="data/target_wallet_official_v1.db");ap.add_argument("--output",required=True);a=ap.parse_args()
 bd=json.loads(Path(a.belief).read_text(encoding="utf-8")); br=sorted(bd["rows"],key=lambda r:(int(r["t"]),int(r["marketId"])))
 fd=json.loads(Path(a.fork).read_text(encoding="utf-8")); fm={int(r["marketId"]):r for r in fd["rows"]}
 feats=sorted(br[0]["features"].keys()); pred={}
 for a0,a1,b0,b1 in FOLDS:
  tr,te=br[a0:a1],br[b0:b1]; m=make_pipeline(SimpleImputer(strategy="median"),ExtraTreesClassifier(n_estimators=300,min_samples_leaf=4,max_features="sqrt",random_state=20260907,n_jobs=1,class_weight="balanced"));m.fit(X(tr,feats),yb(tr)); pp=m.predict_proba(X(te,feats))[:,1]
  for r,p in zip(te,pp): pred[int(r["marketId"])]=float(p)
 mids=sorted(pred,key=lambda m:next(int(r["t"]) for r in br if int(r["marketId"])==m))
 q=','.join('?'*len(mids)); wins={}
 with sqlite3.connect(a.winner_db) as db:
  for mid,w in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',mids): wins[int(mid)]=str(w)
 rows=[]
 for mid in mids:
  if mid not in fm or mid not in wins: continue
  f=fm[mid]; w=wins[mid]; sidekey='upQty' if w=='UP' else 'downQty'
  tm=f['terminalMetrics']; rp=float(tm['NEXT_REPAIR'][sidekey])-float(tm['NEXT_REPAIR']['buyNotional']); ep=float(tm['NEXT_REEXPAND'][sidekey])-float(tm['NEXT_REEXPAND']['buyNotional']); npnl=float(tm['NATIVE'][sidekey])-float(tm['NATIVE']['buyNotional']); d=ep-rp; p=pred[mid]
  choose='NEXT_REEXPAND' if p>=.5 else 'NEXT_REPAIR'; cp=ep if choose=='NEXT_REEXPAND' else rp
  rows.append({'marketId':mid,'pTowardExpand':p,'winnerPostHocOnly':w,'repairPnl':rp,'reexpandPnl':ep,'nativePnl':npnl,'dPnlReexpandMinusRepair':d,'choose':choose,'candidatePnl':cp,'deltaVsNative':cp-npnl})
 ds=np.array([r['dPnlReexpandMinusRepair'] for r in rows]); ps=np.array([r['pTowardExpand'] for r in rows]); non=np.abs(ds)>1e-12
 sign=(ds[non]>0).astype(int)
 auc=float(roc_auc_score(sign,ps[non])) if len(set(sign.tolist()))>1 else None; rho,pv=spearmanr(ps,ds)
 affected=[r for r in rows if r['choose'] != ('NEXT_REPAIR' if str(fm[r['marketId']]['stateSpec'].get('nativeClass'))=='REPAIR' else 'NEXT_REEXPAND')]
 imp=sum(r['deltaVsNative']>1e-9 for r in affected); harm=sum(r['deltaVsNative']<-1e-9 for r in affected); tie=len(affected)-imp-harm
 bp=[r['nativePnl'] for r in rows];cp=[r['candidatePnl'] for r in rows]
 out={'version':'MANAGEMENT_MAINLINE_ROLE_SWITCH_EVENT_BELIEF_VALUE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'summary':{'oosMarkets':len(rows),'nonTieActionValueMarkets':int(non.sum()),'beliefAucForReexpandBetter':auc,'spearmanBeliefVsDeltaPnl':float(rho),'spearmanP':float(pv),'affectedMarkets':len(affected),'affectedImproved':imp,'affectedWorsened':harm,'affectedTied':tie,'affectedImprovementRate':float(imp/len(affected)) if affected else None,'baselineTotalPnl':float(sum(bp)),'candidateTotalPnl':float(sum(cp)),'deltaTotalPnl':float(sum(cp)-sum(bp)),'baselineWinRate':float(np.mean(np.array(bp)>0)),'candidateWinRate':float(np.mean(np.array(cp)>0)),'baselineMaxDD':float(maxdd(bp)),'candidateMaxDD':float(maxdd(cp))},'boundary':['belief prediction strictly OOS expanding chronology','belief trained only on strict-past prediction-market event history','winner used only offline to score branch PnL','no Target/winner in belief feature','0.5 decision threshold fixed a priori; no sweep','single first-eligible intervention only; not runtime authority']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False))
if __name__=='__main__':main()
