from __future__ import annotations
import argparse,json,math,zipfile
from pathlib import Path
import numpy as np
EPS=1e-9
FOLDS=[(0,40,40,60),(0,60,60,80),(0,80,80,100)]

def fs(floor,best):return float(best)+min(float(floor),0.0)
def pnl(tm,winner):return float(tm['upQty'] if winner=='UP' else tm['downQty'])-float(tm['buyNotional'])
def mdd(xs):
    c=0.0;peak=0.0;dd=0.0
    for x in xs:
        c+=float(x);peak=max(peak,c);dd=max(dd,peak-c)
    return dd

def worst_mean(xs,frac=.2):
    a=sorted(float(x) for x in xs);n=max(1,int(math.ceil(len(a)*frac)));return sum(a[:n])/n

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--forks',default='data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_EXACT_FORK_H100_MERGED_V1_20260907.json');ap.add_argument('--bundle',default='data/research/r4_v0/p0_provenance_v1/v16_consumed_holdout100_bundle.zip');ap.add_argument('--output',default='data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_ROLE_SWITCH_FILL_WEIGHTED_SELECTOR_V1_20260907.json');a=ap.parse_args()
    d=json.loads(Path(a.forks).read_text(encoding='utf-8'));rows=sorted(d['rows'],key=lambda r:(int(r['marketId']),int(r['t'])))
    with zipfile.ZipFile(a.bundle) as z:
        co={int(x['marketId']):str(x['winner']).upper() for x in json.loads(z.read('cohort.json'))['rows']}
    oos=[];folds=[]
    for fi,(tr0,tr1,te0,te1) in enumerate(FOLDS,1):
        tr=rows[tr0:tr1];te=rows[te0:te1]
        pR=sum((r['branchResolution']['NEXT_REPAIR'] or {}).get('kind')=='FILL' for r in tr)/len(tr)
        pE=sum((r['branchResolution']['NEXT_REEXPAND'] or {}).get('kind')=='FILL' for r in tr)/len(tr)
        folds.append({'fold':fi,'trainRange':[tr0,tr1],'testRange':[te0,te1],'pFillRepair':pR,'pFillReexpand':pE})
        for r in te:
            s=r['stateSpec'];curF=float(s['floor']);curB=float(s['best']);cur=fs(curF,curB)
            # Candidate full-fill economic geometry is strict-past deterministic and already frozen in the scan.
            def full_score(kind):
                if kind=='REPAIR':
                    c=s['repairCandidate'];side=str(s['weakSide']);p=float(c['price']);q=float(c['qty'])
                else:
                    c=s['expandCandidate'];side=str(s['expandSide']);p=float(c['price']);q=float(c['qty'])
                inv=dict(s['inventory']);cost=float(s['cost'])+p*q;inv[side]=float(inv[side])+q
                pu=float(inv['UP'])-cost;pd=float(inv['DOWN'])-cost
                return fs(min(pu,pd),max(pu,pd)),{'floor':min(pu,pd),'best':max(pu,pd),'side':side,'price':p,'qty':q}
            sr,gr=full_score('REPAIR');se,ge=full_score('REEXPAND')
            eR=pR*(sr-cur);eE=pE*(se-cur)
            native=str(s['nativeClass']);choice='REEXPAND' if eE>eR+EPS else ('REPAIR' if eR>eE+EPS else native)
            b='NEXT_REEXPAND' if choice=='REEXPAND' else 'NEXT_REPAIR';nb='NATIVE'
            w=co[int(r['marketId'])];tp=r['terminalMetrics'];bp=pnl(tp[nb],w);cp=pnl(tp[b],w)
            oos.append({'fold':fi,'marketId':int(r['marketId']),'winnerPostHoc':w,'nativeClass':native,'choice':choice,'changed':choice!=native,
                        'trainPFillRepair':pR,'trainPFillReexpand':pE,'currentStructureScore':cur,'fullFillStructureScoreRepair':sr,'fullFillStructureScoreReexpand':se,
                        'expectedLocalStructureDeltaRepair':eR,'expectedLocalStructureDeltaReexpand':eE,'repairGeometry':gr,'reexpandGeometry':ge,
                        'baselinePnl':bp,'candidatePnl':cp,'deltaPnl':cp-bp,
                        'baselineFloor':float(tp[nb]['floor']),'candidateFloor':float(tp[b]['floor']),'deltaFloor':float(tp[b]['floor'])-float(tp[nb]['floor']),
                        'baselineBest':float(tp[nb]['best']),'candidateBest':float(tp[b]['best']),'deltaBest':float(tp[b]['best'])-float(tp[nb]['best']),
                        'baselineFills':int(tp[nb]['fills']),'candidateFills':int(tp[b]['fills']),'deltaFills':int(tp[b]['fills'])-int(tp[nb]['fills'])})
    aff=[r for r in oos if r['changed']];deltas=[r['deltaPnl'] for r in oos];ad=[r['deltaPnl'] for r in aff]
    bp=[r['baselinePnl'] for r in oos];cp=[r['candidatePnl'] for r in oos]
    imp=sum(x>EPS for x in ad);wor=sum(x<-EPS for x in ad);tie=len(ad)-imp-wor
    base_worst=min(bp);cand_worst=min(cp);base_w20=worst_mean(bp);cand_w20=worst_mean(cp);base_dd=mdd(bp);cand_dd=mdd(cp)
    gates={
      'affectedMarketImprovementRate70': bool(len(aff)>0 and imp/len(aff)>=0.70),
      'totalPnlImproves':bool(sum(cp)>sum(bp)+EPS),
      'averagePnlImproves':bool(np.mean(cp)>np.mean(bp)+EPS),
      'winRateNonWorse':bool(sum(x>EPS for x in cp)>=sum(x>EPS for x in bp)),
      'worstMarketWithin10Pct':bool(cand_worst>=base_worst-0.10*abs(base_worst)-EPS),
      'worst20MeanWithin10Pct':bool(cand_w20>=base_w20-0.10*abs(base_w20)-EPS),
      'maxDrawdownWithin110Pct':bool(cand_dd<=1.10*base_dd+EPS),
      'fillActivityAtLeast80Pct':bool(sum(r['candidateFills'] for r in oos)>=0.80*sum(r['baselineFills'] for r in oos)-EPS),
    }
    summary={'oosMarkets':len(oos),'affectedMarkets':len(aff),'affectedImproved':imp,'affectedWorsened':wor,'affectedTied':tie,
             'affectedImprovementRate':imp/len(aff) if aff else None,'totalBaselinePnl':sum(bp),'totalCandidatePnl':sum(cp),'totalDeltaPnl':sum(deltas),
             'baselineWinRate':sum(x>EPS for x in bp)/len(bp),'candidateWinRate':sum(x>EPS for x in cp)/len(cp),
             'baselineWorstPnl':base_worst,'candidateWorstPnl':cand_worst,'baselineWorst20Mean':base_w20,'candidateWorst20Mean':cand_w20,
             'baselineMaxDrawdown':base_dd,'candidateMaxDrawdown':cand_dd,'baselineFills':sum(r['baselineFills'] for r in oos),'candidateFills':sum(r['candidateFills'] for r in oos),
             'deltaPnlP10':float(np.quantile(deltas,.1)),'deltaPnlMedian':float(np.median(deltas)),'deltaPnlP90':float(np.quantile(deltas,.9))}
    out={'version':'MANAGEMENT_MAINLINE_ROLE_SWITCH_FILL_WEIGHTED_SELECTOR_V1_20260907','researchOnly':True,'runtimeAuthority':False,'sourceForks':a.forks,'promotionGate':'MANAGEMENT_PROMOTION_GATE_70_30_V1_20260907','selector':'choose action maximizing TRAIN-only action-class fill realization rate × deterministic full-fill delta of FavorableStructureScore = Best + min(Floor,0); exact tie preserves native','folds':folds,'summary':summary,'gates':gates,'all70_30GatesPass':all(gates.values()),'rows':oos,
         'boundary':['single intervention at first eligible state per market; suffix current V3B','fill rates estimated only from earlier chronological exact-fork markets in each fold','candidate economics strict-past; no winner/Target/future features','winner posthoc evaluation only','no threshold/model sweep','development-only even if gates pass; repeated whole-market controller requires separate HFT','no NEW24-B/no dream fill/no 8781']}
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'summary':summary,'gates':gates,'pass':out['all70_30GatesPass'],'folds':folds},ensure_ascii=False))
if __name__=='__main__':main()
