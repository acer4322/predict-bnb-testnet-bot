from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
try:
    from tools.train_market_capsule_structural_execution_teacher64_v1 import build, POST, prior_fit, prior_predict, make_model, EPS
except ImportError:
    from train_market_capsule_structural_execution_teacher64_v1 import build, POST, prior_fit, prior_predict, make_model, EPS


def full_deltas(r):
    u=float(r.preUpShares); d=float(r.preDownShares); c=float(r.preNetCost)
    q=float(r.legalQty); p=float(r.actionPrice)
    if str(r.actionSide)=='UP': u += q
    else: d += q
    c += q*p
    f=min(u-c,d-c); b=max(u-c,d-c)
    return f-float(r.preFloor), b-float(r.preUpside)


def prepare(seams, forks):
    df=build(seams,forks)
    df=df[df.eventLagMs>0].copy()
    vals=df.apply(full_deltas,axis=1,result_type='expand')
    df['fullDF']=vals[0]; df['fullDU']=vals[1]
    return df


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--train-seams',required=True); ap.add_argument('--train-forks',required=True)
    ap.add_argument('--test-seams',required=True); ap.add_argument('--test-forks',required=True)
    ap.add_argument('--output',required=True); ns=ap.parse_args()
    tr=prepare(ns.train_seams,ns.train_forks); te=prepare(ns.test_seams,ns.test_forks)
    train_markets=set(map(int,tr.market_id.unique())); test_markets=set(map(int,te.market_id.unique()))
    overlap=sorted(train_markets & test_markets)
    if overlap:
        raise RuntimeError(f'market overlap train/test: {overlap[:10]}')

    prior=prior_fit(tr); pprior=prior_predict(te,prior)
    m=make_model('EXTRATREES')
    Xtr=tr[POST].replace([np.inf,-np.inf],np.nan).fillna(0.0).to_numpy(float)
    Xte=te[POST].replace([np.inf,-np.inf],np.nan).fillna(0.0).to_numpy(float)
    m.fit(Xtr,tr.yFill.to_numpy(int)); pmodel=m.predict_proba(Xte)[:,1]
    te=te.copy(); te['pModel']=pmodel; te['pPrior']=pprior
    for tag,pcol in [('model','pModel'),('prior','pPrior')]:
        te[f'predDF_{tag}']=te[pcol]*te.fullDF; te[f'predDU_{tag}']=te[pcol]*te.fullDU
        te[f'errF_{tag}']=(te[f'predDF_{tag}']-te.actualDeltaFloor).abs()
        te[f'errU_{tag}']=(te[f'predDU_{tag}']-te.actualDeltaUpside).abs()
        den=te.fullDF.abs()+te.fullDU.abs()+1e-6
        te[f'normErr_{tag}']=(te[f'errF_{tag}']+te[f'errU_{tag}'])/den

    market=[]; wins=0
    for mid,g in te.groupby('market_id'):
        me=float(g.normErr_model.mean()); pe=float(g.normErr_prior.mean())
        improved=me < pe-EPS; wins += int(improved)
        market.append({'marketId':int(mid),'rows':len(g),'modelNormError':me,'priorNormError':pe,'gain':pe-me,'improved':bool(improved)})
    total=len(market)
    byrole={}
    for role,g in te.groupby('role'):
        byrole[str(role)]={
            'rows':len(g),
            'modelFloorMae':float(g.errF_model.mean()),'priorFloorMae':float(g.errF_prior.mean()),
            'modelUpsideMae':float(g.errU_model.mean()),'priorUpsideMae':float(g.errU_prior.mean()),
            'modelNormError':float(g.normErr_model.mean()),'priorNormError':float(g.normErr_prior.mean())
        }
    agg={
        'modelFloorMae':float(te.errF_model.mean()),'priorFloorMae':float(te.errF_prior.mean()),
        'modelUpsideMae':float(te.errU_model.mean()),'priorUpsideMae':float(te.errU_prior.mean()),
        'modelNormError':float(te.normErr_model.mean()),'priorNormError':float(te.normErr_prior.mean()),
        'marketImproved':wins,'marketTotal':total,'marketImprovementRate':wins/total if total else None,
        'trainRows':len(tr),'trainMarkets':len(train_markets),'testRows':len(te),'testMarkets':len(test_markets)
    }
    gate={
        'marketDisjoint':not overlap,
        'floorMaeImproves':agg['modelFloorMae'] < agg['priorFloorMae'],
        'upsideMaeImproves':agg['modelUpsideMae'] < agg['priorUpsideMae'],
        'market70':agg['marketImprovementRate'] >= .70 if total else False,
    }
    gate['pass']=all(gate.values()); gate['runtimeAuthorityGranted']=False
    out={
        'version':'MARKET_CAPSULE_FACTORIZED_LOCAL_VALUE_EXTERNAL_C_V1_RESULT_20260907',
        'researchOnly':True,'aggregate':agg,'byRole':byrole,'marketRows':market,
        'marketOverlap':overlap,'externalGate':gate,
        'boundary':'Model and empirical prior fit on A only. Holdout C used only for inference/scoring; no recalibration, threshold sweep, or feature change.'
    }
    p=Path(ns.output); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'aggregate':agg,'byRole':byrole,'externalGate':gate},indent=2,ensure_ascii=False))
    return 0

if __name__=='__main__': raise SystemExit(main())
