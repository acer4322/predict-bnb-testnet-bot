from __future__ import annotations
import glob,json,math,sqlite3,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score,balanced_accuracy_score,roc_auc_score
from sklearn.pipeline import make_pipeline

SRC=ROOT/'data/research/hftbacktest_execution_shift_v0'; OUT=ROOT/'data/research/execution_aware_repair_wake_v0'; DB=ROOT/'data/wallet_maker_book_inference.db'
FEATURES=['seconds_left','maker_abs_net','maker_paired_coverage','taker_abs_net','combined_abs_net','combined_paired_coverage','worst_case_floor','maker_taker_net_same_sign','last_maker_age_ms','last_taker_age_ms','combined_absnet_change_10s','pTaker1s','pTaker3s','same_side_maker_base','opposite_maker_base','same_side_maker_corrective','opposite_maker_corrective','maker_committed_usdt','total_remaining_usdt','direction_same_as_maker_net','direction_strength']

def f(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else np.nan
    except: return np.nan

def window_ends()->dict[int,int]:
    con=sqlite3.connect(DB)
    try:return {int(a):int(b) for a,b in con.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}
    finally:con.close()

def flatten(r:dict[str,Any], ends:dict[int,int])->dict[str,Any]:
    c=r['candidate']; p=c.get('portfolio') or {}; m=c.get('models') or {}; cap=c.get('capital') or {}; direction=c.get('direction') or {}; mid=int(r['marketId']); dm=int(c['decisionMs']); net=f(p.get('maker_net'))
    side='UP' if net>0 else 'DOWN' if net<0 else None; dside=str(direction.get('side') or '').upper();
    if side=='UP': sb=f(m.get('pMakerUpBase')); ob=f(m.get('pMakerDownBase')); sc=f(m.get('pMakerUp')); oc=f(m.get('pMakerDown'))
    elif side=='DOWN': sb=f(m.get('pMakerDownBase')); ob=f(m.get('pMakerUpBase')); sc=f(m.get('pMakerDown')); oc=f(m.get('pMakerUp'))
    else: sb=ob=sc=oc=np.nan
    z={'marketId':mid,'label':1 if r['label']=='BENEFICIAL' else 0,'teacherLabel':r['label'],'deltaUsdt':f(r.get('deltaUsdt')),'seconds_left':(ends.get(mid,dm)-dm)/1000.0}
    for k in ['maker_abs_net','maker_paired_coverage','taker_abs_net','combined_abs_net','combined_paired_coverage','worst_case_floor','maker_taker_net_same_sign','last_maker_age_ms','last_taker_age_ms','combined_absnet_change_10s']: z[k]=f(p.get(k))
    z.update({'pTaker1s':f(m.get('pTaker1s')),'pTaker3s':f(m.get('pTaker3s')),'same_side_maker_base':sb,'opposite_maker_base':ob,'same_side_maker_corrective':sc,'opposite_maker_corrective':oc,'maker_committed_usdt':f(cap.get('makerCommittedUsdt')),'total_remaining_usdt':f(cap.get('totalRemainingUsdt')),'direction_same_as_maker_net':1.0 if side and dside==side else 0.0 if side and dside in {'UP','DOWN'} else np.nan,'direction_strength':f(direction.get('strength'))})
    return z

def dataset()->pd.DataFrame:
    ends=window_ends(); by={}
    for path in glob.glob(str(SRC/'repair_wake_teacher_i*_age5000_v0.json')):
        j=json.load(open(path,encoding='utf-8'))
        for r in j.get('rows') or []:
            if r.get('forcedWakeApplied') and r.get('candidate') and r.get('label') in {'BENEFICIAL','HARMFUL','NEUTRAL'}: by[int(r['marketId'])]=flatten(r,ends)
    return pd.DataFrame(list(by.values())).sort_values('marketId').reset_index(drop=True)

def metric(y,p):
    y=np.asarray(y,dtype=int); p=np.asarray(p,dtype=float); pred=(p>=.5).astype(int); both=len(set(y.tolist()))>1
    return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'predMean':float(p.mean()),'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if both else None,'auc':float(roc_auc_score(y,p)) if both else None}
def economics(x,p):
    use=np.asarray(p)>=.5; delta=x.deltaUsdt.to_numpy(float); return {'wakeCount':int(use.sum()),'selectedDeltaUsdt':float(delta[use].sum()),'alwaysWakeDeltaUsdt':float(delta.sum()),'neverWakeDeltaUsdt':0.0,'oraclePositiveDeltaUsdt':float(delta[delta>1].sum())}

def main():
    d=dataset(); mids=d.marketId.astype(int).tolist(); cut=max(8,len(mids)-7); train=d.iloc[:cut].copy(); test=d.iloc[cut:].copy(); Xtr=train[FEATURES]
    logit=make_pipeline(SimpleImputer(strategy='median'),LogisticRegression(C=.35,class_weight='balanced',max_iter=1000,random_state=20260821)); logit.fit(Xtr,train.label.astype(int))
    ebm=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=8,max_interaction_bins=4,interactions=0,outer_bags=2,learning_rate=.03,max_rounds=350,early_stopping_rounds=35,min_samples_leaf=3,n_jobs=-2,random_state=20260821); ebm.fit(Xtr.apply(pd.to_numeric,errors='coerce'),train.label.astype(int))
    results={}
    for name,x in [('train',train),('holdout',test)]:
        lp=logit.predict_proba(x[FEATURES])[:,1]; ep=ebm.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]; results[name]={'logistic':metric(x.label,lp),'ebm':metric(x.label,ep),'logisticEconomics':economics(x,lp),'ebmEconomics':economics(x,ep)}
    OUT.mkdir(parents=True,exist_ok=True); art=OUT/'execution_aware_repair_wake_pilot_v1.joblib'; joblib.dump({'version':'EXECUTION_AWARE_REPAIR_WAKE_PILOT_V1','features':FEATURES,'logistic':logit,'ebm':ebm,'trainMarkets':train.marketId.astype(int).tolist(),'researchOnly':True},art)
    imp=list(ebm.term_importances()); names=list(ebm.term_names_); order=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:10]
    report={'version':'EXECUTION_AWARE_REPAIR_WAKE_PILOT_V1','researchOnly':True,'dataset':{'rows':len(d),'markets':len(mids),'beneficial':int(d.label.sum()),'harmfulOrNeutral':int((1-d.label).sum()),'teacherDeltaSumUsdt':float(d.deltaUsdt.sum())},'split':{'trainMarkets':len(train),'holdoutMarkets':len(test),'trainIds':train.marketId.astype(int).tolist(),'holdoutIds':test.marketId.astype(int).tolist()},'features':FEATURES,'results':results,'topEbmTerms':[{'term':str(names[i]),'importance':float(imp[i])} for i in order],'artifact':str(art),'promotionBoundary':'Pilot only. Must beat always-wake and never-wake on chronological holdout economics and remain stable on a larger unseen cohort before any closed-loop integration.'}
    (OUT/'execution_aware_repair_wake_pilot_v1_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
