from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v65=sib('eth_v65_for_v70e','run_eth_repair_v65_global_expand_ownership_dedup_smoke.py');v38=v65.v38;EPS=1e-9

class V70EEarlyClockAudit(v65.V65GlobalExpandDedup):
 def __init__(self,*a,**kw):super().__init__(*a,**kw);self.v70eRows=[]
 def submit(self,t,side,p,q):
  role=str(getattr(self,'_pendingAuthorizedRole',None) or '');pid=getattr(self,'_pendingParentId',None);n0=self.n
  ok=super().submit(t,side,p,q)
  if ok and role=='REPAIR':
   key=f'{side}_{n0}';f=self._coord_feature(t);pE=None
   if self.teacher is not None:
    x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
   e=self.carrierLedger.get(key,{})
   self.v70eRows.append({'t':int(t),'key':key,'side':side,'qty':float(q),'price':float(p),'parentId':pid,'remainingSec':(int(self.capEnd)-int(t))/1000.0,'carrierOutstandingAtSubmit':max(0.0,float(e.get('submittedQty') or q)-float(e.get('actualFilled') or 0.0)),'pExpandAtRepairSubmit':pE,'modelWouldAuthorizeAtFixed05':bool(pE is not None and pE>=0.5),'pre180':int(self.capEnd)-int(t)>180000,'coordDebt':float(getattr(self,'_coordDebt',0.0)),'repairProgressFrac':float(f.get('repairProgressFrac') or 0.0),'floor':float(f.get('floor') or 0.0)})
  return ok
 def run_exam_v70e(self,models,winner):
  r=super().run_exam_v65(models,winner);r['v70eRepairSubmitClockAudit']=self.v70eRows;return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v70e_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V70E_EARLY_CLOCK_AUDIT','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V70E_EARLY_CLOCK_AUDIT_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];c=V70EEarlyClockAudit(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=c.run_exam_v70e(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'repairSubmitClocks':len(r['v70eRepairSubmitClockAudit']),'authorizedPre180':sum(1 for z in r['v70eRepairSubmitClockAudit'] if z['modelWouldAuthorizeAtFixed05'] and z['pre180'] and z['carrierOutstandingAtSubmit']>EPS),'rows':r['v70eRepairSubmitClockAudit']},ensure_ascii=False),flush=True)
  audits=[z for x in rows for z in x['functional']['v70eRepairSubmitClockAudit']];eligible=[z for z in audits if z['modelWouldAuthorizeAtFixed05'] and z['pre180'] and z['carrierOutstandingAtSubmit']>EPS]
  out={'version':'ETH_REPAIR_V70E_EARLY_REPAIR_RESERVATION_CLOCK_AUDIT','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'aggregate':{'markets':len(rows),'repairSubmitClocks':len(audits),'eligibleParallelReservationClocks':len(eligible),'maxPExpandAtRepairSubmit':max([z['pExpandAtRepairSubmit'] for z in audits if z['pExpandAtRepairSubmit'] is not None],default=None)},'decision':'PREREGISTER_EARLY_CLOCK_PARALLEL_RELAY_SMOKE' if eligible else 'EARLY_REPAIR_SUBMIT_CLOCK_NOT_REACHABLE_CONTINUE_PARENT_BIRTH_AUDIT','eligibleRows':eligible,'rows':rows,'boundary':['instrumentation only; V65 behavior unchanged','same frozen V44 teacher fixed 0.5 authorization semantics; no threshold tuning','Repair submit clock is observed before its fill/terminal transition','no Target/PnL/winner action authority','realistic HFT only; no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate'],'decision':out['decision']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
