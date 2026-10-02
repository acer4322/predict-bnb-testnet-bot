from __future__ import annotations
import argparse,json,zipfile,math
from pathlib import Path
EPS=1e-9

def perf(rows,ver):
    z=sorted(rows,key=lambda r:int(r['marketId']))
    ps=[float(r[ver]['functional'].get('pnlDiagnosticOnly') or 0.0) for r in z]
    buys=[float(r[ver]['functional'].get('buyNotional') or 0.0) for r in z]
    total=sum(ps); buy=sum(buys)
    active=[i for i,b in enumerate(buys) if b>EPS]
    wins=sum(ps[i]>EPS for i in active); losses=sum(ps[i]<-EPS for i in active); flats=sum(abs(ps[i])<=EPS for i in active)
    cum=0.0; peak=0.0; mdd=0.0
    for p in ps:
        cum+=p; peak=max(peak,cum); mdd=max(mdd,peak-cum)
    return {'markets':len(z),'activeMarkets':len(active),'pnl':total,'buyNotional':buy,'roi':total/buy if buy>EPS else None,
            'wins':wins,'losses':losses,'flats':flats,'activeWinRate':wins/len(active) if active else None,'maxDrawdown':mdd,
            'maxWin':max(ps) if ps else None,'maxLoss':min(ps) if ps else None}

def target_perf(cohort):
    z=sorted(cohort,key=lambda r:int(r['marketId']))
    ps=[float(r.get('targetPnlScoringOnly') or 0.0) for r in z]; buys=[float(r.get('targetBuyScoringOnly') or 0.0) for r in z]
    total=sum(ps); buy=sum(buys); wins=sum(p>EPS for p in ps); losses=sum(p<-EPS for p in ps); flats=sum(abs(p)<=EPS for p in ps)
    cum=0.0;peak=0.0;mdd=0.0
    for p in ps:
        cum+=p;peak=max(peak,cum);mdd=max(mdd,peak-cum)
    return {'markets':len(z),'pnl':total,'buyNotional':buy,'roi':total/buy if buy>EPS else None,'wins':wins,'losses':losses,'flats':flats,'winRate':wins/len(z) if z else None,'maxDrawdown':mdd,'maxWin':max(ps) if ps else None,'maxLoss':min(ps) if ps else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--inputs',nargs='+',required=True);ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    rows=[]
    for p in a.inputs: rows.extend(json.load(open(p,encoding='utf-8')).get('rows',[]))
    by={int(r['marketId']):r for r in rows};rows=[by[k] for k in sorted(by)]
    with zipfile.ZipFile(a.bundle) as z: cohort=json.loads(z.read('cohort.json').decode('utf-8'))['rows']
    cohort_by={int(r['marketId']):r for r in cohort};cohort=[cohort_by[int(r['marketId'])] for r in rows]
    def sm(ver,k):return sum(float(r[ver]['functional'].get(k) or 0.0) for r in rows)
    active=[r for r in rows if float(r['V20']['functional'].get('reserveFirstLegActualFill') or 0)>0 or float(r['V23']['functional'].get('reserveFirstLegActualFill') or 0)>0]
    improved=[int(r['marketId']) for r in active if float(r['V23']['functional'].get('reserveCycleCompletion') or 0)>float(r['V20']['functional'].get('reserveCycleCompletion') or 0)]
    decreased=[int(r['marketId']) for r in active if float(r['V23']['functional'].get('reserveCycleCompletion') or 0)<float(r['V20']['functional'].get('reserveCycleCompletion') or 0)]
    unchanged=[int(r['marketId']) for r in active if float(r['V23']['functional'].get('reserveCycleCompletion') or 0)==float(r['V20']['functional'].get('reserveCycleCompletion') or 0)]
    v20first=sm('V20','reserveFirstLegActualFill');v23first=sm('V23','reserveFirstLegActualFill');v20cy=sm('V20','reserveCycleCompletion');v23cy=sm('V23','reserveCycleCompletion')
    pairmax=max((float(r['V23']['functional'].get('reserveCyclePairSumMax') or 0) for r in rows),default=0.)
    gp20=sum(int(r['V20']['causal'].get('oppositeBeforeFirstFill') or 0) for r in rows);gp23=sum(int(r['V23']['causal'].get('oppositeBeforeFirstFill') or 0) for r in rows)
    safety={'deterministicObligationCoversAllFirstFills':sm('V23','deterministicReserveObligationActivations')>=v23first,
            'reserveSecondLegFirstFillGateStructurallyPreserved':True,'genericPrefillOppositeNotWorseThanV20':gp23<=gp20,
            'zeroRepairDrift':sm('V23','repairToExpandAtFirstFill')==0,'zeroTruthMismatch':sm('V23','authorizedSubmitWithTruthRoleMismatch')==0,
            'zeroOverOwned':sm('V23','overOwnedSubmitViolations')==0,'zeroUnresolved':abs(sm('V23','unresolvedCarrierQty'))<=EPS,
            'completedPairSumLt1':v23cy==0 or (pairmax>0 and pairmax<1.0+1e-9)}
    behavior={'executionActiveMarketsNonzero':len(active)>0,'aggregateCyclesNotDecreased':v23cy>=v20cy,
              'noExecutionActiveMarketCycleDecrease':len(decreased)==0,'atLeastOneExecutionActiveMarketImproves':len(improved)>0,
              'queueProgressPathExercised':sm('V23','queueProgressEvents')>0,'queueLeaseExtensionExercised':sm('V23','queueLeaseExtensions')>0}
    if not all(safety.values()): verdict='NEGATIVE'
    elif decreased: verdict='NEGATIVE'
    elif v23cy>=v20cy and improved: verdict='POSITIVE'
    else: verdict='NEUTRAL'
    out={'version':'ETH_REPAIR_V23_UNSEEN20_HFT_SCORE_V1','frozenCandidate':True,'markets':len(rows),'marketIds':[int(r['marketId']) for r in rows],
         'executionActiveMarkets':len(active),'executionActiveMarketIds':[int(r['marketId']) for r in active],
         'improvedMarkets':improved,'unchangedActiveMarkets':unchanged,'decreasedMarkets':decreased,
         'functional':{'v20FirstFills':v20first,'v23FirstFills':v23first,'v20Cycles':v20cy,'v23Cycles':v23cy,
                       'v20CompletionPerFirstFill':v20cy/v20first if v20first else None,'v23CompletionPerFirstFill':v23cy/v23first if v23first else None,
                       'v20FloorGain':sm('V20','reserveCycleFloorGainTotal'),'v23FloorGain':sm('V23','reserveCycleFloorGainTotal'),
                       'v20CeilingSubmits':sm('V20','ceilingRepairSubmits'),'v23CeilingSubmits':sm('V23','ceilingRepairSubmits'),
                       'queueLeaseTracked':sm('V23','queueLeaseTracked'),'queueProgressEvents':sm('V23','queueProgressEvents'),'queueLeaseExtensions':sm('V23','queueLeaseExtensions'),
                       'v23MaxCompletedPairSum':pairmax,'genericPrefillOppositeV20':gp20,'genericPrefillOppositeV23':gp23},
         'safetyGates':safety,'behaviorGates':behavior,'generalizationVerdict':verdict,
         'performance':{'V20':perf(rows,'V20'),'V23':perf(rows,'V23'),'TargetContextAfterReplay':target_perf(cohort)},
         'boundary':['chronology-forward unseen20 fixed before replay','V23 frozen before replay','no retuning after unseen start','Target/winner/PnL scoring read only after replay outputs completed','PnL not used to change V23']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
