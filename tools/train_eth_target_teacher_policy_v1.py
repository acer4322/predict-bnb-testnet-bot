from __future__ import annotations
import argparse, json, math
from pathlib import Path
import joblib, numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import accuracy_score, balanced_accuracy_score, classification_report, confusion_matrix, log_loss, roc_auc_score, mean_absolute_error, r2_score

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]

def wbalanced(y):
    vals,c=np.unique(y,return_counts=True); d={v:len(y)/(len(vals)*n) for v,n in zip(vals,c)}
    return np.asarray([d[v] for v in y],dtype=float)

def clf_metrics(y,p,classes):
    pr=np.argmax(p,axis=1); out={'n':int(len(y)),'accuracy':float(accuracy_score(y,pr)),'balancedAccuracy':float(balanced_accuracy_score(y,pr)),'logLoss':float(log_loss(y,p,labels=classes)),'confusion':confusion_matrix(y,pr,labels=classes).tolist(),'report':classification_report(y,pr,labels=classes,output_dict=True,zero_division=0)}
    return out

def bin_metrics(y,p,threshold=.5):
    pr=(p>=threshold).astype(int); both=len(np.unique(y))==2
    return {'n':int(len(y)),'positiveRate':float(np.mean(y)),'predPositiveRate':float(np.mean(pr)),'accuracy':float(accuracy_score(y,pr)),'balancedAccuracy':float(balanced_accuracy_score(y,pr)),'rocAuc':float(roc_auc_score(y,p)) if both else None}

