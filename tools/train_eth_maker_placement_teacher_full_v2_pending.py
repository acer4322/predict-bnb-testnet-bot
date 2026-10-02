from __future__ import annotations
import json,math,argparse
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,accuracy_score,mean_absolute_error,r2_score

def threshold_match_rate(p,rate):
    if len(p)==0:return .5
    k=max(0,min(len(p)-1,int(round((1-rate)*(len(p)-1)))))
    return float(np.sort(p)[k])

def cls_metrics(y,p,thr):
    pred=(p>=thr).astype(int); both=len(np.unique(y))>1
    return {'n':int(len(y)),'positiveRate':float(np.mean(y)),'predPositiveRate':float(np.mean(pred)),'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'rocAuc':float(roc_auc_score(y,p)) if both else None}

def reg_metrics(y,p,raw=False):
    d={'n':int(len(y)),'mae':float(mean_absolute_error(y,p)),'r2':float(r2_score(y,p)) if len(y)>1 else None,'medianActual':float(np.median(y)),'medianPred':float(np.median(p))}
    if raw: d['maeRawQty']=float(mean_absolute_error(np.expm1(y),np.expm1(np.clip(p,0,8))))
    return d

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--dataset',required=True); ap.add_argument('--meta',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    z=np.load(a.dataset); meta=json.loads(Path(a.meta).read_text()); X=z['X']; mid=z['market_id']; ts=z['timestamp_ms']; blocked=set(map(int,meta['blockedFromTrainingAndOfflineSplits']))
    market_time={int(m):int(np.min(ts[mid==m])) for m in np.unique(mid)}; markets=[m for m,_ in sorted(market_time.items(),key=lambda kv:(kv[1],kv[0])) if m not in blocked]
    n=len(markets); a1=int(.70*n); a2=int(.85*n); split={'train':set(markets[:a1]),'validation':set(markets[a1:a2]),'test':set(markets[a2:])}
    idx={k:np.asarray([i for i,m in enumerate(mid) if int(m) in v],dtype=int) for k,v in split.items()}
    yh=z['y_hazard']; ys=z['y_side_up']; yw=z['y_weak']; yo=z['y_offset_ticks']; yq=z['y_log_qty']
    tr=idx['train']; ytr=yh[tr].astype(int); pos=max(1,int(ytr.sum())); neg=max(1,len(ytr)-pos); sw=np.where(ytr==1,len(ytr)/(2*pos),len(ytr)/(2*neg))
    hz=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=40,l2_regularization=2.,random_state=20260831).fit(X[tr],ytr,sample_weight=sw)
    pv=hz.predict_proba(X[idx['validation']])[:,1]; hthr=threshold_match_rate(pv,float(np.mean(yh[idx['validation']])))
    action=np.where(yh==1)[0]
    def fit_cls_target(y,seed):
        valid=action[np.isfinite(y[action])]; tri=np.asarray([i for i in valid if int(mid[i]) in split['train']],int); vai=np.asarray([i for i in valid if int(mid[i]) in split['validation']],int)
        yy=y[tri].astype(int); p=max(1,int(yy.sum())); q=max(1,len(yy)-p); w=np.where(yy==1,len(yy)/(2*p),len(yy)/(2*q)); model=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=25,l2_regularization=2.,random_state=seed).fit(X[tri],yy,sample_weight=w); prob=model.predict_proba(X[vai])[:,1]; thr=threshold_match_rate(prob,float(np.mean(y[vai]))); return model,thr,valid
    side,sthr,svalid=fit_cls_target(ys,20260832); weak,wthr,wvalid=fit_cls_target(yw,20260833)
    def fit_reg(y,seed,clip=None):
        valid=action[np.isfinite(y[action])]; tri=np.asarray([i for i in valid if int(mid[i]) in split['train']],int); yy=y[tri].astype(float); yyfit=np.clip(yy,*clip) if clip else yy; model=HistGradientBoostingRegressor(max_iter=240,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=20,l2_regularization=2.,random_state=seed).fit(X[tri],yyfit); return model,valid
    off,ovalid=fit_reg(yo,20260834,(-10,10)); qty,qvalid=fit_reg(yq,20260835,None)
    report={'version':'ETH_MAKER_PLACEMENT_TEACHER_FULL_V2_PENDING','features':meta['features'],'markets':{'usable':n,'train':len(split['train']),'validation':len(split['validation']),'test':len(split['test']),'blocked':sorted(blocked)},'thresholds':{'placementHazard':hthr,'sideUp':sthr,'weak':wthr},'metrics':{}}
    for sn in ('train','validation','test'):
        ii=idx[sn]; report['metrics'].setdefault(sn,{})['hazard']=cls_metrics(yh[ii].astype(int),hz.predict_proba(X[ii])[:,1],hthr)
        for name,y,model,thr,valid in [('sideUp',ys,side,sthr,svalid),('weak',yw,weak,wthr,wvalid)]:
            jj=np.asarray([i for i in valid if int(mid[i]) in split[sn]],int); report['metrics'][sn][name]=cls_metrics(y[jj].astype(int),model.predict_proba(X[jj])[:,1],thr)
        for name,y,model,valid,raw in [('offsetTicks',yo,off,ovalid,False),('qtyLogLowerBound',yq,qty,qvalid,True)]:
            jj=np.asarray([i for i in valid if int(mid[i]) in split[sn]],int); pred=model.predict(X[jj]); report['metrics'][sn][name]=reg_metrics(y[jj],pred,raw)
    bundle={'version':report['version'],'features':meta['features'],'thresholds':report['thresholds'],'hazardModel':hz,'sideModel':side,'weakModel':weak,'offsetModel':off,'qtyModel':qty,'trainingBoundary':'no18 reconstructed placement; blocked dev20+fresh6; seconds_left>180 only'}
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); joblib.dump(bundle,out.parent/'model.joblib'); (out.parent/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
