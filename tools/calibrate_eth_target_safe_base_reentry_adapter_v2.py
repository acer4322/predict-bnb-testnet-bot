from __future__ import annotations
import argparse,json,joblib
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

def best_th(y,p):
 qs=np.unique(np.quantile(p,np.linspace(.02,.98,65)));best=(.5,-1)
 for th in qs:
  b=balanced_accuracy_score(y,(p>=th).astype(int)) if len(np.unique(y))>1 else 0
  if b>best[1]:best=(float(th),float(b))
 return best[0]
def met(y,p,th):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=th).astype(int)
 return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'predPositiveRate':float(z.mean())}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);ap.add_argument('--adapter-out',required=True);a=ap.parse_args()
 d=np.load(a.dataset);meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));ck=joblib.load(a.model);X=d['X'];yh=d['y_hazard'].astype(int);ys=d['y_side_up'];mid=d['market_id'];ts=d['timestamp_ms'];F=meta['features'];ix={k:i for i,k in enumerate(F)}
 markets=sorted(set(map(int,mid)),key=lambda m:int(ts[mid==m].min()));n=len(markets);a1=int(.70*n);a2=int(.85*n);val=set(markets[a1:a2]);test=set(markets[a2:]);gross=np.maximum(X[:,ix['gross_shares']],1e-9);ratio=X[:,ix['abs_net']]/gross;safe=(X[:,ix['floor']]>=0)&(ratio<=.05)&(X[:,ix['seconds_left']]>180)
 mv=safe&np.isin(mid,list(val));mt=safe&np.isin(mid,list(test));phv=ck['hazardModel'].predict_proba(X[mv])[:,1];pht=ck['hazardModel'].predict_proba(X[mt])[:,1];hth=best_th(yh[mv],phv)
 sv=mv&(yh==1)&np.isfinite(ys);st=mt&(yh==1)&np.isfinite(ys);psv=ck['sideUpModel'].predict_proba(X[sv])[:,1];pst=ck['sideUpModel'].predict_proba(X[st])[:,1];sth=best_th(ys[sv].astype(int),psv)
 rep={'version':'ETH_TARGET_SAFE_BASE_REENTRY_ADAPTER_V2','frozenBase':ck.get('version'),'scope':{'floorMin':0.0,'absNetRatioMax':0.05,'secondsLeftMin':180},'thresholds':{'hazard500ms':hth,'sideUp':sth},'validation':{'hazard':met(yh[mv],phv,hth),'side':met(ys[sv].astype(int),psv,sth)},'test':{'hazard':met(yh[mt],pht,hth),'side':met(ys[st].astype(int),pst,sth)}}
 t=rep['test'];g={'hazardAuc':t['hazard']['auc']>=.70,'hazardBalancedAccuracy':t['hazard']['balancedAccuracy']>=.60,'sideAuc':t['side']['auc']>=.65,'sideBalancedAccuracy':t['side']['balancedAccuracy']>=.58,'testPositives':t['hazard']['positives']>=30};rep['passChecks']=g;rep['representationPass']=all(g.values());Path(a.output).write_text(json.dumps(rep,indent=2),encoding='utf-8');joblib.dump({'version':rep['version'],'baseModelPath':str(a.model),'hazardThreshold':hth,'sideThreshold':sth,'scope':rep['scope'],'representationPass':rep['representationPass']},a.adapter_out);print(json.dumps(rep,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
