from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

EPS=1e-9


def finite(v, default=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except Exception: return default


def role_for(seam:dict[str,Any], side:str)->str:
    u=finite(seam.get('pre_up_shares')); d=finite(seam.get('pre_down_shares'))
    if abs(u-d)<=1e-9: return 'BALANCED'
    weak='UP' if u<d else 'DOWN'; return 'REPAIR' if side==weak else 'EXPAND'


def side_book(seam:dict[str,Any], side:str):
    bid=finite(seam.get('receipt_strict_best_bid')); ask=finite(seam.get('receipt_strict_best_ask'))
    bd=finite(seam.get('receipt_strict_bid_depth_total')); ad=finite(seam.get('receipt_strict_ask_depth_total')); oc=finite(seam.get('receipt_strict_order_count'))
    if side=='UP': return bid,ask,bd,ad,oc
    return 1-ask,1-bid,ad,bd,oc


def mat_book(row:dict[str,Any], side:str):
    q=row.get('bookAtMaterialization') or row.get('eventBook') or {}
    s=q.get(side) or {}
    return finite(s.get('bid'),np.nan),finite(s.get('ask'),np.nan)


def build(seams_path:str,fork_path:str)->pd.DataFrame:
    sdata=json.loads(Path(seams_path).read_text(encoding='utf-8')); fmap={str(s['seam_id']):s for s in sdata.get('rows',[])}
    fdata=json.loads(Path(fork_path).read_text(encoding='utf-8'))
    rows=[]
    for r in fdata.get('rows',[]):
        a=r.get('action') or {}
        if a.get('kind')=='WAIT' or 'error' in r: continue
        sid=str(r['seamId']); s=fmap[sid]; side=str(a['side']); role=role_for(s,side)
        bid,ask,bd,ad,oc=side_book(s,side); p=finite(a.get('price')); qty=finite(a.get('qty')); base=finite(a.get('baseQty'))
        u=finite(s.get('pre_up_shares')); d=finite(s.get('pre_down_shares')); floor=finite(s.get('pre_floor')); upside=finite(s.get('pre_upside'))
        rec={
            'market_id':int(r['marketId']),'seam_id':sid,'action_name':a['name'],'actionSide':side,'routeActive':1.0 if a['kind']=='ACTIVE' else 0.0,
            'roleRepair':1.0 if role=='REPAIR' else 0.0,'roleExpand':1.0 if role=='EXPAND' else 0.0,'roleBalanced':1.0 if role=='BALANCED' else 0.0,
            'baseQty':base,'legalQty':qty,'actionPrice':p,'sideBid':bid,'sideAsk':ask,'sideSpread':ask-bid,'sideBidDepth':bd,'sideAskDepth':ad,'orderCount':oc,
            'priceMinusBid':p-bid,'priceMinusAsk':p-ask,'preFloor':floor,'preUpside':upside,'preAbsGap':abs(u-d),'preTotalShares':u+d,'preUpShares':u,'preDownShares':d,'preNetCost':finite(s.get('pre_net_cost')),
            'actualDeltaFloor':finite(r.get('deltaFloorAtEvent')),'actualDeltaUpside':finite(r.get('deltaUpsideAtEvent')),
            'eventKind':str(r.get('eventKind')),'yFill':1 if r.get('eventKind')=='FILL' else 0,'eventLagMs':int(r.get('eventLagMs') or 0),
            'preMaterializationQuoteChanged':1.0 if r.get('preMaterializationQuoteChanged') else 0.0,
            'action_event_ms':int(s['action_event_ms']), 'role':role, 'route':str(a['kind']), 'baseKey':int(round(base))
        }
        mb,ma=mat_book(r,side); rec['matBid']=mb; rec['matAsk']=ma; rec['matSpread']=ma-mb if np.isfinite(mb) and np.isfinite(ma) else np.nan
        rec['matBidDrift']=mb-bid if np.isfinite(mb) else np.nan; rec['matAskDrift']=ma-ask if np.isfinite(ma) else np.nan
        rec['priceMinusMatBid']=p-mb if np.isfinite(mb) else np.nan; rec['priceMinusMatAsk']=p-ma if np.isfinite(ma) else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)

PRE=['routeActive','roleRepair','roleExpand','roleBalanced','baseQty','legalQty','actionPrice','sideBid','sideAsk','sideSpread','sideBidDepth','sideAskDepth','orderCount','priceMinusBid','priceMinusAsk','preFloor','preUpside','preAbsGap','preTotalShares']
POST=PRE+['preMaterializationQuoteChanged','matBid','matAsk','matSpread','matBidDrift','matAskDrift','priceMinusMatBid','priceMinusMatAsk']


def prior_fit(train:pd.DataFrame):
    stats={}
    for cols in [('route','role','baseKey'),('route','role'),('route',)]:
        g=train.groupby(list(cols))['yFill'].agg(['sum','count'])
        stats[cols]={tuple(k) if isinstance(k,tuple) else (k,):((float(v['sum'])+1.0)/(float(v['count'])+2.0)) for k,v in g.iterrows()}
    glob=(float(train.yFill.sum())+1)/(len(train)+2)
    return stats,glob


