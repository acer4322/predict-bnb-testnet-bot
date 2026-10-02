from __future__ import annotations
import json, math
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_maker_only_refresh_notaker_wave40_plan_20260830.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_maker_lifecycle_arbitration_teacher_v2.json'

MODES=['CONTINUE_PASSIVE','SAME_PRICE_REFRESH','REPRICE_1T','REPRICE_3T']


def finite(x):
    try:
        z=float(x)
        return z if math.isfinite(z) else None
    except Exception:return None


def flatten(rec):
    c=rec.get('candidate') or {}
    p=c.get('portfolio') or {}; m=c.get('models') or {}; u=c.get('public') or {}
    # path is identical across counterfactual modes; take first available branch action.
    act=None
    for md in ('REPRICE_1T','SAME_PRICE_REFRESH','REPRICE_3T'):
        a=((rec.get('branches') or {}).get(md) or {}).get('action')
        if isinstance(a,dict) and isinstance(a.get('pathAtCancel'),dict): act=a; break
    q=(act or {}).get('pathAtCancel') or {}
    rem=finite(q.get('remainingQty')); cum=finite(q.get('cumExecQty'))
    total=(rem or 0)+(cum or 0)
    return {
      'marketId':int(rec['marketId']),
      'riskAgeMs':finite(c.get('riskAgeMs')),'bookAgeMs':finite(c.get('bookAgeMs')),
      'activeMakerOrders':finite(c.get('activeMakerOrders')),
      'makerAbsNet':finite(p.get('maker_abs_net')),'makerCoverage':finite(p.get('maker_paired_coverage')),
      'combinedAbsNet':finite(p.get('combined_abs_net')),'combinedCoverage':finite(p.get('combined_paired_coverage')),
      'floor':finite(p.get('worst_case_floor')),'bestCasePnl':finite(p.get('best_case_pnl')),
      'makerAvgPairEdge':finite(p.get('maker_avg_pair_edge')),
      'pMakerUp':finite(m.get('pMakerUp')),'pMakerDown':finite(m.get('pMakerDown')),
      'pTaker1s':finite(m.get('pTaker1s')),'pTaker3s':finite(m.get('pTaker3s')),
      'secondsLeft':finite(u.get('secondsLeft')),'directionScore':finite(u.get('directionScore')),
      'spotQueueImbalance':finite(u.get('spotQueueImbalance')),'spotTakerImbalance1s':finite(u.get('spotTakerImbalance1s')),
      'futuresQueueImbalance':finite(u.get('futuresQueueImbalance')),'futuresTakerImbalance1s':finite(u.get('futuresTakerImbalance1s')),
      'orderAgeMs':finite(q.get('orderAgeMs')),'quoteOffsetTicks':finite(q.get('quoteOffsetTicks')),
      'remainingQty':rem,'cumExecQty':cum,'fillProgressRatio':(cum/total if total>1e-9 else None),
      'initialDepth':finite(q.get('initialDepth')),'publicCumDepletion':finite(q.get('publicCumDepletion')),
      'occupiedBefore':1.0 if q.get('occupiedBefore') else 0.0,
    }


