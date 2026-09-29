from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,accuracy_score,mean_absolute_error,r2_score

def thr_for_rate(p,r): return float(np.quantile(p,1-min(.999,max(.001,float(r)))))
def cm(y,p,th):
    z=(p>=th).astype(int); both=len(np.unique(y))==2
    return {'n':int(len(y)),'positiveRate':float(np.mean(y)),'predPositiveRate':float(np.mean(z)),'accuracy':float(accuracy_score(y,z)),'balancedAccuracy':float(balanced_accuracy_score(y,z)) if both else None,'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if int(np.sum(y)) else None}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--output-dir',required=True);a=ap.parse_args()
    z=np.load(a.dataset);meta=json.load(open(a.meta,encoding='utf-8'));X=z['X'];mid=z['market_id'];ts=z['timestamp_ms'];yh=z['y_hazard'].astype(int);ys=z['y_side_up'];yw=z['y_weak'];yo=z['y_offset_ticks'];yq=z['y_log_qty']
    markets=sorted(set(map(int,mid)),key=lambda m:int(ts[mid==m].min()));n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'validation':set(markets[a1:a2]),'test':set(markets[a2:])};ix={k:np.where(np.isin(mid,list(v)))[0] for k,v in sets.items()}
    tr=ix['train'];va=ix['validation'];hz=HistGradientBoostingClassifier(max_iter=280,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=80,l2_regularization=2.,class_weight='balanced',random_state=20260831).fit(X[tr],yh[tr]); ph=hz.predict_proba(X[va])[:,1]; thh=thr_for_rate(ph,np.mean(yh[va]));pos=np.where(yh==1)[0]
    def fitc(target,seed):
        good=pos[np.isfinite(target[pos])];tri=np.asarray([i for i in good if int(mid[i]) in sets['train']],int);vai=np.asarray([i for i in good if int(mid[i]) in sets['validation']],int);m=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=2.,class_weight='balanced',random_state=seed).fit(X[tri],target[tri].astype(int));p=m.predict_proba(X[vai])[:,1];th=thr_for_rate(p,np.mean(target[vai]));return m,th,good
    side,ths,sg=fitc(ys,20260832);weak,thw,wg=fitc(yw,20260833)
    og=pos[np.isfinite(yo[pos])];otr=np.asarray([i for i in og if int(mid[i]) in sets['train']],int);off=HistGradientBoostingRegressor(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=2.,random_state=20260835).fit(X[otr],yo[otr]); qg=pos[np.isfinite(yq[pos])];qtr=np.asarray([i for i in qg if int(mid[i]) in sets['train']],int);qty=HistGradientBoostingRegressor(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=2.,random_state=20260834).fit(X[qtr],yq[qtr])
    rep={'version':'ETH_MAKER_PLACEMENT_EVENTCLOCK_V3','features':meta['features'],'markets':{k:len(v) for k,v in sets.items()}|{'total':n},'thresholds':{'hazard500ms':thh,'sideUp':ths,'weak':thw},'metrics':{}}
    for name,ms in sets.items():
        ii=ix[name];row={'hazard500ms':cm(yh[ii],hz.predict_proba(X[ii])[:,1],thh)}
        for nm,targ,model,th,good in [('sideUp',ys,side,ths,sg),('weak',yw,weak,thw,wg)]:
            jj=np.asarray([i for i in good if int(mid[i]) in ms],int);row[nm]=cm(targ[jj].astype(int),model.predict_proba(X[jj])[:,1],th)
        jj=np.asarray([i for i in og if int(mid[i]) in ms],int);predoff=off.predict(X[jj]);err=np.abs(predoff-yo[jj]);row['priceOffsetTicks']={'n':int(len(jj)),'maeTicks':float(mean_absolute_error(yo[jj],predoff)),'medianAeTicks':float(np.median(err)),'within1TickRate':float(np.mean(err<=1.0)),'within2TickRate':float(np.mean(err<=2.0)),'r2':float(r2_score(yo[jj],predoff)) if len(jj)>1 else None,'medianActual':float(np.median(yo[jj])),'medianPred':float(np.median(predoff))}; jj=np.asarray([i for i in qg if int(mid[i]) in ms],int);predlog=qty.predict(X[jj]);act=np.expm1(yq[jj]);pred=np.expm1(np.clip(predlog,0,8));row['qtyLowerBound']={'n':int(len(jj)),'maeLog':float(mean_absolute_error(yq[jj],predlog)),'r2Log':float(r2_score(yq[jj],predlog)) if len(jj)>1 else None,'maeShares':float(mean_absolute_error(act,pred)),'medianActualShares':float(np.median(act)),'medianPredShares':float(np.median(pred))};rep['metrics'][name]=row
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True);joblib.dump({'version':rep['version'],'features':meta['features'],'hazardModel':hz,'sideUpModel':side,'weakModel':weak,'priceOffsetModel':off,'qtyLowerBoundModel':qty,'thresholds':rep['thresholds'],'researchOnly':True,'decisionClock':meta.get('decisionClock')},out/'model.joblib');(out/'report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
