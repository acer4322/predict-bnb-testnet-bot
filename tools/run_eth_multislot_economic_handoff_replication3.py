from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys,time,threading
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9;pe=base.pe
DEFAULT_IDS=[1945898,1946653,1946683]

def slim(r):return base.slim(r)
def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',default=','.join(map(str,DEFAULT_IDS)));ap.add_argument('--baseline-json',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    ids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='economic_handoff_rep3_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'ECONOMIC_HANDOFF_REPLICATION3','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ECONOMIC_HANDOFF_REPLICATION3_START','marketIds':ids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};bj=json.load(open(a.baseline_json,encoding='utf-8'));bmap={int(x['marketId']):x for x in bj['rows']}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for mid in ids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=pe.make(mod.EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=s.run_locked(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
            finally:s.close()
            ss=pe.safety(r);safe=all(float(x or 0)<=EPS for x in ss.values());occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r);b=bmap[mid]
            ex=bool(int(r.get('economicCancelAllows') or 0)>0 or int(r.get('economicCancelBlocks') or 0)>0 or int(r.get('partitionApplied') or 0)>0)
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'baseline':{'pnlDiagnosticOnly':float(b['pnlDiagnosticOnly']),'floor':float(b['floor']),'fills':int(b['fills']),'rounds':int(b['rounds']),'repairParentBirths':int(b['repairParentBirths']),'repairParentCompletions':int(b['repairParentCompletions'])},'candidate':m,'delta':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-float(b['pnlDiagnosticOnly']),'floor':m['floor']-float(b['floor']),'fills':m['fills']-int(b['fills']),'rounds':m['rounds']-int(b['rounds'])},'moduleExercised':ex,'partitionApplied':int(r.get('partitionApplied') or 0),'economicCancelAllows':int(r.get('economicCancelAllows') or 0),'economicCancelBlocks':int(r.get('economicCancelBlocks') or 0),'leasedReplacementSubmits':int(r.get('leasedReplacementSubmits') or 0),'rollingFillQty':float(r.get('repeatedRollingFillQty') or 0.0),'handoffLeaseActiveBlocks':int(r.get('handoffLeaseActiveBlocks') or 0),'safety':ss,'safetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':bool(occok),'allocationParents':parents,'economicEvents':(r.get('economicHandoffLeaseEvents') or [])[:120]}
            rows.append(row);print(json.dumps({'marketId':mid,'baselinePnl':row['baseline']['pnlDiagnosticOnly'],'candidatePnl':m['pnlDiagnosticOnly'],'deltaPnl':row['delta']['pnlDiagnosticOnly'],'partitionApplied':row['partitionApplied'],'allows':row['economicCancelAllows'],'blocks':row['economicCancelBlocks'],'leasedSubmits':row['leasedReplacementSubmits'],'rollingFillQty':row['rollingFillQty'],'safetyZero':safe,'cons':cons,'bound':bound,'occ':occok},ensure_ascii=False),flush=True)
        allsafe=all(x['safetyZero'] and x['allocationConservation'] and x['allocationParentDebtBounded'] and x['executionOccupancyBounded'] for x in rows);exrows=[x for x in rows if x['moduleExercised']];sum_base=sum(x['baseline']['pnlDiagnosticOnly'] for x in rows);sum_cand=sum(x['candidate']['pnlDiagnosticOnly'] for x in rows);worse=sum(x['delta']['pnlDiagnosticOnly']<-1e-7 for x in exrows);better=sum(x['delta']['pnlDiagnosticOnly']>1e-7 for x in exrows)
        agg={'markets':len(rows),'moduleExercisedMarkets':len(exrows),'safetyAllPass':allsafe,'baselineTotalPnl':sum_base,'candidateTotalPnl':sum_cand,'deltaTotalPnl':sum_cand-sum_base,'exercisedBetterMarkets':better,'exercisedWorseMarkets':worse,'totalEconomicAllows':sum(x['economicCancelAllows'] for x in rows),'totalEconomicBlocks':sum(x['economicCancelBlocks'] for x in rows),'totalLeasedReplacementSubmits':sum(x['leasedReplacementSubmits'] for x in rows),'totalRollingFillQty':sum(x['rollingFillQty'] for x in rows)}
        if not allsafe:decision='REJECT_REPLICATION3_SAFETY_ACCOUNTING'
        elif len(exrows)==0:decision='REPLICATION3_NOT_EXERCISED'
        elif worse==0:decision='REPLICATION3_FUNCTIONAL_NO_EXERCISED_REGRESSION'
        else:decision='REPLICATION3_ECONOMIC_MIXED_DIAGNOSE'
        out={'version':'ETH_MULTISLOT_ECONOMIC_HANDOFF_REPLICATION3_V1','date':'2026-09-05','researchOnly':True,'marketIds':ids,'decision':decision,'aggregate':agg,'rows':rows,'boundary':['three consumed latest24 markets chosen by distinct outcome class','candidate-only economic-handoff control mode vs frozen latest24 baseline','V16 preflight before management cancel','approved replacement price leased through cancel terminal','future inadmissible reanchor keeps queue','no threshold/model/qty tuning','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
