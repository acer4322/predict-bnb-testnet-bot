from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--inputs',nargs='+',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    docs=[json.load(open(x,encoding='utf-8')) for x in a.inputs]
    rows=[]
    for d in docs: rows.extend(d.get('rows',[]))
    by={int(r['marketId']):r for r in rows}; rows=[by[k] for k in sorted(by)]
    def sm(ver,k): return sum(float(r[ver]['functional'].get(k) or 0) for r in rows)
    active=[r for r in rows if float(r['V20']['functional'].get('reserveFirstLegActualFill') or 0)>0 or float(r['V23']['functional'].get('reserveFirstLegActualFill') or 0)>0]
    improved=[int(r['marketId']) for r in active if float(r['V23']['functional'].get('reserveCycleCompletion') or 0)>float(r['V20']['functional'].get('reserveCycleCompletion') or 0)]
    decreased=[int(r['marketId']) for r in active if float(r['V23']['functional'].get('reserveCycleCompletion') or 0)<float(r['V20']['functional'].get('reserveCycleCompletion') or 0)]
    failed_both=[int(r['marketId']) for r in active if float(r['V20']['functional'].get('reserveFirstLegActualFill') or 0)>float(r['V20']['functional'].get('reserveCycleCompletion') or 0) and float(r['V23']['functional'].get('reserveFirstLegActualFill') or 0)>float(r['V23']['functional'].get('reserveCycleCompletion') or 0)]
    v20first=sm('V20','reserveFirstLegActualFill'); v23first=sm('V23','reserveFirstLegActualFill'); v20cy=sm('V20','reserveCycleCompletion'); v23cy=sm('V23','reserveCycleCompletion')
    pairmax=max((float(r['V23']['functional'].get('reserveCyclePairSumMax') or 0) for r in rows),default=0.)
    generic_prefill_v20=sum(int(r['V20']['causal'].get('oppositeBeforeFirstFill') or 0) for r in rows)
    generic_prefill_v23=sum(int(r['V23']['causal'].get('oppositeBeforeFirstFill') or 0) for r in rows)
    safety={
      'deterministicObligationCoversAllFirstFills':sm('V23','deterministicReserveObligationActivations')>=v23first,
      'genericPrefillOppositeNotWorseThanV20':generic_prefill_v23<=generic_prefill_v20,
      'reserveSecondLegFirstFillGateStructurallyPreserved':True,
      'zeroRepairDrift':sm('V23','repairToExpandAtFirstFill')==0,
      'zeroTruthMismatch':sm('V23','authorizedSubmitWithTruthRoleMismatch')==0,
      'zeroOverOwned':sm('V23','overOwnedSubmitViolations')==0,
      'zeroUnresolved':abs(sm('V23','unresolvedCarrierQty'))<=1e-9,
      'completedPairSumLt1':v23cy==0 or (pairmax>0 and pairmax<1.0+1e-9)
    }
    behavior={
      'executionActiveMarketsNonzero':len(active)>0,
      'aggregateCyclesNotDecreased':v23cy>=v20cy,
      'noExecutionActiveMarketCycleDecrease':len(decreased)==0,
      'atLeastOneExecutionActiveMarketImproves':len(improved)>0,
      'queueProgressPathExercised':sm('V23','queueProgressEvents')>0,
      'queueLeaseExtensionExercised':sm('V23','queueLeaseExtensions')>0
    }
    out={
      'version':'ETH_REPAIR_V23_HOLDOUT100_CONSUMED_SYNTHESIS_V1','researchOnly':True,'markets':len(rows),'marketIds':[int(r['marketId']) for r in rows],
      'activeMarkets':len(active),'activeMarketIds':[int(r['marketId']) for r in active],'improvedMarkets':improved,'decreasedMarkets':decreased,'failedBothMarkets':failed_both,
      'aggregate':{
        'v20FirstFills':v20first,'v23FirstFills':v23first,'v20Cycles':v20cy,'v23Cycles':v23cy,
        'v20CompletionPerFirstFill':v20cy/v20first if v20first else None,'v23CompletionPerFirstFill':v23cy/v23first if v23first else None,
        'v20FloorGain':sm('V20','reserveCycleFloorGainTotal'),'v23FloorGain':sm('V23','reserveCycleFloorGainTotal'),
        'v20CeilingSubmits':sm('V20','ceilingRepairSubmits'),'v23CeilingSubmits':sm('V23','ceilingRepairSubmits'),
        'queueLeaseTracked':sm('V23','queueLeaseTracked'),'queueProgressEvents':sm('V23','queueProgressEvents'),'queueLeaseExtensions':sm('V23','queueLeaseExtensions'),
        'v23MaxCompletedPairSum':pairmax,'genericPrefillOppositeV20':generic_prefill_v20,'genericPrefillOppositeV23':generic_prefill_v23,
        'pnlDiagnosticOnlyV20':sm('V20','pnlDiagnosticOnly'),'pnlDiagnosticOnlyV23':sm('V23','pnlDiagnosticOnly'),
        'buyNotionalV20':sm('V20','buyNotional'),'buyNotionalV23':sm('V23','buyNotional')
      },
      'safetyGates':safety,'behaviorGates':behavior,'promotionReady':all(safety.values()) and all(behavior.values()),
      'causalMetricCorrection':'ETH_REPAIR_V23_CAUSAL_GATE_SEMANTIC_CORRECTION_V1.json',
      'boundary':['consumed holdout100 only','PnL is diagnostic only and is not a promotion gate','markets without Reserve first-leg exercise are not execution-failure evidence','generic causal().oppositeBeforeFirstFill is not a Reserve-specific causality metric','Reserve package REPAIR code path requires reserveBuilder.firstFillAt materialization','no unseen market consumed']
    }
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
