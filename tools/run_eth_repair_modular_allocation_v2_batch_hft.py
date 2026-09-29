from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib
from pathlib import Path
import run_eth_repair_modular_allocation_v2_generic_hft as g

EPS=1e-9
v38=g.v38;v80=g.v80;mod=g.mod

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    out_path=Path(a.output); out_path.parent.mkdir(parents=True,exist_ok=True)
    details_dir=out_path.parent/'details'; details_dir.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='eth_alloc_v2_batch_'))
    stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'MODULAR_ALLOCATION_V2_BATCH','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start()
    print(json.dumps({'heartbeat':'MODULAR_ALLOCATION_V2_BATCH_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        def mk(cls,tape):
            return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        for idx,mid in enumerate(mids,1):
            cr=cohort[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            b=mk(mod.ModularRecursiveRepairExecutionV2,tape)
            try: br=b.run_modular_v2(models,cr['winner'])
            finally: b.close()
            c=mk(g.ModularAllocationLedgerV2,tape)
            try: rr=c.run_allocation_v2(models,cr['winner'])
            finally: c.close()
            ss=g.safety(rr)
            cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
            parents=rr.get('allocationV2Parents') or {}
            parent_ok=all(float(x.get('repairPaid') or 0)<=float(x.get('initialDebt') or 0)+1e-7 and float(x.get('remainingDebt') or 0)>=-1e-9 for x in parents.values())
            gates={'allocationModuleActive':rr.get('allocationLedgerV2')=='shared_parent_debt_repair_first_allocation_v2','routerStillActive':(rr.get('modularRepairExecutionProfile') or {}).get('repairExecution')=='recursive_composite_single_disconnect_execution_v1','physicalAllocationConservation':cons,'parentRepairNeverExceedsInitialDebt':parent_ok,'zeroTruthMismatch':ss['truthMismatch']==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS}
            cand={'floor':float(rr.get('floor') or 0.0),'pnl':float(rr.get('pnlDiagnosticOnly') or 0.0),'fills':int(rr.get('actualFillEvents') or 0),'submits':int(rr.get('submits') or 0),'activeCompositeSubmits':int(rr.get('modularActiveCompositeSubmits') or 0),'activeCompositeFillQty':float(rr.get('modularActiveCompositeFillQty') or 0.0),'overflowPaid':float(rr.get('v84OverflowPaidQty') or 0.0),'overflowRemaining':float(rr.get('v84OverflowRemainingQty') or 0.0),'repairParentBirths':int(rr.get('repairParentBirths') or 0),'shareSettlements':int(rr.get('v80ShareRepairSettlements') or 0),'terminalUnresolvedParents':int(rr.get('v34ParentTerminalUnresolved') or 0),'repairFillEvents':int(rr.get('v38PassiveRepairFillEvents') or 0),'makerFillEvents':int(rr.get('v53MakerFillEvents') or 0),'activeFillEvents':int(rr.get('v53ActiveFillEvents') or 0)}
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'gates':gates,'safety':ss,'candidate':cand,'baseline':{'floor':float(br.get('floor') or 0.0),'pnl':float(br.get('pnlDiagnosticOnly') or 0.0),'fills':int(br.get('actualFillEvents') or 0),'legacyRepairDrift':int(br.get('repairToExpandAtFirstFill') or 0)}}
            rows.append(row)
            json.dump({'marketId':mid,'winnerPostHocOnly':cr['winner'],'gates':gates,'safety':ss,'baseline':br,'candidate':rr},open(details_dir/f'{mid}.json','w',encoding='utf-8'),indent=2)
            active=[x for x in rows if x['candidate']['fills']>0]
            progress={'completed':idx,'total':len(mids),'lastMarket':mid,'aggregatePnl':sum(x['candidate']['pnl'] for x in rows),'aggregateFloor':sum(x['candidate']['floor'] for x in rows),'activeMarkets':len(active),'positivePnlMarkets':sum(x['candidate']['pnl']>EPS for x in rows),'allSafetyPass':all(all(x['gates'].values()) for x in rows),'rows':rows}
            json.dump(progress,open(out_path.parent/'progress.json','w',encoding='utf-8'),indent=2)
            print(json.dumps({'marketId':mid,'idx':idx,'of':len(mids),'pnl':cand['pnl'],'floor':cand['floor'],'fills':cand['fills'],'repairParents':cand['repairParentBirths'],'settlements':cand['shareSettlements'],'activeComposite':cand['activeCompositeSubmits'],'terminalUnresolved':cand['terminalUnresolvedParents'],'safetyPass':all(gates.values()),'aggPnl':progress['aggregatePnl'],'activeMarkets':len(active)},ensure_ascii=False),flush=True)
        active=[x for x in rows if x['candidate']['fills']>0]
        agg={'markets':len(rows),'activeMarkets':len(active),'activeMarketRate':len(active)/len(rows) if rows else 0.0,'positivePnlMarkets':sum(x['candidate']['pnl']>EPS for x in rows),'positivePnlRateAll':sum(x['candidate']['pnl']>EPS for x in rows)/len(rows) if rows else 0.0,'winRateTraded':sum(x['candidate']['pnl']>EPS for x in active)/len(active) if active else None,'aggregatePnl':sum(x['candidate']['pnl'] for x in rows),'aggregateFloor':sum(x['candidate']['floor'] for x in rows),'minPnl':min((x['candidate']['pnl'] for x in rows),default=0.0),'maxPnl':max((x['candidate']['pnl'] for x in rows),default=0.0),'totalFills':sum(x['candidate']['fills'] for x in rows),'repairParentBirths':sum(x['candidate']['repairParentBirths'] for x in rows),'shareSettlements':sum(x['candidate']['shareSettlements'] for x in rows),'activeCompositeSubmits':sum(x['candidate']['activeCompositeSubmits'] for x in rows),'terminalUnresolvedParents':sum(x['candidate']['terminalUnresolvedParents'] for x in rows),'allSafetyPass':all(all(x['gates'].values()) for x in rows)}
        out={'version':'ETH_REPAIR_MODULAR_ALLOCATION_LEDGER_V2_BATCH_HFT','date':'2026-09-03','researchOnly':True,'marketIds':mids,'aggregate':agg,'rows':rows,'boundary':['same frozen strategy as generic AllocationLedger V2 HFT','models loaded once only; harness optimization','winner post-hoc scoring only','no Target runtime input','no tuning','realistic HFT only','no 8781']}
        json.dump(out,open(out_path,'w',encoding='utf-8'),indent=2)
        print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
    finally:
        stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