def prior_predict(test:pd.DataFrame,model):
    stats,glob=model; out=[]
    for _,r in test.iterrows():
        val=None
        for cols in [('route','role','baseKey'),('route','role'),('route',)]:
            key=tuple(r[c] for c in cols)
            if key in stats[cols]: val=stats[cols][key]; break
        out.append(glob if val is None else val)
    return np.asarray(out,float)


def metric(y,p):
    p=np.clip(np.asarray(p,float),1e-6,1-1e-6); y=np.asarray(y,int)
    return {'logloss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p)),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None}


def make_model(name):
    if name=='LOGISTIC': return Pipeline([('scale',StandardScaler()),('m',LogisticRegression(max_iter=1000,C=1.0,solver='lbfgs'))])
    return ExtraTreesClassifier(n_estimators=300,min_samples_leaf=4,max_features='sqrt',random_state=20260907,n_jobs=1,class_weight=None)


def run_head(df:pd.DataFrame,features:list[str],head:str):
    markets=df.groupby('market_id')['action_event_ms'].min().sort_values().index.tolist(); folds=[(18,28),(28,38),(38,48)]
    pred_store=defaultdict(list); fold_rows=[]
    for trn_end,tst_end in folds:
        trm=markets[:trn_end]; tem=markets[trn_end:tst_end]; tr=df[df.market_id.isin(trm)].copy(); te=df[df.market_id.isin(tem)].copy()
        if len(tr)==0 or len(te)==0: continue
        base=prior_predict(te,prior_fit(tr)); fold_rows.append({'head':head,'trainMarkets':len(trm),'testMarkets':len(tem),'cell':'PRIOR',**metric(te.yFill,base)})
        for name in ('LOGISTIC','EXTRATREES'):
            m=make_model(name); Xtr=tr[features].replace([np.inf,-np.inf],np.nan).fillna(0.0).to_numpy(float); Xte=te[features].replace([np.inf,-np.inf],np.nan).fillna(0.0).to_numpy(float)
            m.fit(Xtr,tr.yFill.to_numpy(int)); p=m.predict_proba(Xte)[:,1]
            fold_rows.append({'head':head,'trainMarkets':len(trm),'testMarkets':len(tem),'cell':name,**metric(te.yFill,p)})
            for idx,pr,bp in zip(te.index,p,base): pred_store[name].append((idx,float(pr),float(bp)))
    agg={}
    for name,vals in pred_store.items():
        idx=[x[0] for x in vals]; sub=df.loc[idx].copy(); p=np.asarray([x[1] for x in vals]); bp=np.asarray([x[2] for x in vals]); y=sub.yFill.to_numpy(int)
        mm=metric(y,p); bm=metric(y,bp); wins=0; total=0; gains=[]
        for mid,g in sub.assign(_p=p,_b=bp).groupby('market_id'):
            if len(set(g.yFill))<1: continue
            ml=metric(g.yFill,g._p)['logloss']; bl=metric(g.yFill,g._b)['logloss']; gains.append(bl-ml); wins+=int(ml<bl-EPS); total+=1
        agg[name]={**mm,'baselineLogloss':bm['logloss'],'loglossGainVsBaseline':bm['logloss']-mm['logloss'],'marketImprovedVsBaseline':wins,'markets':total,'marketImprovementRate':wins/total if total else None,'medianPerMarketGain':float(np.median(gains)) if gains else None,'passes70pct':bool(total and wins/total>=.70 and mm['logloss']<bm['logloss'])}
    return fold_rows,agg


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--seams',required=True); ap.add_argument('--forks',required=True); ap.add_argument('--output',required=True); ns=ap.parse_args()
    df=build(ns.seams,ns.forks); pre=df.copy(); post=df[df.eventLagMs>0].copy()
    f1,a1=run_head(pre,PRE,'PRE_SUBMIT_FILL_FIRST'); f2,a2=run_head(post,POST,'POST_MATERIALIZATION_FILL_BEFORE_STATE_CHANGE')
    out={'version':'MARKET_CAPSULE_STRUCTURAL_EXECUTION_TEACHER64_V1_RESULT_20260907','researchOnly':True,'rows':len(df),'uniqueMarkets':int(df.market_id.nunique()),'eventCounts':df.eventKind.value_counts().to_dict(),'postMaterializationRows':len(post),'postEventCounts':post.eventKind.value_counts().to_dict(),'featureLeakAudit':{'fixedSecondsFeature':False,'eventLagFeature':False,'secondsLeftFeature':False,'samplingCategoryInFeatures':False,'TargetActionInFeatures':False,'winnerFutureInFeatures':False,'postMaterializationFeaturesUsedOnlyInPostHead':True},'foldMetrics':f1+f2,'aggregate':{'PRE_SUBMIT':a1,'POST_MATERIALIZATION':a2},'developmentGate':{'preSubmitLearnable':any(v['passes70pct'] for v in a1.values()),'postMaterializationLearnable':any(v['passes70pct'] for v in a2.values()),'runtimeAuthorityGranted':False}}
    p=Path(ns.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'rows':len(df),'postRows':len(post),'eventCounts':out['eventCounts'],'aggregate':out['aggregate'],'developmentGate':out['developmentGate']},indent=2,ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
