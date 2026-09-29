from __future__ import annotations
import argparse, json, os
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
try:
    from tools.train_market_capsule_structural_execution_teacher64_v1 import build, POST, make_model, EPS
except ImportError:
    from train_market_capsule_structural_execution_teacher64_v1 import build, POST, make_model, EPS


def full_deltas(r):
    u=float(r.preUpShares); d=float(r.preDownShares); c=float(r.preNetCost); q=float(r.legalQty); p=float(r.actionPrice)
    if str(r.actionSide)=='UP': u+=q
    else: d+=q
    c+=q*p
    f=min(u-c,d-c); b=max(u-c,d-c)
    return f-float(r.preFloor), b-float(r.preUpside)


def prep(seams,forks):
    df=build(seams,forks); df=df[df.eventLagMs>0].copy()
    vals=df.apply(full_deltas,axis=1,result_type='expand'); df['fullDF']=vals[0]; df['fullDU']=vals[1]
    return df


def X(df):
    return df[POST].replace([np.inf,-np.inf],np.nan).fillna(0.0).to_numpy(float)


def fit_models(tr):
    clf=make_model('EXTRATREES'); clf.fit(X(tr),tr.yFill.to_numpy(int))
    kw=dict(n_estimators=300,min_samples_leaf=4,max_features='sqrt',random_state=20260907,n_jobs=1)
    rf=ExtraTreesRegressor(**kw); ru=ExtraTreesRegressor(**kw)
    rf.fit(X(tr),tr.actualDeltaFloor.to_numpy(float)); ru.fit(X(tr),tr.actualDeltaUpside.to_numpy(float))
    return clf,rf,ru


def score(te,models):
    clf,rf,ru=models; xx=X(te)
    pf=clf.predict_proba(xx)[:,1]
    jF=rf.predict(xx); jU=ru.predict(xx)
    fF=pf*te.fullDF.to_numpy(float); fU=pf*te.fullDU.to_numpy(float)
    aF=te.actualDeltaFloor.to_numpy(float); aU=te.actualDeltaUpside.to_numpy(float)
    den=te.fullDF.abs().to_numpy(float)+te.fullDU.abs().to_numpy(float)+1e-6
    out=te[['market_id','role','route','seam_id','action_name']].copy()
    out['errF_factorized']=np.abs(fF-aF); out['errU_factorized']=np.abs(fU-aU)
    out['errF_joint']=np.abs(jF-aF); out['errU_joint']=np.abs(jU-aU)
    out['norm_factorized']=(out.errF_factorized+out.errU_factorized)/den
    out['norm_joint']=(out.errF_joint+out.errU_joint)/den
    return out


def summarize(z):
    markets=[]; wins=0
    for mid,g in z.groupby('market_id'):
        f=float(g.norm_factorized.mean()); j=float(g.norm_joint.mean()); wins+=int(j<f-EPS)
        markets.append({'marketId':int(mid),'rows':len(g),'factorizedNormError':f,'jointNormError':j,'gain':f-j})
    n=len(markets)
    pmf=np.array([m['factorizedNormError'] for m in markets],float) if markets else np.array([])
    pmj=np.array([m['jointNormError'] for m in markets],float) if markets else np.array([])
    agg={
      'rows':len(z),'markets':n,
      'factorizedFloorMae':float(z.errF_factorized.mean()),'jointFloorMae':float(z.errF_joint.mean()),
      'factorizedUpsideMae':float(z.errU_factorized.mean()),'jointUpsideMae':float(z.errU_joint.mean()),
      'factorizedNormError':float(z.norm_factorized.mean()),'jointNormError':float(z.norm_joint.mean()),
      'marketImproved':wins,'marketImprovementRate':wins/n if n else None,
      'factorizedMarketP90NormError':float(np.quantile(pmf,.90)) if n else None,
      'jointMarketP90NormError':float(np.quantile(pmj,.90)) if n else None,
    }
    gate={
      'floorMaeImproves':agg['jointFloorMae']<agg['factorizedFloorMae']-EPS,
      'upsideMaeImproves':agg['jointUpsideMae']<agg['factorizedUpsideMae']-EPS,
      'meanNormImproves':agg['jointNormError']<agg['factorizedNormError']-EPS,
      'market70':bool(n and agg['marketImprovementRate']>=.70),
      'p90NonWorse':bool(n and agg['jointMarketP90NormError']<=agg['factorizedMarketP90NormError']+EPS),
    }
    gate['pass']=all(gate.values())
    return agg,gate,markets


def development(A):
    markets=A.groupby('market_id')['action_event_ms'].min().sort_values().index.tolist(); folds=[(18,28),(28,38),(38,48)]
    zz=[]; fs=[]
    for trn_end,tst_end in folds:
        trm=markets[:trn_end]; tem=markets[trn_end:tst_end]
        tr=A[A.market_id.isin(trm)].copy(); te=A[A.market_id.isin(tem)].copy()
        if len(tr)==0 or len(te)==0: continue
        zz.append(score(te,fit_models(tr))); fs.append({'trainMarkets':len(trm),'testMarkets':len(tem),'rows':len(te)})
    if not zz:return {'folds':fs,'aggregate':None,'gate':None,'marketRows':[]}
    import pandas as pd
    z=pd.concat(zz,ignore_index=True); a,g,m=summarize(z); return {'folds':fs,'aggregate':a,'gate':g,'marketRows':m}


def main():
    ap=argparse.ArgumentParser()
    for k in ('a-seams','a-forks','b-seams','b-forks','c-seams','c-forks','output'): ap.add_argument('--'+k,required=True)
    ns=ap.parse_args()
    A=prep(ns.a_seams,ns.a_forks); B=prep(ns.b_seams,ns.b_forks); C=prep(ns.c_seams,ns.c_forks)
    dev=development(A)
    frozen=fit_models(A)
    zb=score(B,frozen); bA,bG,bM=summarize(zb)
    zc=score(C,frozen); cA,cG,cM=summarize(zc)
    out={
      'version':'MARKET_CAPSULE_H2_JOINT_FILL_VALUE_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,
      'trainA':{'rows':len(A),'markets':int(A.market_id.nunique())},
      'developmentA':dev,
      'externalB':{'aggregate':bA,'gate':bG,'marketRows':bM},
      'externalC':{'aggregate':cA,'gate':cG,'marketRows':cM},
      'primaryVerdict':'PASS_H2' if bG['pass'] and cG['pass'] else 'FAIL_H2_EXTERNAL_GENERALIZATION',
      'guardrails':{'samePOSTFeatures':True,'eventLagFeature':False,'fixedSeconds':False,'winnerFuture':False,'TargetAction':False,'BCRetrain':False,'thresholdSweep':False,'modelSweep':False}
    }
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(ns.output).upper()=='AUTO' else Path(ns.output)
    op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'developmentA':dev.get('aggregate'),'B':bA,'Bgate':bG,'C':cA,'Cgate':cG,'verdict':out['primaryVerdict']},ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
