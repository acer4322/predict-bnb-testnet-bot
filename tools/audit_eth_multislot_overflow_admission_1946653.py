from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
MID=1946653;pe=base.pe
T0=1788536158200;T1=1788536158600

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='audit_overflow_admission_1946653_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'AUDIT_MULTISLOT_OVERFLOW_ADMISSION_1946653','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'AUDIT_MULTISLOT_OVERFLOW_ADMISSION_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
  models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(mod.EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:
   r=s.run_locked(models,cr['winner']);cons,bound,parents=pe.alloc(s,r)
   order_rows=[]
   for k,o in getattr(s,'orders',{}).items():
    placed=int(o.get('placed') or 0)
    if T0<=placed<=T1:
     e=getattr(s,'carrierLedger',{}).get(k,{})
     order_rows.append({'key':k,'placed':placed,'side':o.get('side'),'price':o.get('price'),'qty':o.get('qty'),'execution_role':o.get('execution_role'),'ledger':{q:e.get(q) for q in ['parentId','objectiveId','objectiveRole','lane','submittedQty','actualFilled','terminalConfirmed','cancelRequested','parentDebtAtSubmit']}})
   event_groups={}
   for name,val in vars(s).items():
    if not isinstance(val,list):continue
    rows=[]
    for z in val:
     if not isinstance(z,dict) or 't' not in z:continue
     try:t=int(z.get('t'))
     except Exception:continue
     if T0<=t<=T1:rows.append(z)
    if rows:event_groups[name]=rows[:120]
   v84={str(k):dict(v) for k,v in getattr(s,'v84Composite',{}).items() if str(k) in {x['key'] for x in order_rows} or float(v.get('overflowAllocated') or 0)>0}
   pay=s._current_payoffs()
  finally:s.close()
  out={'version':'ETH_MULTISLOT_OVERFLOW_ADMISSION_AUDIT_1946653_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'window':[T0,T1],'ordersInWindow':order_rows,'eventGroups':event_groups,'v84Composite':v84,'allocationParents':parents,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'terminalPayoffs':pay,'candidateSummary':base.slim(r),'boundary':['behavior-inert event introspection around UP_8 submit','no strategy changes','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'orders':order_rows,'eventGroupNames':sorted(event_groups),'v84':v84,'candidate':out['candidateSummary']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