def tune_rate_threshold(p, target_rate):
    if not len(p): return .5
    q=max(0.0,min(1.0,1.0-target_rate)); return float(np.quantile(p,q))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--dataset',required=True); ap.add_argument('--meta',required=True); ap.add_argument('--output-dir',required=True); a=ap.parse_args()
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    z=np.load(a.dataset); meta=json.load(open(a.meta,encoding='utf-8')); X=z['X'].astype(np.float64); yr=z['y_role'].astype(int); yw=z['y_weak']; ys=z['y_side_up']; yq=z['y_log_qty']; mid=z['market_id'].astype(int)
    dev=set(map(int,meta.get('dev20ExcludedFromFormalTrainingByTrainer',[])))
    uniq=sorted(set(mid.tolist())-dev)
    n=len(uniq); a1=int(.70*n); a2=int(.85*n); train=set(uniq[:a1]); val=set(uniq[a1:a2]); test=set(uniq[a2:])
    ix={'train':np.flatnonzero(np.isin(mid,list(train))),'validation':np.flatnonzero(np.isin(mid,list(val))),'test':np.flatnonzero(np.isin(mid,list(test)))}
    # Models are factorized to be rollout-friendly.
    y_action=(yr>0).astype(int)
    action=HistGradientBoostingClassifier(max_iter=220,learning_rate=.06,max_leaf_nodes=31,min_samples_leaf=120,l2_regularization=2.0,random_state=20260831).fit(X[ix['train']],y_action[ix['train']],sample_weight=wbalanced(y_action[ix['train']]))
    act_train=np.flatnonzero((yr>0)&np.isin(mid,list(train))); act_val=np.flatnonzero((yr>0)&np.isin(mid,list(val))); act_test=np.flatnonzero((yr>0)&np.isin(mid,list(test)))
    y_taker=(yr==2).astype(int)
    role=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=23,min_samples_leaf=80,l2_regularization=2.0,random_state=20260832).fit(X[act_train],y_taker[act_train],sample_weight=wbalanced(y_taker[act_train]))
    weak_train=np.flatnonzero(np.isfinite(yw)&np.isin(mid,list(train))); weak_val=np.flatnonzero(np.isfinite(yw)&np.isin(mid,list(val))); weak_test=np.flatnonzero(np.isfinite(yw)&np.isin(mid,list(test)))
    weak=HistGradientBoostingClassifier(max_iter=200,learning_rate=.055,max_leaf_nodes=27,min_samples_leaf=80,l2_regularization=2.0,random_state=20260833).fit(X[weak_train],yw[weak_train].astype(int),sample_weight=wbalanced(yw[weak_train].astype(int)))
    side_train=np.flatnonzero(np.isfinite(ys)&np.isin(mid,list(train))); side_val=np.flatnonzero(np.isfinite(ys)&np.isin(mid,list(val))); side_test=np.flatnonzero(np.isfinite(ys)&np.isin(mid,list(test)))
    side=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=80,l2_regularization=2.0,random_state=20260834).fit(X[side_train],ys[side_train].astype(int),sample_weight=wbalanced(ys[side_train].astype(int)))
    maker_train=np.flatnonzero((yr==1)&np.isfinite(yq)&np.isin(mid,list(train))); taker_train=np.flatnonzero((yr==2)&np.isfinite(yq)&np.isin(mid,list(train)))
    qty_m=HistGradientBoostingRegressor(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=60,l2_regularization=2.0,random_state=20260835).fit(X[maker_train],yq[maker_train])
    qty_t=HistGradientBoostingRegressor(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=2.0,random_state=20260836).fit(X[taker_train],yq[taker_train])
    pv=action.predict_proba(X[ix['validation']])[:,1]; action_rate=float(np.mean(y_action[ix['validation']])); ath=tune_rate_threshold(pv,action_rate)
    pr_v=role.predict_proba(X[act_val])[:,1]; taker_rate=float(np.mean(y_taker[act_val])); rth=tune_rate_threshold(pr_v,taker_rate)
    pw_v=weak.predict_proba(X[weak_val])[:,1]; weak_rate=float(np.mean(yw[weak_val])); wth=tune_rate_threshold(pw_v,weak_rate)
    ps_v=side.predict_proba(X[side_val])[:,1]; side_rate=float(np.mean(ys[side_val])); sth=tune_rate_threshold(ps_v,side_rate)
    report={'version':'ETH_TARGET_TEACHER_POLICY_V1','features':meta['features'],'markets':{'totalNonDev':n,'train':len(train),'validation':len(val),'test':len(test),'dev20Excluded':sorted(dev)},'thresholds':{'action':ath,'takerGivenAction':rth,'weakGivenAction':wth,'sideUpGivenAction':sth},'ratesValidation':{'action':action_rate,'takerGivenAction':taker_rate,'weakGivenAction':weak_rate,'sideUpGivenAction':side_rate},'metrics':{}}
    for nm,ii in ix.items():
        pa=action.predict_proba(X[ii])[:,1]; report['metrics'].setdefault(nm,{})['action']=bin_metrics(y_action[ii],pa,ath)
    for nm,ii in [('train',act_train),('validation',act_val),('test',act_test)]: report['metrics'].setdefault(nm,{})['takerGivenAction']=bin_metrics(y_taker[ii],role.predict_proba(X[ii])[:,1],rth)
    for nm,ii in [('train',weak_train),('validation',weak_val),('test',weak_test)]: report['metrics'].setdefault(nm,{})['weakGivenAction']=bin_metrics(yw[ii].astype(int),weak.predict_proba(X[ii])[:,1],wth)
    for nm,ii in [('train',side_train),('validation',side_val),('test',side_test)]: report['metrics'].setdefault(nm,{})['sideUpGivenAction']=bin_metrics(ys[ii].astype(int),side.predict_proba(X[ii])[:,1],sth)
    for role_name,rcode,model in [('maker',1,qty_m),('taker',2,qty_t)]:
        report['metrics'][f'qty_{role_name}']={}
        for nm,ms in [('train',train),('validation',val),('test',test)]:
            ii=np.flatnonzero((yr==rcode)&np.isfinite(yq)&np.isin(mid,list(ms))); pred=model.predict(X[ii]); actual=yq[ii]
            report['metrics'][f'qty_{role_name}'][nm]={'n':int(len(ii)),'maeLog':float(mean_absolute_error(actual,pred)),'r2Log':float(r2_score(actual,pred)) if len(ii)>1 else None,'maeShares':float(mean_absolute_error(np.expm1(actual),np.expm1(np.clip(pred,0,10)))),'medianActualShares':float(np.median(np.expm1(actual))),'medianPredShares':float(np.median(np.expm1(np.clip(pred,0,10))))}
    bundle={'version':'ETH_TARGET_TEACHER_POLICY_V1','features':meta['features'],'models':{'action':action,'taker':role,'weak':weak,'side_up':side,'qty_maker':qty_m,'qty_taker':qty_t},'thresholds':report['thresholds'],'trainingMarkets':sorted(train),'validationMarkets':sorted(val),'testMarkets':sorted(test),'dev20Excluded':sorted(dev),'quantityClip':{'maker':[1.0,20.0],'taker':[1.0,80.0]},'boundary':'strict-past Target ETH teacher; rollout must use OUR inventory/book only; no Target action time/side/winner as features'}
    joblib.dump(bundle,out/'model.joblib',compress=3)
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
