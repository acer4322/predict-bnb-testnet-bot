"""Compact paired inference and anti-collapse audit; no runtime/model writes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--result', required=True)
    ap.add_argument('--training-rows', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    obj = json.loads(Path(a.result).read_text())
    rows = obj['rows']
    data = [json.loads(x) for x in Path(a.training_rows).read_text().splitlines() if x.strip()]
    mids = sorted({(x['windowEndMs'], x['marketId']) for x in data})
    trids = {m for _, m in mids[:70]}
    train = [x for x in data if x['marketId'] in trids]
    labels = list(rows[0]['deltas'])
    # Baselines are frozen TRAIN-only outcome means, not fit to branch outcomes.
    prior = {(role,l):float(np.mean([x[l] for x in train if x['role']==role])) for role in ('SATELLITE_EXPAND','SATELLITE_REPAIR','ECONOMIC_CORE') for l in labels}
    rng = np.random.default_rng(20260907)
    indices = rng.integers(0,len(rows),size=(2000,len(rows)))
    metrics = {}
    for l in labels:
        actual = np.array([[r['branches'][b]['observed'][l] for b in ('control','treatment')] for r in rows])
        predictions = {
            'frozenPostSubmit':np.array([[r['branches'][b]['predictedPostSubmit'][l] for b in ('control','treatment')] for r in rows]),
            'frozenPreSubmit':np.array([[r['branches'][b]['predictedPreSubmit'][l] for b in ('control','treatment')] for r in rows]),
            'trainRolePrior':np.array([[prior[(r['branches'][b]['featuresPostSubmit']['role'],l)] for b in ('control','treatment')] for r in rows]),
            'constantZero':np.zeros_like(actual,dtype=float),
        }
        observed_delta = actual[:,1]-actual[:,0]
        non = np.abs(observed_delta)>1e-9
        binary = l.startswith(('anyFill','cancelReq','terminal'))
        item = {'markets':len(rows),'carrierRows':2*len(rows),'horizonSeconds':int(l[-2]),'nonTiedPairs':int(non.sum()),'observedTies':int((~non).sum()),'positiveTreatmentDeltaPairs':int((observed_delta>1e-9).sum()),'negativeTreatmentDeltaPairs':int((observed_delta< -1e-9).sum()),'models':{}}
        losses = {}
        for name,p in predictions.items():
            delta = p[:,1]-p[:,0]
            loss = ((p-actual)**2 if binary else np.abs(p-actual)).mean(axis=1)
            losses[name] = loss
            item['models'][name] = {'lossKind':'Brier' if binary else 'MAE','loss':float(loss.mean()),'pairedDeltaMAE':float(np.abs(delta-observed_delta).mean()),'correctNonTieSigns':int((delta[non]*observed_delta[non]>0).sum()),'positivePredictedDeltaPairs':int((delta>1e-9).sum()),'negativePredictedDeltaPairs':int((delta< -1e-9).sum()),'nonTieSignAccuracy':float((delta[non]*observed_delta[non]>0).mean()) if non.any() else None}
        item['incrementOverTrainRolePrior']={}
        for name in ('frozenPostSubmit','frozenPreSubmit'):
            diff=losses['trainRolePrior']-losses[name]
            item['incrementOverTrainRolePrior'][name]={'lossImprovement':float(diff.mean()),'marketBootstrap95CI':np.quantile(diff[indices].mean(axis=1),[.025,.975]).tolist(),'resamples':2000}
        metrics[l]=item
    activity={}
    for side in ('control','treatment'):
        bs=[r['branches'][side] for r in rows]
        act={k:sum(b['activity'][k] for b in bs) for k in ('tradeCoverage','fills','submits','alternations','buyNotional','grossExposure','expandRepairExpandRoleCycleProxy','zeroAction','nearZeroActionLe1','openAttempts')}
        act['openAttemptWithoutSubmitFraction']=sum(b['activity']['openAttempts']*b['activity']['openAttemptWithoutSubmitFraction'] for b in bs)/max(1,act['openAttempts'])
        for k in ('fixedFavoredPayoff','oppositePayoff','terminalFloor','terminalBest','worstIntramarketFloor','winnerPnlPostHoc'):
            act[k]=sum(b['metrics'][k] for b in bs)
        act['winnerWinRatePosthoc']=float(np.mean([b['metrics']['winnerPnlPostHoc']>0 for b in bs]))
        act['worstWinnerPnlPosthoc']=min(b['metrics']['winnerPnlPostHoc'] for b in bs)
        act['winnerLossTailMeanWorstQuartilePosthoc']=float(np.mean(sorted(b['metrics']['winnerPnlPostHoc'] for b in bs)[:max(1,int(np.ceil(len(bs)/4)))]))
        act['fixedFavoredPerNotional']=act['fixedFavoredPayoff']/max(1e-12,act['buyNotional'])
        act['winnerPnlPerNotionalPosthoc']=act['winnerPnlPostHoc']/max(1e-12,act['buyNotional'])
        activity[side]=act
    activity['treatmentMinusControl']={k:activity['treatment'][k]-activity['control'][k] for k in activity['control']}
    activity['ratios']={k:activity['treatment'][k]/activity['control'][k] if activity['control'][k] else None for k in ('fills','submits','alternations','buyNotional','grossExposure','expandRepairExpandRoleCycleProxy')}
    # Algebraic same-notional normalization; NOT a physical scaled-order replay.
    decomposition=[]
    for r in rows:
        c,t=(r['branches'][b]['metrics'] for b in ('control','treatment'))
        n0,n1=c['buyNotional'],t['buyNotional']
        quality=n0*(t['fixedFavoredPayoff']/n1-c['fixedFavoredPayoff']/n0) if min(n0,n1)>0 else None
        activity_term=(n1-n0)*t['fixedFavoredPayoff']/n1 if n1>0 else None
        decomposition.append({'marketId':r['marketId'],'fixedFavoredDelta':t['fixedFavoredPayoff']-c['fixedFavoredPayoff'],'sameNotionalRateComponent':quality,'notionalChangeComponent':activity_term,'exactActivityMatch':all(c[k]==t[k] for k in ('fills','submits','alternations','buyNotional'))})
    coherence={}
    for mode in ('predictedPostSubmit','predictedPreSubmit'):
        checks=[]
        for r in rows:
            for branch,b in r['branches'].items():
                p=b[mode]
                for h in (3,5):
                    f,rep,ov=(p[f'{k}{h}s'] for k in ('fillQty','repairPayQty','overflowQty'))
                    checks.append({'marketId':r['marketId'],'branch':branch,'h':h,'fillAboveOrderQty':f>b['featuresPostSubmit']['qty']+1e-9,'repairAboveFill':rep>f+1e-9,'overflowAboveFill':ov>f+1e-9,'allocationSumMismatch':abs(rep+ov-f)>1e-8,'allocationResidual':rep+ov-f})
        coherence[mode]={'carrierHorizonRows':len(checks),**{k:sum(x[k] for x in checks) for k in ('fillAboveOrderQty','repairAboveFill','overflowAboveFill','allocationSumMismatch')},'witnesses':[x for x in checks if x['repairAboveFill'] or x['overflowAboveFill']][:4]}
    result={'version':'GPT6_PHASEB_WORLD_COUNTERFACTUAL_COMPACT_V1','sourceResult':a.result,'runtimeAuthority':False,'allCorrectnessPass':obj['allCorrectnessPass'],'markets':len(rows),'marketIds':[r['marketId'] for r in rows],'trainingMembership':dict(__import__('collections').Counter(r['trainingMembership'] for r in rows)),'metrics':metrics,'antiCollapse':activity,'frequencyBenefitDecomposition':decomposition,'predictedQuantityCoherence':coherence,'uncertainty':'2000 paired whole-market bootstrap resamples; consumed development selection, small sample and multiplicity not corrected','limitations':['Same-notional rate decomposition is algebra, not activity-matched HFT policy evidence','Role cycle and no-submit measures are proxies; eligible-opportunity HOLD is unobserved','No promotion or positive economics claim from model discrimination alone']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    corpus_path=Path(a.output).with_name(Path(a.output).stem+'_BRANCH_ROWS.jsonl')
    corpus=[]
    for pair in rows:
        for branch,b in pair['branches'].items():
            candidate='ctrl' if branch=='control' else 'trt'
            prefix=b['commonPrefixCandidates'][candidate]
            corpus.append({'marketId':pair['marketId'],'decisionMs':pair['t'],'branch':branch,'pairId':f"{pair['marketId']}:{pair['t']}",'trainingMembershipOfFrozenModel':pair['trainingMembership'],'prefixDigest':b['prefixDigest'],'correctnessPass':pair['correctnessPass'],'featuresCommonPrefix':{k:prefix[k] for k in b['featuresPreSubmit']},'featuresPreSubmit':b['featuresPreSubmit'],'postSubmitTelemetryNotDecisionInput':b['featuresPostSubmit'],'physicalLabels':b['observed'],'full5sObserved':b['full5sObserved'],'runtimeAuthority':False})
    with corpus_path.open('w',encoding='utf-8') as f:
        for row in corpus:f.write(json.dumps(row)+'\n')
    result['counterfactualCorpus']={'path':str(corpus_path),'rows':len(corpus),'markets':len(rows),'use':'Development/training coverage only after this evaluation; never reuse as unseen validation of a refit','forbiddenFeatures':'No winner, settlement, PnL or future execution in featuresCommonPrefix/featuresPreSubmit'}
    Path(a.output).write_text(json.dumps(result,indent=2))
    print(json.dumps({'markets':len(rows),'correct':obj['allCorrectnessPass'],'trainingMembership':result['trainingMembership'],'corpusRows':len(corpus),'summaryPath':a.output,'corpusPath':str(corpus_path)}))


if __name__=='__main__':
    main()
