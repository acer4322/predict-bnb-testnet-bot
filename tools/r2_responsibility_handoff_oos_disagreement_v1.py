from __future__ import annotations
import json,joblib,sys,math
from pathlib import Path
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import ExtraTreesClassifier
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import r2_dynamic_lifecycle_supervisor_automl_v1 as s
from tools import hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter as b
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0';OLD=BASE/'sequential_arbitration_option_v1.joblib';ART=BASE/'r2_responsibility_handoff_expert_v1_train125.joblib';REP=BASE/'r2_responsibility_handoff_oos_disagreement_v1.json'
def fit(X,y):
 m=make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=500,min_samples_leaf=4,max_features=.8,n_jobs=-1,random_state=20260823,class_weight='balanced'));m.fit(X,y);return m
def main():
 rows,mids,sp=s.load();mids=sorted(mids);tr=set(mids[:125]);te=set(mids[125:]);rr,X,y=s.xy(rows,tr);mask=np.array([a in ['KEEP_EXECUTING','REPLACE_ROUTE'] for a in y]);yt=np.array([a=='REPLACE_ROUTE' for a in np.array(y)[mask]],int);expert=fit(X[mask],yt);old=joblib.load(OLD);hy={'currentOnly':{'features':old['currentOnly']['features'],'models':dict(old['currentOnly']['models'])},'thresholds':dict(old['thresholds'])};hy['currentOnly']['models']['replace']=expert;joblib.dump(hy,ART)
 rr2,X2,y2=s.xy(rows,te);act=np.array([a in ['KEEP_EXECUTING','REPLACE_ROUTE'] for a in y2]);idx=np.where(act)[0];op=old['currentOnly']['models']['replace'].predict_proba(X2[idx])[:,1];npred=expert.predict_proba(X2[idx])[:,1];cand=[]
 for j,o,n in zip(idx,op,npred):
  if (o>=.5)!=(n>=.5):cand.append({'marketId':int(rr2[j]['marketId']),'atMs':int(rr2[j]['candidateAtMs']),'teacher':y2[j],'oldP':float(o),'newP':float(n),'gap':abs(float(o-n))})
 cand.sort(key=lambda z:z['gap'],reverse=True);markets=[]
 for z in cand:
  if z['marketId'] not in markets:markets.append(z['marketId'])
  if len(markets)>=6:break
 out={'disagreements':cand,'selectedMarkets':markets,'rows':[]}
 oldpath=b.MODEL_PATH
 for name,path in [('BASE',OLD),('TRAIN125_EXPERT',ART)]:
  b.MODEL_PATH=path
  for mid in markets:
   try:
    r=b.run_market(mid);a=r['actualExecution'];l=r['lifecycle'];out['rows'].append({'policy':name,'marketId':mid,'pnl':a['realizedPnl'],'absNet':a['combinedFinalAbsNet'],'tracking':a['finalAbsTrackingError'],'paired':a['finalPortfolio']['combined_paired_coverage'],'takerFilled':a['takerFilledShares'],'replace':l['actionCounts']['REPLACE_ROUTE'],'unresolved':l['unresolvedTakerReturns']})
   except Exception as e:out['rows'].append({'policy':name,'marketId':mid,'error':repr(e)})
 b.MODEL_PATH=oldpath
 for name in ['BASE','TRAIN125_EXPERT']:
  x=[r for r in out['rows'] if r['policy']==name and 'pnl' in r];out[name]={'markets':len(x),'pnl':sum(float(r['pnl']) for r in x),'meanAbsNet':sum(r['absNet'] for r in x)/len(x) if x else None,'meanTracking':sum(r['tracking'] for r in x)/len(x) if x else None,'replace':sum(r['replace'] for r in x),'unresolved':sum(r['unresolved'] for r in x)}
 REP.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'selectedMarkets':markets,'topDisagreements':cand[:10],'BASE':out['BASE'],'TRAIN125_EXPERT':out['TRAIN125_EXPERT']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
