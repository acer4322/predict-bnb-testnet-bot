from __future__ import annotations
import argparse,json,statistics,os
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error

GEOM=['actionIsReexpand','actionSideUp','actionPrice','actionQty','fullFillMidpoint','fullFillSkew','repairProgressFrac','remainingDebtQty','inventoryGapQty','floor','best','payoffGap','freeSlots','liveSlots','qLadderLive','actionQtyOverDebt']
FULL=GEOM+['liveRepairSlots','liveExpandSlots','boundaryWasFill','bookImbalanceAligned','bookSpread','weakBid','weakAsk','expandBid','expandAsk','weakQuoteSpread','expandQuoteSpread','actionPriceMinusSameBid','actionPriceMinusSameAsk']
TARGETS=['localDeltaMidpoint','localDeltaSkew']

def q90(a):
    if not a:return 0.0
    return float(np.quantile(np.asarray(a,float),0.9,method='linear'))

def eval_pred(rows,yt,yp,scale):
    ae=np.abs(yt-yp)
    vec=np.mean(ae/scale,axis=1)
    by={}
    for i,r in enumerate(rows):by.setdefault(int(r['marketId']),[]).append(float(vec[i]))
    return {'maeMidpoint':float(np.mean(ae[:,0])),'maeSkew':float(np.mean(ae[:,1])),'vectorMean':float(np.mean(vec)),'vectorP90':q90(list(vec)),'marketVector':{str(k):float(np.mean(v)) for k,v in by.items()}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--corpus',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.corpus).read_text(encoding='utf-8')); rows=d['rows']; mids=sorted({int(r['marketId']) for r in rows}); train_m=set(mids[:70]); test_m=set(mids[70:]); tr=[r for r in rows if int(r['marketId']) in train_m]; te=[r for r in rows if int(r['marketId']) in test_m]
    ytr=np.asarray([[float(r['targets'][t]) for t in TARGETS] for r in tr],float); yte=np.asarray([[float(r['targets'][t]) for t in TARGETS] for r in te],float)
    scale=np.maximum(np.mean(np.abs(ytr),axis=0),1e-6)
    zero=np.zeros_like(yte); mech=np.asarray([[float(r['features']['fullFillMidpoint']),float(r['features']['fullFillSkew'])] for r in te],float)
    base_zero=eval_pred(te,yte,zero,scale); base_mech=eval_pred(te,yte,mech,scale)
    cells={}
    for fs_name,cols in [('GEOMETRY',GEOM),('EVENT_BOOK',FULL)]:
        Xtr=np.asarray([[float(r['features'][c]) for c in cols] for r in tr],float); Xte=np.asarray([[float(r['features'][c]) for c in cols] for r in te],float)
        models={
            'Ridge':make_pipeline(StandardScaler(),Ridge(alpha=1.0)),
            'ExtraTrees':ExtraTreesRegressor(n_estimators=300,max_depth=6,min_samples_leaf=3,max_features='sqrt',random_state=20260907,n_jobs=1)
        }
        for mn,m in models.items():
            m.fit(Xtr,ytr); pred=np.asarray(m.predict(Xte),float); ev=eval_pred(te,yte,pred,scale)
            wins=sum(ev['marketVector'][str(mid)]<base_zero['marketVector'][str(mid)]-1e-12 for mid in sorted(test_m)); losses=sum(ev['marketVector'][str(mid)]>base_zero['marketVector'][str(mid)]+1e-12 for mid in sorted(test_m)); ties=30-wins-losses
            gate={'midpointBeatsZero':ev['maeMidpoint']<base_zero['maeMidpoint'],'skewBeatsZero':ev['maeSkew']<base_zero['maeSkew'],'marketWinsAtLeast70pct':wins>=21,'p90NotWorse':ev['vectorP90']<=base_zero['vectorP90']}
            cells[f'{fs_name}__{mn}']={'features':cols,'metrics':ev,'vsZero':{'marketWins':wins,'marketLosses':losses,'marketTies':ties},'gate':gate,'pass':all(gate.values())}
    out={'version':'MANAGEMENT_MAINLINE_LIFECYCLE_LOCAL_VECTOR_TEACHER_V1_20260907','researchOnly':True,'runtimeAuthority':False,'trainMarkets':sorted(train_m),'testMarkets':sorted(test_m),'trainRows':len(tr),'testRows':len(te),'targetScale':dict(zip(TARGETS,map(float,scale))),'baselines':{'ZERO':base_zero,'FULL_FILL_MECHANICAL':base_mech},'cells':cells,'anyPass':any(x['pass'] for x in cells.values()),'boundary':['fixed prereg models/features','chronology market split 70/30','no threshold/model/feature sweep','no winner/terminal/Target/future features','no fixed time/window features','PASS is transition-model evidence only; no action authority']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'anyPass':out['anyPass'],'zero':base_zero,'cells':{k:{'pass':v['pass'],'metrics':v['metrics'],'vsZero':v['vsZero'],'gate':v['gate']} for k,v in cells.items()}},ensure_ascii=False))
if __name__=='__main__':main()
