from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score,recall_score
LOCAL=['secondsLeft','orderAgeS','remainingRatio','partialFillRatio','price','sideIsUp','sameSideLiveCount','oppSideLiveCount','quoteOffsetTicks','spreadTicks','predictSourceAgeMs','predictReceiptAgeMs','cancelPending']
RECENT=LOCAL+['sameSideFillShares5s','sameSideFillShares15s','timeSinceSameSideFillS','lastMakerAgeMs','makerFills5s','makerShares5s']
FULL=RECENT+['combinedAbsNet','combinedCoverage','worstCaseFloor','bestCasePnl','makerAbsNet']
GROUPS={'ORDER_LOCAL':LOCAL,'ORDER_PLUS_RECENT':RECENT,'FULL':FULL}

def metrics(y,p):
    yh=(p>=.5).astype(int); out={'n':len(y),'pos':int(y.sum()),'balancedAccuracy':float(balanced_accuracy_score(y,yh)),'positiveRecall':float(recall_score(y,yh,pos_label=1,zero_division=0)),'negativeRecall':float(recall_score(y,yh,pos_label=0,zero_division=0)),'averagePrecision':float(average_precision_score(y,p))}
    out['auc']=float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None; return out

def bootstrap_by_market(df,ycol,pcol,n=5000,seed=9917):
    rng=np.random.default_rng(seed); mids=np.array(sorted(df.marketId.unique())); vals=[]
    for _ in range(n):
        draw=rng.choice(mids,size=len(mids),replace=True); ix=np.concatenate([df.index[df.marketId==m].to_numpy() for m in draw]); y=df.loc[ix,ycol].to_numpy(int); p=df.loc[ix,pcol].to_numpy(float)
        if len(np.unique(y))<2: continue
        vals.append((roc_auc_score(y,p),balanced_accuracy_score(y,(p>=.5).astype(int))))
    a=np.array(vals); return {'nBootstrap':len(vals),'aucMedian':float(np.median(a[:,0])),'aucP025':float(np.quantile(a[:,0],.025)),'aucP975':float(np.quantile(a[:,0],.975)),'baMedian':float(np.median(a[:,1])),'baP025':float(np.quantile(a[:,1],.025)),'baP975':float(np.quantile(a[:,1],.975))}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); raw=json.load(open(a.input,encoding='utf-8')); df=pd.DataFrame(raw['rows']); results={}; pred_rows=[]
    for h in (5,15,30):
        ycol=f'labelFill{h}s'; results[str(h)]={}
        for g,feats in GROUPS.items():
            for modelname in ('LOGIT','EXTRATREES'):
                oof=[]
                for mid in sorted(df.marketId.unique()):
                    tr=df[df.marketId!=mid]; te=df[df.marketId==mid]; y=tr[ycol].to_numpy(int)
                    if len(np.unique(y))<2: continue
                    if modelname=='LOGIT': model=Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('sc',StandardScaler()),('m',LogisticRegression(max_iter=2000,class_weight='balanced',C=1.0,random_state=7000+h))])
                    else: model=Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',ExtraTreesClassifier(n_estimators=300,max_depth=6,min_samples_leaf=5,class_weight='balanced',random_state=8000+h,n_jobs=1))])
                    model.fit(tr[feats],y); p=model.predict_proba(te[feats])[:,1]
                    for idx,pp in zip(te.index,p): oof.append((idx,float(pp)))
                od=pd.DataFrame(oof,columns=['idx','p']).set_index('idx').sort_index(); sub=df.loc[od.index].copy(); sub['p']=od['p']; m=metrics(sub[ycol].to_numpy(int),sub.p.to_numpy(float)); tmp=sub[['marketId',ycol,'p']].rename(columns={'p':'prob'}); tmp['pred']=tmp.prob>=.5; tmp['horizon']=h; tmp['group']=g; tmp['model']=modelname; pred_rows.extend(tmp.to_dict('records'))
                bootdf=sub[['marketId',ycol]].copy(); bootdf['prob']=sub.p; m['marketBootstrap']=bootstrap_by_market(bootdf,ycol,'prob',5000,seed=9000+h+(0 if modelname=='LOGIT' else 100)+(list(GROUPS).index(g)*10)); results[str(h)][f'{g}_{modelname}']=m
    out={'version':'R4_LIVE_CARRIER_VIABILITY_ECHTGELD11_EVAL_V1','researchOnly':True,'runtimeAuthority':False,'source':a.input,'featureGroups':GROUPS,'results':results,'predictions':pred_rows}; Path(a.output).write_text(json.dumps(out,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps(results))
if __name__=='__main__': main()
