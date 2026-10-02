from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np

try:
    from tools.train_market_capsule_structural_execution_teacher64_v1 import build, POST, prior_fit, prior_predict, make_model, EPS
except ImportError:
    from train_market_capsule_structural_execution_teacher64_v1 import build, POST, prior_fit, prior_predict, make_model, EPS


def full_deltas(r):
    u=float(r.preUpShares); d=float(r.preDownShares); c=float(r.preNetCost); q=float(r.legalQty); p=float(r.actionPrice)
    if str(r.actionSide)=='UP': u+=q
    else: d+=q
    c+=q*p
    f=min(u-c,d-c); b=max(u-c,d-c)
    return f-float(r.preFloor), b-float(r.preUpside)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--seams',required=True); ap.add_argument('--forks',required=True); ap.add_argument('--output',required=True); ns=ap.parse_args()
    df=build(ns.seams,ns.forks); df=df[df.eventLagMs>0].copy()
    vals=df.apply(full_deltas,axis=1,result_type='expand'); df['fullDF']=vals[0]; df['fullDU']=vals[1]
    markets=df.groupby('market_id')['action_event_ms'].min().sort_values().index.tolist(); folds=[(18,28),(28,38),(38,48)]
    pred=[]; fold=[]
    for trn_end,tst_end in folds:
        trm=markets[:trn_end]; tem=markets[trn_end:tst_end]; tr=df[df.market_id.isin(trm)].copy(); te=df[df.market_id.isin(tem)].copy()
        base=prior_predict(te,prior_fit(tr))
        m=make_model('EXTRATREES'); Xtr=tr[POST].replace([np.inf,-np.inf],np.nan).fillna(0.0).to_numpy(float); Xte=te[POST].replace([np.inf,-np.inf],np.nan).fillna(0.0).to_numpy(float); m.fit(Xtr,tr.yFill.to_numpy(int)); pm=m.predict_proba(Xte)[:,1]
        for idx,mp,bp in zip(te.index,pm,base): pred.append((idx,float(mp),float(bp)))
        fold.append({'trainMarkets':len(trm),'testMarkets':len(tem),'rows':len(te)})
    idx=[x[0] for x in pred]; z=df.loc[idx].copy(); z['pModel']=[x[1] for x in pred]; z['pPrior']=[x[2] for x in pred]
    for tag,pcol in [('model','pModel'),('prior','pPrior')]:
        z[f'predDF_{tag}']=z[pcol]*z.fullDF; z[f'predDU_{tag}']=z[pcol]*z.fullDU
        z[f'errF_{tag}']=(z[f'predDF_{tag}']-z.actualDeltaFloor).abs(); z[f'errU_{tag}']=(z[f'predDU_{tag}']-z.actualDeltaUpside).abs()
        den=z.fullDF.abs()+z.fullDU.abs()+1e-6; z[f'normErr_{tag}']=(z[f'errF_{tag}']+z[f'errU_{tag}'])/den
    market=[]; wins=0
    for mid,g in z.groupby('market_id'):
        me=float(g.normErr_model.mean()); pe=float(g.normErr_prior.mean()); wins+=int(me<pe-EPS); market.append({'marketId':int(mid),'rows':len(g),'modelNormError':me,'priorNormError':pe,'gain':pe-me})
    total=len(market); role={}
    for rr,g in z.groupby('role'):
        role[str(rr)]={'rows':len(g),'modelFloorMae':float(g.errF_model.mean()),'priorFloorMae':float(g.errF_prior.mean()),'modelUpsideMae':float(g.errU_model.mean()),'priorUpsideMae':float(g.errU_prior.mean())}
    out={'version':'MARKET_CAPSULE_FACTORIZED_LOCAL_VALUE_V1_RESULT_20260907','researchOnly':True,'rows':len(z),'markets':total,'folds':fold,'aggregate':{'modelFloorMae':float(z.errF_model.mean()),'priorFloorMae':float(z.errF_prior.mean()),'modelUpsideMae':float(z.errU_model.mean()),'priorUpsideMae':float(z.errU_prior.mean()),'modelNormError':float(z.normErr_model.mean()),'priorNormError':float(z.normErr_prior.mean()),'marketImproved':wins,'marketImprovementRate':wins/total if total else None},'byRole':role,'marketRows':market}
    a=out['aggregate']; out['developmentGate']={'floorMaeImproves':a['modelFloorMae']<a['priorFloorMae'],'upsideMaeImproves':a['modelUpsideMae']<a['priorUpsideMae'],'market70':a['marketImprovementRate']>=.70 if total else False,'pass':a['modelFloorMae']<a['priorFloorMae'] and a['modelUpsideMae']<a['priorUpsideMae'] and a['marketImprovementRate']>=.70,'runtimeAuthorityGranted':False}
    p=Path(ns.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'aggregate':out['aggregate'],'byRole':role,'developmentGate':out['developmentGate']},indent=2,ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
