from __future__ import annotations
import argparse, json, math, os
from pathlib import Path

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

INTERVALS = [(0,500),(500,1000),(1000,2000),(2000,5000),(5000,10000)]
CAUSES = ['SURVIVE','FILL','OWN_CANCEL_ZERO','GENUINE_ZERO']
CAT_ROLE = ['role','interval']
CAT_FULL = ['role','side','scopeSide','interval']
NUM_FULL = [
    'price','qty','rank','depth','bestPrice','bestDepth','distanceTicks','pairSum',
    'floor','best','gap','upQty','downQty','cost','scopeDebtQty','reservedRepairQuota',
    'availableExpandRiskCredit','scopeRepairProgressClocks','livePassiveSlots','liveActiveSlots',
    'repairQtyAuthorized','overflowQtyAuthorized'
]


def _risk_rows(order: dict) -> list[dict]:
    delay = order.get('eventDelayMs')
    cause = str(order.get('cause') or 'CENSORED')
    out=[]
    for i,(a,b) in enumerate(INTERVALS):
        if delay is not None and float(delay) <= a:
            break
        y='SURVIVE'
        if delay is not None and a < float(delay) <= b and cause in CAUSES[1:]:
            y=cause
        r={k:order.get(k) for k in set(CAT_FULL+NUM_FULL)}
        r['interval']=f'{a}_{b}'
        r['marketId']=int(order['marketId'])
        r['y']=y
        out.append(r)
        if y!='SURVIVE':
            break
    return out


def _load(path: Path):
    d=json.loads(path.read_text(encoding='utf-8'))
    orders=[]
    for mr in d['rows']:
        mid=int(mr['marketId'])
        for x0 in mr.get('hazardRows',[]):
            x=dict(x0);x['marketId']=mid
            orders.append(x)
    rr=[]
    for x in orders: rr.extend(_risk_rows(x))
    return d,orders,rr


def _matrix(rows, cats, nums):
    X=[];y=[]
    for r in rows:
        X.append([r.get(k) for k in cats+nums]);y.append(r['y'])
    return np.asarray(X,dtype=object),np.asarray(y,dtype=object)


def _pipe(cats,nums):
    idx_cat=list(range(len(cats)));idx_num=list(range(len(cats),len(cats)+len(nums)))
    trs=[]
    if cats:
        trs.append(('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),('oh',OneHotEncoder(handle_unknown='ignore'))]),idx_cat))
    if nums:
        trs.append(('num',Pipeline([('imp',SimpleImputer(strategy='median')),('scale',StandardScaler())]),idx_num))
    pre=ColumnTransformer(trs,remainder='drop')
    clf=LogisticRegression(max_iter=2500,class_weight='balanced',C=1.0,random_state=20260907)
    return Pipeline([('pre',pre),('clf',clf)])


def _align_probs(model,X):
    p=model.predict_proba(X);classes=list(model.named_steps['clf'].classes_)
    out=np.zeros((len(X),len(CAUSES)),float)
    for j,c in enumerate(CAUSES):
        if c in classes:out[:,j]=p[:,classes.index(c)]
    s=out.sum(axis=1,keepdims=True);s[s<=0]=1.0
    return out/s


def _metrics(y,p):
    yi=np.asarray([CAUSES.index(v) for v in y],int)
    one=np.zeros_like(p);one[np.arange(len(y)),yi]=1.0
    return {
        'n':int(len(y)),
        'logLoss':float(log_loss(y,p,labels=CAUSES)),
        'multiBrier':float(np.mean(np.sum((p-one)**2,axis=1))),
        'accuracy':float(np.mean(np.argmax(p,axis=1)==yi)),
    }


def _pooled_interval_probs(train_rows,test_rows):
    from collections import Counter,defaultdict
    by=defaultdict(Counter)
    for r in train_rows:by[r['interval']][r['y']]+=1
    arr=[]
    for r in test_rows:
        c=by[r['interval']];n=sum(c.values());
        # Laplace smoothing to avoid zero probability.
        arr.append([(c[k]+1)/(n+len(CAUSES)) for k in CAUSES])
    return np.asarray(arr,float)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    src=Path(a.input);d,orders,rr=_load(src)
    mids=sorted({int(x['marketId']) for x in rr})
    train=set(mids[:12]);val=set(mids[12:16]);hold=set(mids[16:])
    tr=[r for r in rr if r['marketId'] in train];va=[r for r in rr if r['marketId'] in val];ho=[r for r in rr if r['marketId'] in hold]

    result={'version':'LANE_G_COMPETING_RISK_HAZARD_MODEL_V1','researchOnly':True,'runtimeAuthority':False,
            'source':str(src),'markets':mids,'trainMarkets':sorted(train),'validationMarkets':sorted(val),'holdoutMarkets':sorted(hold),
            'orders':len(orders),'riskRows':len(rr),'intervalsMs':INTERVALS,'causes':CAUSES,'models':{}}

    p0v=_pooled_interval_probs(tr,va);p0h=_pooled_interval_probs(tr,ho)
    yv=np.asarray([r['y'] for r in va],object);yh=np.asarray([r['y'] for r in ho],object)
    result['models']['POOLED_INTERVAL']={'validation':_metrics(yv,p0v),'holdout':_metrics(yh,p0h)}

    specs=[('ROLE_INTERVAL',CAT_ROLE,[]),('FULL_SUBMIT_STATE',CAT_FULL,NUM_FULL)]
    fitted={}
    for name,cats,nums in specs:
        Xtr,ytr=_matrix(tr,cats,nums);Xv,_=_matrix(va,cats,nums);Xh,_=_matrix(ho,cats,nums)
        m=_pipe(cats,nums);m.fit(Xtr,ytr);fitted[name]=m
        result['models'][name]={'validation':_metrics(yv,_align_probs(m,Xv)),'holdout':_metrics(yh,_align_probs(m,Xh)),'categoricalFeatures':cats,'numericFeatures':nums}

    # Selection uses validation log-loss only; holdout is reported after winner is fixed.
    winner=min(['POOLED_INTERVAL','ROLE_INTERVAL','FULL_SUBMIT_STATE'],key=lambda n:result['models'][n]['validation']['logLoss'])
    result['selectedByValidationLogLoss']=winner
    result['holdoutSelected']=result['models'][winner]['holdout']
    result['gates']={
        'fullVsPooledHoldoutLogLossBetter':result['models']['FULL_SUBMIT_STATE']['holdout']['logLoss'] < result['models']['POOLED_INTERVAL']['holdout']['logLoss'],
        'fullVsRoleHoldoutLogLossBetter':result['models']['FULL_SUBMIT_STATE']['holdout']['logLoss'] < result['models']['ROLE_INTERVAL']['holdout']['logLoss'],
    }
    result['boundary']=[
        'consumed full24 only; no fresh data',
        'strict-past submit features only',
        'winner/Target/future state absent from features',
        'discrete-time cause-specific competing-risk labels from realistic-HFT order outcomes',
        'validation chooses model family; chronological final 8 markets are untouched holdout for reported evaluation',
        'research-only; no Active threshold or runtime switch promoted'
    ]
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'selected':winner,'gates':result['gates'],'models':{k:{'validation':v['validation'],'holdout':v['holdout']} for k,v in result['models'].items()}},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
