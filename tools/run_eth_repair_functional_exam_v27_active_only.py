from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
 from tools import run_eth_repair_functional_exam_v27_bounded_active_repair as v27
except ImportError:
 import importlib.util
 p=Path(__file__).resolve().with_name('run_eth_repair_functional_exam_v27_bounded_active_repair.py');s=importlib.util.spec_from_file_location('v27active',p);v27=importlib.util.module_from_spec(s);s.loader.exec_module(v27)
EPS=1e-9

def main():
 ap=argparse.ArgumentParser();
 for k in ['bundle','lifecycle_model','capability_model','dagger_cache','timing_model','economic_model','price_model','surplus_model','hazard_model','sizing_model','market_ids']:
  ap.add_argument('--'+k.replace('_','-'),required=True)
 a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v27_activeonly_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v27.load_runtime(a);haz=joblib.load(a.hazard_model);siz=joblib.load(a.sizing_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=v27.BoundedActiveRepairSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,hazard_art=haz,sizing_art=siz)
   try:r=sim.run_exam_v27(models,cr['winner']);c=sim.causal()
   finally:sim.close()
   first_actual=(c.get('firstActualFill') or {}).get('t');active_submits=[e for e in r.get('activeTrace',[]) if e.get('event')=='ACTIVE_SUBMIT'];prefill=sum(1 for e in active_submits if first_actual is not None and int(e['t'])<=int(first_actual));fill_over=max(0.0,float(r.get('activeFillQty') or 0)-float(r.get('activeGapAtSubmit') or 0))
   row={'marketId':mid,'functional':r,'causal':c,'localSafety':{'activeSubmitAfterFirstActualFill':prefill==0,'zeroPassiveRepairOwnershipAtActiveSubmit':float(r.get('activePassiveOwnedAtSubmitMax') or 0)<=EPS,'activeSubmitQtyWithinResidual':float(r.get('activeSubmitQty') or 0)<=float(r.get('activeGapAtSubmit') or 0)+1e-8,'activeFillWithinResidual':fill_over<=1e-8,'zeroActiveTruthRoleBlocks':int(r.get('activeTruthRoleBlocks') or 0)==0,'zeroAuthorizedTruthMismatch':int(r.get('authorizedSubmitWithTruthRoleMismatch') or 0)==0,'zeroOverOwnedRepairSubmit':int(r.get('overOwnedSubmitViolations') or 0)==0,'zeroUnresolvedCarrierAtTerminal':abs(float(r.get('unresolvedCarrierQty') or 0))<=1e-9,'atMostOneActiveSubmit':int(r.get('activeSubmitCount') or 0)<=1}}
   rows.append(row);print(json.dumps({'marketId':mid,'activeSubmit':r.get('activeSubmitCount'),'activeFillQty':r.get('activeFillQty'),'activeSubmitQty':r.get('activeSubmitQty'),'gapAtSubmit':r.get('activeGapAtSubmit'),'cycles':r.get('reserveCycleCompletion'),'floor':r.get('floor'),'absNet':r.get('absNet'),'pnlDiagnostic':r.get('pnlDiagnosticOnly'),'localSafety':row['localSafety'],'activeTrace':r.get('activeTrace')},ensure_ascii=False),flush=True)
  safety={k:all(x['localSafety'][k] for x in rows) for k in rows[0]['localSafety']} if rows else {};active_sub=sum(float(x['functional'].get('activeSubmitCount') or 0) for x in rows);active_fill=sum(float(x['functional'].get('activeFillQty') or 0) for x in rows);behavior={'activeSubmitExercised':active_sub>0,'activeActualFillExercised':active_fill>0}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V27_ACTIVE_ONLY','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'aggregate':{'activeSubmits':active_sub,'activeFillQty':active_fill,'cycles':sum(float(x['functional'].get('reserveCycleCompletion') or 0) for x in rows),'floorGain':sum(float(x['functional'].get('reserveCycleFloorGainTotal') or 0) for x in rows),'pnlDiagnostic':sum(float(x['functional'].get('pnlDiagnosticOnly') or 0) for x in rows),'buyNotional':sum(float(x['functional'].get('buyNotional') or 0) for x in rows)},'safetyGates':safety,'behaviorGates':behavior,'smokeVerified':bool(rows) and all(safety.values()) and all(behavior.values()),'rows':rows,'boundary':['V27-only replay; frozen V23 baseline is not recomputed here.','consumed realistic HFT only','frozen ETH urgency+sizing teachers','one active intervention max per market','ADD absent','PnL diagnostic only']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':out['aggregate'],'safety':safety,'behavior':behavior},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
