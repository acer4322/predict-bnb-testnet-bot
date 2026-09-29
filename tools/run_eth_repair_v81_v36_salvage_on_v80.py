from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9

def sib(name,file):
    p=Path(__file__).with_name(file)
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v80=sib('eth_v80_for_v81','run_eth_repair_v80_modular_management_kernel.py')
v38=v80.v38

class V80NoV36ActiveRepair(v80.V80ModularManagementKernel):
    """A/B control: same V80 authority, only disable V36 hard-active Repair submit path."""
    def _maybe_hard_active(self,t):
        return False


def safety(r):
    return {
        'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0.0),
        'overOwned':float(r.get('overOwnedSubmitViolations') or 0.0),
        'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0.0),
        'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0.0),
        'preBirthPaymentLeak':float(r.get('v70dPreBirthPaymentLeak') or 0.0),
        'duplicateGenerationDebt':float(r.get('v70dDuplicateGenerationDebt') or 0.0),
    }


def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if mids != [1823894,1823897]: raise ValueError(f'V81 fixed cohort mismatch: {mids}')
    tmp=Path(tempfile.mkdtemp(prefix='eth_v81_v36_salvage_')); stop=threading.Event()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'V81_V36_SALVAGE','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V81_V36_SALVAGE_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for mid in mids:
            cr=by[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            b=V80NoV36ActiveRepair(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try: br=b.run_exam_v80(models,cr['winner'])
            finally: b.close()
            c=v80.V80ModularManagementKernel(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try: rr=c.run_exam_v80(models,cr['winner'])
            finally: c.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baselineNoV36':br,'candidateV36':rr})
            print(json.dumps({'marketId':mid,
                'baseline':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'v36FillQty':br.get('v36ActiveFillQty'),'deficit':br.get('v80EconomicDeficitAmountAtEnd')},
                'candidate':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'v36Hard':rr.get('v36HardEventConfirmedCount'),'v36Submit':rr.get('v36ActiveSubmitCount'),'v36FillQty':rr.get('v36ActiveFillQty'),'v36FloorDeltas':rr.get('v36FloorFillDeltas'),'deficit':rr.get('v80EconomicDeficitAmountAtEnd')}}),flush=True)
        bfloor=sum(float(x['baselineNoV36'].get('floor') or 0.0) for x in rows);cfloor=sum(float(x['candidateV36'].get('floor') or 0.0) for x in rows)
        bfill=sum(float(x['baselineNoV36'].get('v36ActiveFillQty') or 0.0) for x in rows);cfill=sum(float(x['candidateV36'].get('v36ActiveFillQty') or 0.0) for x in rows)
        deltas=[float(d) for x in rows for d in (x['candidateV36'].get('v36FloorFillDeltas') or [])]
        s={k:sum(safety(x['candidateV36'])[k] for x in rows) for k in safety(rows[0]['candidateV36'])}
        maxresp=max(int(x['candidateV36'].get('v70gMaxResponsibilitiesPerGeneration') or 0) for x in rows)
        legacy_supp=sum(int(x['candidateV36'].get('v80LegacyStructuralCompletionSuppressed') or 0) for x in rows)
        gates={
            'bothMarketsComplete':len(rows)==2,
            'candidateV36ActualFillExercised':cfill>EPS,
            'baselineV36ActualFillZero':bfill<=EPS,
            'candidateFloorNotWorse':cfloor+EPS>=bfloor,
            'zeroHarmfulV36FillDelta':all(d>=-EPS for d in deltas),
            'zeroTruthMismatch':s['truthMismatch']==0,
            'zeroOverOwned':s['overOwned']==0,
            'zeroResponsibilityOverfill':s['responsibilityOverfill']<=EPS,
            'zeroRepairDrift':s['repairDrift']==0,
            'zeroPreBirthPaymentLeak':s['preBirthPaymentLeak']<=EPS,
            'zeroDuplicateGenerationDebt':s['duplicateGenerationDebt']<=EPS,
            'oneResponsibilityPerGeneration':maxresp<=1,
            'v80CompletionFirewallPresent':legacy_supp>=0,
        }
        decision='SALVAGE_V36_AS_V80_REPAIR_EXECUTION_MODULE_AUTHORIZE_BROADER_HFT' if all(gates.values()) else 'DO_NOT_SALVAGE_V36_AS_IS_DIAGNOSE'
        out={'version':'ETH_REPAIR_V81_V36_SALVAGE_ON_V80','date':'2026-09-03','researchOnly':True,'fixedCohort':mids,'ab':'same V80 economic authority; only V36 hard-active Repair disabled vs enabled','aggregate':{'markets':2,'baselineFloorSum':bfloor,'candidateFloorSum':cfloor,'floorDelta':cfloor-bfloor,'baselineV36FillQty':bfill,'candidateV36FillQty':cfill,'candidateV36FloorFillDeltas':deltas,'candidateLegacyCompletionSuppressions':legacy_supp},'safety':s,'gates':gates,'decision':decision,'rows':rows,'boundary':['No numeric tuning','No winner/PnL runtime input','Realistic HFT/Predict Tape only','No 8781','Only V36 Repair execution differs between A/B']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'aggregate':out['aggregate'],'gates':gates,'safety':s}),flush=True)
    finally:
        stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
