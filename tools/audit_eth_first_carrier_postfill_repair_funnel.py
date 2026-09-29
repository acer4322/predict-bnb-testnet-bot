from __future__ import annotations
import argparse, json, shutil, sys, tempfile, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path: sys.path.insert(0,str(ROOT/"tools"))

import tools.run_eth_first_carrier_active_relay_smoke as relay
import tools.audit_eth_action_liveness_funnel_smoke4 as liveaudit

pg=relay.pg; pe=pg.pe

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='first_carrier_postfill_funnel_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); by={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for mid in mids:
            cr=by[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'; sim=pe.make(relay.FirstCarrierActiveRelayHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:
                r=sim.run_first_carrier_relay(models,cr['winner']); cons,bound,parents=pe.alloc(sim,r); inv=liveaudit.compact_event_inventory(sim,r)
            finally: sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'pnl':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'firstCarrierActiveFillQty':float(r.get('firstCarrierActiveFillQty') or 0),'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0),'allocationParents':parents,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'safety':pe.safety(r),'eventInventory':inv})
        out={'version':'ETH_FIRST_CARRIER_POSTFILL_REPAIR_FUNNEL_AUDIT_V1','date':'2026-09-05','researchOnly':True,'behaviorChange':False,'marketIds':mids,'rows':rows,'boundary':['behavior-inert matched anatomy after the same first-carrier Active relay capability','winner post-hoc only','no threshold/price/qty mutation','no Target runtime input; no dream fill; no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2,default=liveaudit._json_default),encoding='utf-8')
        for x in rows:
            print(json.dumps({'marketId':x['marketId'],'pnl':x['pnl'],'fills':x['fills'],'rounds':x['rounds'],'parentBirths':x['repairParentBirths'],'parentCompletions':x['repairParentCompletions'],'parallelRepairSubmits':x['parallelRepairSubmits'],'parallelRepairActiveFillQty':x['parallelRepairActiveFillQty'],'eventLists':sorted(x['eventInventory'])},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
