from __future__ import annotations
import argparse,json,os,shutil,tempfile,threading,time,zipfile
from pathlib import Path
import sys,importlib,importlib.util
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
import joblib

def load(fullname,filename):
    print(f'IMPORT_STAGE {fullname} START',flush=True)
    try:
        m=importlib.import_module(fullname); print(f'IMPORT_STAGE {fullname} PASS project',flush=True); return m
    except ImportError:
        p=Path(__file__).with_name(filename); spec=importlib.util.spec_from_file_location(fullname,p)
        if spec is None or spec.loader is None: raise ImportError(p)
        m=importlib.util.module_from_spec(spec); sys.modules[fullname]=m; spec.loader.exec_module(m); print(f'IMPORT_STAGE {fullname} PASS staged',flush=True); return m
load('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
load('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
load('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
load('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
load('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
load('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg=load('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py'); pe=pg.pe

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output)
    tmp=Path(tempfile.mkdtemp(prefix='initial_dir_latest8_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'INITIAL_DIRECTION_AUDIT','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'INITIAL_DIRECTION_AUDIT_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for i,mid in enumerate(mids,1):
            cr=by[mid];sim=pe.make(pg.ProspectiveGuardParentOccupancyHFT,tmp/'tapes'/f'{mid}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
            try:
                r=sim.run_guard(models,cr['winner'])
                submits=list(getattr(sim,'submitTrace',[]) or r.get('submitTrace') or [])
                fills=list(getattr(sim,'v53Fills',[]) or r.get('v53FillEvents') or r.get('v53Fills') or [])
            finally:sim.close()
            opens=[x for x in submits if x.get('lane')=='DIRECTION_OPEN_FIRST']
            first=opens[0] if opens else None
            openfills=[x for x in fills if x.get('executionRole')=='DIRECTION_OPEN_FIRST' or x.get('lane')=='DIRECTION_OPEN_FIRST']
            ff=openfills[0] if openfills else None
            start=int(cr.get('windowEndMs') or 0)-300000
            row={'marketId':mid,'windowEndMs':cr.get('windowEndMs'),'ourIntent':None if first is None else {'t':int(first['t']),'normalizedPhase':(int(first['t'])-start)/300000.0,'side':first.get('side'),'price':float(first.get('price') or 0),'qty':float(first.get('qty') or 0)},'ourFirstMaterialized':None if ff is None else {'t':int(ff['t']),'normalizedPhase':(int(ff['t'])-start)/300000.0,'side':ff.get('side'),'price':float(ff.get('price') or 0),'qty':float(ff.get('qty') or 0)},'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0)}
            rows.append(row);outp.parent.mkdir(parents=True,exist_ok=True);outp.with_name(f'{outp.stem}.{mid}.partial.json').write_text(json.dumps(row,indent=2),encoding='utf-8');print(json.dumps({'progress':f'{i}/{len(mids)}','marketId':mid,'intent':row['ourIntent'],'materialized':row['ourFirstMaterialized']},ensure_ascii=False),flush=True)
        out={'version':'OUR_INITIAL_DIRECTION_LATEST8_V1','date':'2026-09-05','behaviorChange':False,'marketIds':mids,'rows':rows,'boundary':['current stack unchanged','Target not read on worker','winner scoring only','no dream fill','no 8781']};outp.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'markets':len(rows)}),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
