from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
pe=base.pe

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix=f'audit_active_expand_{mid}_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'AUDIT_ACTIVE_EXPAND_GENERIC','marketId':mid,'elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'AUDIT_ACTIVE_EXPAND_GENERIC_START','marketId':mid}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz';s=pe.make(mod.EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:
   r=s.run_locked(models,cr['winner']);fills=[]
   for k,e in getattr(s,'carrierLedger',{}).items():
    q=float(e.get('actualFilled') or 0.0)
    if q<=1e-9:continue
    o=getattr(s,'orders',{}).get(k,{})
    fills.append({'key':k,'placed':int(o.get('placed') or e.get('submittedAt') or 0),'side':e.get('side') or o.get('side'),'price':o.get('price'),'qty':q,'role':e.get('objectiveRole'),'lane':e.get('lane'),'parentId':e.get('parentId'),'objectiveId':e.get('objectiveId')})
   fills.sort(key=lambda x:x['placed']);v64=[x for x in (getattr(s,'v64Events',[]) or []) if x.get('event')=='ACTIVE_EXPAND_FALLBACK_FILL']
  finally:s.close()
  out={'version':'ETH_ACTIVE_EXPAND_FILL_GENERIC_AUDIT_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'activeExpandFillEvents':v64,'activeExpandFillQty':sum(float(x.get('incQty') or 0) for x in v64),'fills':fills,'candidate':base.slim(r),'boundary':['behavior-inert current-stack audit','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'marketId':mid,'activeExpandFillQty':out['activeExpandFillQty'],'activeExpandEvents':v64,'fills':fills,'candidate':out['candidate']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
