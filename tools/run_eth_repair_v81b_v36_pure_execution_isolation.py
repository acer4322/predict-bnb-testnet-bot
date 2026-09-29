from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9

def sib(name,file):
    p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v80=sib('eth_v80_for_v81b','run_eth_repair_v80_modular_management_kernel.py');v38=v80.v38

class V80NoCoord(v80.V80ModularManagementKernel):
    def _score_state(self,t,after_kind): return None
class V80NoCoordNoV36(V80NoCoord):
    def _maybe_hard_active(self,t): return False

def saf(r):
    return sum(float(r.get(k) or 0.0) for k in ['authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if mids!=[1823894,1823897]: raise ValueError(mids)
    tmp=Path(tempfile.mkdtemp(prefix='eth_v81b_'));stop=threading.Event()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'V81B_V36_ISOLATION','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V81B_V36_ISOLATION_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for mid in mids:
            cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=V80NoCoordNoV36(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try: br=b.run_exam_v80(models,cr['winner'])
            finally: b.close()
            c=V80NoCoord(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try: rr=c.run_exam_v80(models,cr['winner'])
            finally: c.close()
            rows.append({'marketId':mid,'baseline':br,'candidate':rr})
            print(json.dumps({'marketId':mid,'baselineFloor':br.get('floor'),'candidateFloor':rr.get('floor'),'candidateV36Fill':rr.get('v36ActiveFillQty'),'candidateDeltas':rr.get('v36FloorFillDeltas'),'v44Fill':rr.get('v44ActualFillQty')},ensure_ascii=False),flush=True)
        per_market_nonworse=all(float(x['candidate'].get('floor') or 0)+EPS>=float(x['baseline'].get('floor') or 0) for x in rows)
        cfill=sum(float(x['candidate'].get('v36ActiveFillQty') or 0) for x in rows);bfill=sum(float(x['baseline'].get('v36ActiveFillQty') or 0) for x in rows);deltas=[float(d) for x in rows for d in (x['candidate'].get('v36FloorFillDeltas') or [])]
        gates={'candidateV36Exercised':cfill>EPS,'baselineV36Zero':bfill<=EPS,'candidateFloorNonWorseEachMarket':per_market_nonworse,'zeroHarmfulV36Delta':all(d>=-EPS for d in deltas),'zeroCandidateSafetyViolations':all(saf(x['candidate'])<=EPS for x in rows),'downstreamV44Disabled':all(float(x['candidate'].get('v44ActualFillQty') or 0)<=EPS for x in rows)}
        out={'version':'ETH_REPAIR_V81B_V36_PURE_EXECUTION_ISOLATION','date':'2026-09-03','researchOnly':True,'aggregate':{'baselineFloorSum':sum(float(x['baseline'].get('floor') or 0) for x in rows),'candidateFloorSum':sum(float(x['candidate'].get('floor') or 0) for x in rows),'candidateV36FillQty':cfill,'candidateFloorDeltas':deltas},'gates':gates,'decision':'CONFIRM_V36_EXECUTION_PRIMITIVE_CAUSALLY_USEFUL_UNDER_V80' if all(gates.values()) else 'V36_ISOLATION_NOT_CONFIRMED','rows':rows,'boundary':['diagnostic isolation only','_score_state disabled identically','no tuning','realistic HFT only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'aggregate':out['aggregate'],'gates':gates}),flush=True)
    finally: stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
