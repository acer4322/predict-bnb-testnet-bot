from __future__ import annotations
import argparse, json, math, os
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, accuracy_score, mean_absolute_error, r2_score


def qthreshold(p, rate):
    if len(p)==0: return .5
    rate=float(min(.999,max(.001,rate)))
    return float(np.quantile(p,1-rate))

def cls_metrics(y,p,thr):
    pred=(p>=thr).astype(np.int8); both=len(np.unique(y))==2
    return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'predPositiveRate':float(pred.mean()) if len(y) else None,
            'accuracy':float(accuracy_score(y,pred)) if len(y) else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if both else None,
            'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if int(y.sum())>0 else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--dataset',required=True); ap.add_argument('--meta',required=True); ap.add_argument('--output-dir',required=True); a=ap.parse_args()
    z=np.load(a.dataset); meta=json.load(open(a.meta,encoding='utf-8')); X=z['X']; mid=z['market_id']; ts=z['timestamp_ms']; yh=z['y_hazard'].astype(int); ys=z['y_side_up']; yw=z['y_weak']; yo=z['y_offset_ticks']; yq=z['y_log_qty']
    markets=sorted(set(map(int,mid)),key=lambda m:int(ts[mid==m].min())); n=len(markets); i1=int(.70*n); i2=int(.85*n); sets={'train':set(markets[:i1]),'validation':set(markets[i1:i2]),'test':set(markets[i2:])}
    ix={k:np.asarray([i for i,m in enumerate(mid) if int(m) in v],dtype=int) for k,v in sets.items()}
    tr=ix['train']; va=ix['validation']
    hz=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=60,l2_regularization=2.,random_state=20260831,class_weight='balanced').fit(X[tr],yh[tr])
    pv=hz.predict_proba(X[va])[:,1]; thr_h=qthreshold(pv,float(yh[va].mean()))
    pos=np.where(yh==1)[0]
    def fit_cls(target,seed):
        good=pos[np.isfinite(target[pos])]; tri=np.asarray([i for i in good if int(mid[i]) in sets['train']],int); vai=np.asarray([i for i in good if int(mid[i]) in sets['validation']],int)
        m=HistGradientBoostingClassifier(max_iter=240,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=30,l2_regularization=2.,random_state=seed,class_weight='balanced').fit(X[tri],target[tri].astype(int))
        p=m.predict_proba(X[vai])[:,1]; th=qthreshold(p,float(np.mean(target[vai])))
        return m,th,good
    side,thr_side,side_good=fit_cls(ys,20260832); weak,thr_weak,weak_good=fit_cls(yw,20260833)
    def fit_reg(target,seed,clip=None):
        good=pos[np.isfinite(target[pos])]; tri=np.asarray([i for i in good if int(mid[i]) in sets['train']],int)
        m=HistGradientBoostingRegressor(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=30,l2_regularization=2.,random_state=seed).fit(X[tri],target[tri])
        return m,good
    off,off_good=fit_reg(yo,20260834); qty,qty_good=fit_reg(yq,20260835)
    report={'version':'ETH_MAKER_PLACEMENT_TEACHER_PILOT300_V1','features':meta['features'],'markets':{'total':n,'train':len(sets['train']),'validation':len(sets['validation']),'test':len(sets['test'])},'thresholds':{'placementHazard':thr_h,'sideUpGivenPlacement':thr_side,'weakGivenPlacement':thr_weak},'metrics':{}}
    for name,ms in sets.items():
        ii=ix[name]; pp=hz.predict_proba(X[ii])[:,1]; row={'placementHazard':cls_metrics(yh[ii],pp,thr_h)}
        for nm,targ,model,thr,good in [('sideUpGivenPlacement',ys,side,thr_side,side_good),('weakGivenPlacement',yw,weak,thr_weak,weak_good)]:
            jj=np.asarray([i for i in good if int(mid[i]) in ms],int); prob=model.predict_proba(X[jj])[:,1]; row[nm]=cls_metrics(targ[jj].astype(int),prob,thr)
        jj=np.asarray([i for i in off_good if int(mid[i]) in ms],int); pred=off.predict(X[jj]); err=np.abs(pred-yo[jj]); row['offsetTicks']={'n':int(len(jj)),'maeTicks':float(mean_absolute_error(yo[jj],pred)),'medianAeTicks':float(np.median(err)),'within1TickRate':float(np.mean(err<=1.0)),'within2TickRate':float(np.mean(err<=2.0)),'r2':float(r2_score(yo[jj],pred)) if len(jj)>1 else None,'medianActual':float(np.median(yo[jj])),'medianPred':float(np.median(pred))}
        jj=np.asarray([i for i in qty_good if int(mid[i]) in ms],int); predlog=qty.predict(X[jj]); actual=np.expm1(yq[jj]); pred=np.expm1(np.clip(predlog,0,8)); row['quantityLowerBound']={'n':int(len(jj)),'maeLog':float(mean_absolute_error(yq[jj],predlog)),'r2Log':float(r2_score(yq[jj],predlog)) if len(jj)>1 else None,'maeShares':float(mean_absolute_error(actual,pred)),'medianActualShares':float(np.median(actual)),'medianPredShares':float(np.median(pred))}
        report['metrics'][name]=row
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    bundle={'version':report['version'],'features':meta['features'],'placementHazardModel':hz,'sideUpModel':side,'weakModel':weak,'offsetTicksModel':off,'quantityLowerBoundModel':qty,'thresholds':report['thresholds'],'boundary':meta.get('strictPast'),'researchOnly':True}
    joblib.dump(bundle,out/'model.joblib'); (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
