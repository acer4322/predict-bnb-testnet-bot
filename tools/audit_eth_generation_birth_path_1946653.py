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

def hit(z):
 s=json.dumps(z,ensure_ascii=False,default=str)
 return ('DOWN_4' in s or 'GENERATION' in s.upper() or 'OVERFLOW' in s.upper() or 'THESIS' in s.upper())

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='audit_generation_birth_1946653_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'AUDIT_GENERATION_BIRTH_1946653','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'AUDIT_GENERATION_BIRTH_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(mod.EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:
   r=s.run_locked(models,cr['winner']);groups={}
   for name,val in vars(s).items():
    if not isinstance(val,list):continue
    rows=[z for z in val if isinstance(z,dict) and hit(z)]
    if rows:groups[name]=rows[:250]
   fills=[]
   for k,e in getattr(s,'carrierLedger',{}).items():
    q=float(e.get('actualFilled') or 0)
    if q<=1e-9:continue
    o=getattr(s,'orders',{}).get(k,{})
    fills.append({'key':k,'placed':int(o.get('placed') or e.get('submittedAt') or 0),'side':e.get('side') or o.get('side'),'price':o.get('price'),'qty':q,'role':e.get('objectiveRole'),'lane':e.get('lane'),'parentId':e.get('parentId'),'objectiveId':e.get('objectiveId')})
   fills.sort(key=lambda x:x['placed'])
  finally:s.close()
  out={'version':'ETH_GENERATION_BIRTH_PATH_AUDIT_1946653_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'fills':fills,'eventGroups':groups,'candidate':base.slim(r),'boundary':['behavior-inert introspection','no strategy changes','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'fills':fills,'eventGroupNames':sorted(groups)},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