def main():
    plan=json.loads(PLAN.read_text(encoding='utf-8'))
    rows=[]; missing=[]
    for j in plan['jobs']:
        jid=j['job_id']; fp=ROOT/'data/research/lan_worker_returns'/jid/'result.json'
        if not fp.exists(): missing.append(jid); continue
        rep=json.loads(fp.read_text(encoding='utf-8'))
        for r in rep.get('rows') or []:
            if r.get('error') or not r.get('candidate') or not r.get('branches'): continue
            x=flatten(r); bs=r['baselineScore']; basep=float(bs['pnlUsdt'])
            outcomes={'CONTINUE_PASSIVE':basep}
            conversions={'CONTINUE_PASSIVE':('WIN' if basep>0 else 'LOSS')+'->'+('WIN' if basep>0 else 'LOSS')}
            for md,b in r['branches'].items():
                outcomes[md]=float(b['score']['pnlUsdt']); conversions[md]=str(b['conversion'])
            # Primary teacher objective: preserve/create WIN first, then choose highest PnL among winning actions;
            # if none win, choose least-negative PnL. This label is offline-only.
            winners=[a for a,v in outcomes.items() if v>0]
            pool=winners if winners else list(outcomes)
            best=max(pool,key=lambda a:(outcomes[a], {'CONTINUE_PASSIVE':3,'SAME_PRICE_REFRESH':2,'REPRICE_3T':1,'REPRICE_1T':0}[a]))
            x.update({'baselinePnl':basep,'baselineWin':int(basep>0),'actionPnl':outcomes,'conversions':conversions,
                      'teacherAction':best,'teacherPnl':outcomes[best],
                      'reprice1Delta':outcomes['REPRICE_1T']-basep,
                      'reprice1Win':int(outcomes['REPRICE_1T']>0),
                      'reprice1Harm':int(outcomes['REPRICE_1T']<basep-1e-9),
                      'reprice1LossToWin':int(basep<=0 and outcomes['REPRICE_1T']>0),
                      'continueWinner':int(basep>0)})
            rows.append(x)
    feats=[k for k in rows[0] if k not in {'marketId','baselinePnl','baselineWin','actionPnl','conversions','teacherAction','teacherPnl','reprice1Delta','reprice1Win','reprice1Harm','reprice1LossToWin','continueWinner'}] if rows else []
    # Simple univariate effect-size screen; strict-past features only.
    screens={}
    for target in ('reprice1LossToWin','reprice1Harm'):
        pos=[r for r in rows if r[target]==1]; neg=[r for r in rows if r[target]==0]
        arr=[]
        for f in feats:
            a=np.array([r[f] for r in pos if r.get(f) is not None],float); b=np.array([r[f] for r in neg if r.get(f) is not None],float)
            if len(a)<2 or len(b)<2: continue
            denom=math.sqrt((float(np.var(a))+float(np.var(b)))/2+1e-12)
            d=(float(np.mean(a))-float(np.mean(b)))/denom
            arr.append({'feature':f,'effectSize':d,'posMean':float(np.mean(a)),'negMean':float(np.mean(b)),'posN':len(a),'negN':len(b)})
        screens[target]=sorted(arr,key=lambda z:abs(z['effectSize']),reverse=True)[:12]
    # Tiny leave-one-out nearest-centroid arbitration as a diagnostic only; no action authority.
    pred=[]
    numeric=[f for f in feats if sum(r.get(f) is not None for r in rows)>=max(8,len(rows)//2)]
    for i,r in enumerate(rows):
        tr=[z for j,z in enumerate(rows) if j!=i]
        means={f:np.mean([z[f] for z in tr if z.get(f) is not None]) for f in numeric}
        stds={f:np.std([z[f] for z in tr if z.get(f) is not None])+1e-9 for f in numeric}
        cents={}
        for a in MODES:
            aa=[z for z in tr if z['teacherAction']==a]
            if len(aa)<2: continue
            cents[a]={f:np.mean([(z[f] if z.get(f) is not None else means[f]) for z in aa]) for f in numeric}
        def dist(a):
            return np.mean([(((r[f] if r.get(f) is not None else means[f])-cents[a][f])/stds[f])**2 for f in numeric])
        pa=min(cents,key=dist) if cents else 'CONTINUE_PASSIVE'
        pred.append({'marketId':r['marketId'],'actual':r['teacherAction'],'predicted':pa,'correct':pa==r['teacherAction'],
                     'predictedPnl':r['actionPnl'][pa],'oraclePnl':r['teacherPnl'],'continuePnl':r['baselinePnl']})
    out={'version':'R4_MAKER_LIFECYCLE_ARBITRATION_TEACHER_V2','researchOnly':True,'actionAuthority':False,
         'source':'40 consumed realistic-HFT Maker-only exact-seam counterfactuals','strictPastFeaturesOnly':True,
         'offlineOutcomeUse':'teacher/scoring only','marketsWithExactSeam':len(rows),'missingJobs':missing,
         'teacherActionCounts':dict(Counter(r['teacherAction'] for r in rows)),
         'reprice1':{'lossToWin':sum(r['reprice1LossToWin'] for r in rows),'harmfulVsContinue':sum(r['reprice1Harm'] for r in rows),
                     'winnerDestroyed':sum(r['baselineWin'] and not r['reprice1Win'] for r in rows)},
         'featureScreens':screens,'diagnosticLooCentroid':{'n':len(pred),'accuracy':sum(p['correct'] for p in pred)/len(pred) if pred else None,
                    'pnlIfPredicted':sum(p['predictedPnl'] for p in pred),'pnlContinue':sum(p['continuePnl'] for p in pred),
                    'pnlOracle':sum(p['oraclePnl'] for p in pred),'rows':pred},'rows':rows,
         'boundary':'Development/separability diagnostic only; no threshold/model promotion. Taker fallback is outside this passive-only teacher.'}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({k:v for k,v in out.items() if k not in {'rows','featureScreens'}},ensure_ascii=False,indent=2))
    print('TOP_LOSS_TO_WIN',json.dumps(screens.get('reprice1LossToWin',[])[:6],ensure_ascii=False))
    print('TOP_HARM',json.dumps(screens.get('reprice1Harm',[])[:6],ensure_ascii=False))
if __name__=='__main__': main()
