from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v56=sib('eth_v56_for_v59','run_eth_repair_v56_target_book_hazard_parallel_residual_shadow.py')
port=sib('eth_port_v57_for_v59','train_target_eth_repair_taker_portable_normalized_v57.py')
v38=v56.v38;EPS=1e-9

class V59StateDump(v56.V56HazardParallelShadow):
 def _shadow_score(self,t):
  n=len(self.v56Rows);super()._shadow_score(t)
  if len(self.v56Rows)<=n:return
  x,vals=self._feature_vector(t)
  if vals is None:return
  row=self.v56Rows[-1];pv=port.portable_values(vals)
  for k,v in pv.items():row['p_'+k]=v
  gross=max(0.0,float(vals.get('combined_gross') or 0.0));den=gross+1.0;rem=max(EPS,float(row.get('generationRemaining') or 0.0));debt=max(EPS,float(row.get('generationDebt') or rem))
  row.update({
   'm_raw_hazard':float(row.get('hazard') or 0.0),
   'm_resp_remaining_per_gross':float(row.get('generationRemaining') or 0.0)/den,
   'm_passive_remaining_frac':float(row.get('passiveRemaining') or 0.0)/rem,
   'm_active_legal_frac':float(row.get('activeLegalMin') or 0.0)/rem,
   'm_passive_legal_frac':float(row.get('passiveLegalMin') or 0.0)/rem,
   'm_projected_floor_per_gross':float(row.get('projectedFloor') or vals.get('worst_case_floor') or 0.0)/den,
   'm_projected_gap_frac':float(row.get('projectedRepairGap') or row.get('generationRemaining') or 0.0)/rem,
   'm_passive_paid_frac':float(row.get('passiveRepairQtyThisGeneration') or 0.0)/debt,
   'm_active_paid_frac':float(row.get('activeRepairQtyThisGeneration') or 0.0)/debt,
   'm_is_generation':1.0 if str(row.get('responsibilityKind'))=='V48_GENERATION' else 0.0,
   'm_existing_hard':1.0 if row.get('existingHardConfirmed') else 0.0,
   'm_feasible':1.0 if row.get('feasible') else 0.0,
   'm_last_passive_expand':1.0 if row.get('lastFillRole')=='PASSIVE_EXPAND' else 0.0,
   'm_last_passive_repair':1.0 if row.get('lastFillRole')=='PASSIVE_REPAIR' else 0.0,
   'm_last_active_repair':1.0 if row.get('lastFillRole')=='ACTIVE_REPAIR' else 0.0,
   'm_last_active_expand':1.0 if row.get('lastFillRole')=='ACTIVE_EXPAND' else 0.0
  })
 def run_exam_v59(self,models,winner):
  r=super().run_exam_v56(models,winner);r['v59Rows']=self.v56Rows;return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','hazard-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v59_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V59','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V59_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];haz=joblib.load(a.hazard_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V59StateDump(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,hazard_art=haz)
   try:r=sim.run_exam_v59(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'winner':cr['winner'],'functional':r});print(json.dumps({'marketId':mid,'eligibleRows':len(r.get('v59Rows',[])),'rounds':r.get('repairExpandRepairRounds',0),'pnlDiagnostic':r.get('pnlDiagnosticOnly')},ensure_ascii=False),flush=True)
  out={'version':'ETH_REPAIR_V59_OUR_STATE_ROUTE_DISTILL_DUMP','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'markets':len(rows),'eligibleRows':sum(len(x['functional'].get('v59Rows',[])) for x in rows),'rows':rows,'boundary':['V52/V53 behavior unchanged','all >180s OUR Repair-responsibility states stored at 1s buckets','features strict-past OUR state/public book only','Target label not present in worker dump','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'markets':len(rows),'eligibleRows':out['eligibleRows']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
