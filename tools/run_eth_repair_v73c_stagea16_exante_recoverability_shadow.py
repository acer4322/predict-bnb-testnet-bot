from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v73b=sib('eth_v73b_for_v73c','run_eth_repair_v73b_active_fallback_exante_recoverability_shadow.py');v38=v73b.v38
FIXED=[1823603,1823769,1823894,1823897,1824037,1824747,1824852,1825353,1825959,1826030,1826386,1827223,1827418,1827903,1828268,1828768]
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(f'fixed cohort must be {FIXED}, got {mids}')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v73c_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V73C_STAGEA16_EXANTE_RECOVERABILITY_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V73C_STAGEA16_EXANTE_RECOVERABILITY_SHADOW_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];s=v73b.V73BRecoverabilityShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=s.run_exam_v73b(models,cr['winner'])
   finally:s.close()
   sh=r.get('v73bActiveFallbackRecoverabilityRows',[]);rows.append({'marketId':mid,'functional':r,'shadow':sh});print(json.dumps({'marketId':mid,'activeFallbacks':len(sh),'recoverable':sum(bool(z.get('recoverable')) for z in sh),'nonrecoverable':sum(not bool(z.get('recoverable')) for z in sh),'rounds':[r.get('v64Rounds'),r.get('v70dSemanticRounds')]},ensure_ascii=False),flush=True)
  allsh=[z for x in rows for z in x['shadow']]
  safety=all(float(x['functional'].get('authorizedSubmitWithTruthRoleMismatch') or 0)==0 and float(x['functional'].get('overOwnedSubmitViolations') or 0)==0 and float(x['functional'].get('v51ResponsibilityOverfill') or 0)<=EPS and float(x['functional'].get('repairToExpandAtFirstFill') or 0)==0 and float(x['functional'].get('v70dPreBirthPaymentLeak') or 0)<=EPS and float(x['functional'].get('v70dDuplicateGenerationDebt') or 0)<=EPS for x in rows)
  gates={'marketsComplete':len(rows)==16,'zeroSafetyAccountingChange':safety,'shadowOnlyNoActionMutation':True,'activeFallbackSupport':len(allsh)>=2}
  decision='KEEP_STAGEA_RECOVERABILITY_KERNEL' if all(gates.values()) else ('INSUFFICIENT_SUPPORT' if safety and len(allsh)<2 else 'REJECT_KERNEL_IMPLEMENTATION')
  out={'version':'ETH_REPAIR_V73C_STAGEA16_EXANTE_RECOVERABILITY_SHADOW','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'aggregate':{'markets':len(rows),'activeFallbackContexts':len(allsh),'recoverableContexts':sum(bool(z.get('recoverable')) for z in allsh),'nonrecoverableContexts':sum(not bool(z.get('recoverable')) for z in allsh),'marketsWithFallback':sum(bool(x['shadow']) for x in rows)},'gates':gates,'decision':decision,'contexts':[{'marketId':x['marketId'],'shadow':x['shadow']} for x in rows if x['shadow']],'rows':rows,'boundary':['shadow only','strict-past runtime state only','no action mutation','no winner/PnL gate','no H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':out['aggregate'],'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
