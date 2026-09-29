from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v75=sib('eth_v75_for_v76','run_eth_repair_v75_pre_safe_thesis_ownership_fresh1911708.py');v73b=v75.v73b;v70g=v75.v70g;v38=v75.v38
class V76RecoverabilityGate(v75.V75PreSafeThesis):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v76Blocks=0;self.v76Allows=0;self.v76Events=[]
 def _submit_active_expand(self,t,source_key,e,o,remaining):
  row=self._shadow_active_recoverability(t,source_key,e,o,remaining)
  if row is not None and not bool(row.get('recoverable')):
   self.v76Blocks+=1;self.v76Events.append({**row,'event':'RECOVERABILITY_ACTIVE_HANDOFF_BLOCK'});return False
  self.v76Allows+=1;ok=v70g.V70GGenerationScopedRelay._submit_active_expand(self,t,source_key,e,o,remaining)
  if row is not None:self.v76Events.append({**row,'event':'RECOVERABILITY_ACTIVE_HANDOFF_ALLOW','submitOk':bool(ok)})
  return ok
 def run_exam_v76(self,models,winner):
  r=super().run_exam_v75(models,winner);r.update({'v76ActiveBlocks':self.v76Blocks,'v76ActiveAllows':self.v76Allows,'v76Events':self.v76Events[:80]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,default=1911708);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=1911708:raise ValueError('V76 preregistered only for 1911708')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v76_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V76_RECOVERABILITY_GATE','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V76_RECOVERABILITY_GATE_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};cr=by[a.market_id]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{a.market_id}.json.xz'
  b=v75.V75PreSafeThesis(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:br=b.run_exam_v75(models,cr['winner'])
  finally:b.close()
  c=V76RecoverabilityGate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:rr=c.run_exam_v76(models,cr['winner'])
  finally:c.close()
  safety={'truthMismatch':float(rr.get('authorizedSubmitWithTruthRoleMismatch') or 0),'overOwned':float(rr.get('overOwnedSubmitViolations') or 0),'responsibilityOverfill':float(rr.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(rr.get('repairToExpandAtFirstFill') or 0),'preBirthPaymentLeak':float(rr.get('v70dPreBirthPaymentLeak') or 0),'duplicateGenerationDebt':float(rr.get('v70dDuplicateGenerationDebt') or 0)}
  gates={'preSafeThesisStillBorn':int(rr.get('v75PreSafeThesisBirths') or 0)>=1,'unrecoverableActiveFallbackBlocked':int(rr.get('v76ActiveBlocks') or 0)>=1,'zeroBlockedActiveFill':float(rr.get('v64FillQty') or 0)<=EPS,'zeroTruthMismatch':safety['truthMismatch']==0,'zeroOverOwned':safety['overOwned']==0,'zeroResponsibilityOverfill':safety['responsibilityOverfill']<=EPS,'zeroRepairDrift':safety['repairDrift']==0,'zeroPreBirthPaymentLeak':safety['preBirthPaymentLeak']<=EPS,'zeroDuplicateGenerationDebt':safety['duplicateGenerationDebt']<=EPS,'oneResponsibilityPerGeneration':int(rr.get('v70gMaxResponsibilitiesPerGeneration') or 0)<=1}
  decision='KEEP_V76_FOR_FRESH_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_V76'
  out={'version':'ETH_REPAIR_V76_RECOVERABILITY_GATED_ACTIVE_HANDOFF_FRESH1911708','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'marketId':a.market_id,'winnerPostHocDiagnosticOnly':cr['winner'],'gates':gates,'decision':decision,'safety':safety,'diagnostics':{'v75':{'pnl':br.get('pnlDiagnosticOnly'),'floor':br.get('floor'),'absNet':br.get('absNet'),'activeFillQty':br.get('v64FillQty'),'thesisSide':br.get('thesisSide')},'v76':{'pnl':rr.get('pnlDiagnosticOnly'),'floor':rr.get('floor'),'absNet':rr.get('absNet'),'activeFillQty':rr.get('v64FillQty'),'thesisSide':rr.get('thesisSide'),'blocks':rr.get('v76ActiveBlocks'),'allows':rr.get('v76ActiveAllows')}},'v76Events':rr.get('v76Events',[]),'candidate':rr,'baselineV75':br,'boundary':['fresh 1911708 only','strict-past recoverability gate','winner post-hoc diagnostic only','no Target action clock','no numeric tuning','realistic-HFT/Predict Tape','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'diagnostics':out['diagnostics'],'v76Events':out['v76Events']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
