from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,time
from pathlib import Path
import joblib
import tools.run_eth_frontier_bounded_passive_repair_ab_1945869 as ab

pe=ab.pe
BASELINE={'fills':3,'pnl':-1.3333333333333333,'floor':-1.3333333333333333,'repairPaid':1.6666666666666667,'remainingDebt':2.7564102564102564,'rounds':0,'safe':True,'allocationConservation':True,'allocationBounded':True,'occupancyBounded':True}

def dump_partial(path,state,extra=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    row={'version':'ETH_FRONTIER_BOUNDED_PASSIVE_REPAIR_CANDIDATE_ONLY_1945869_V1','date':'2026-09-05','marketId':1945869,'state':state,'ts':time.time(),'baselineFrozen':BASELINE}
    if extra: row.update(extra)
    path.write_text(json.dumps(row,indent=2),encoding='utf-8')

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--output',required=True)
    a=ap.parse_args();mid=int(a.market_id)
    if mid!=1945869: raise ValueError(mid)
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    partial=outdir/'partial.json'
    dump_partial(partial,'STARTED')
    tmp=Path(tempfile.mkdtemp(prefix='frontier_candidate_only_1945869_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        dump_partial(partial,'BUNDLE_READY',{'winnerPostHocOnly':cr['winner']})
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        dump_partial(partial,'RUNTIME_READY')
        tape=tmp/'tapes'/f'{mid}.json.xz'
        s=pe.make(ab.FrontierBoundedPassiveRepairHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:
            r=s.run_first_carrier_relay(models,cr['winner'])
            m=ab.summarise(s,r)
            raw={k:v for k,v in m.items() if k not in ('physical','reservationReasons','frontierPlacementEvents','allocationParents')}
            dump_partial(partial,'CANDIDATE_COMPLETE',{'candidateCompact':raw})
        finally:
            s.close()
        gates={'candidateSafe':bool(m['safe']),'placementPolicyExercised':int(m['frontierPlacementChanges'])>0,
               'moreRepairPaymentOrRound':m['repairPaid']>BASELINE['repairPaid']+1e-9 or m['rounds']>BASELINE['rounds'],
               'floorNonWorse':m['floor']>=BASELINE['floor']-1e-7,'pnlImproves':m['pnl']>BASELINE['pnl']+1e-9,
               'allocationConservation':bool(m['allocationConservation']),'allocationBounded':bool(m['allocationBounded']),'occupancyBounded':bool(m['occupancyBounded'])}
        if not all([gates['candidateSafe'],gates['allocationConservation'],gates['allocationBounded'],gates['occupancyBounded']]): decision='REJECT_FRONTIER_BOUNDED_PASSIVE_SAFETY'
        elif not gates['placementPolicyExercised']: decision='NO_FRONTIER_BOUNDED_PASSIVE_REACHABILITY'
        elif gates['moreRepairPaymentOrRound'] and gates['floorNonWorse'] and gates['pnlImproves']: decision='KEEP_FRONTIER_BOUNDED_PASSIVE_REPAIR_FOR_ONE_REPLICATION'
        else: decision='FRONTIER_BOUNDED_PASSIVE_FUNCTIONAL_BUT_ECONOMICALLY_INCOMPLETE'
        out={'version':'ETH_FRONTIER_BOUNDED_PASSIVE_REPAIR_CANDIDATE_ONLY_1945869_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'baselineFrozen':BASELINE,'candidate':m,'gates':gates,'boundary':['candidate only; frozen baseline imported from ETH_GENERATION_DEBT_X_ACTIVE_REARM_2X2_1945869','changes only newly submitted Passive Repair child price','qty/debt/role/objective/ownership/Active frozen','target price maker-safe <= min(current best bid,inherited economic ceiling)','realistic HFT; no dream fill; no 8781']}
        op=outdir/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        dump_partial(partial,'DONE',{'decision':decision,'candidateCompact':{k:m.get(k) for k in ['fills','pnl','floor','repairPaid','remainingDebt','rounds','frontierPlacementChanges','safe']},'gates':gates})
        print(json.dumps({'ok':True,'decision':decision,'baseline':BASELINE,'candidate':{k:m.get(k) for k in ['fills','pnl','floor','repairPaid','remainingDebt','rounds','frontierPlacementChanges','safe']},'gates':gates},ensure_ascii=False),flush=True)
    finally:
        shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
