from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_parent_family_safety_1946784 as fam
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9;pe=base.pe

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--baseline-json',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix=f'multislot_family_generic_{mid}_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'MULTISLOT_PARENT_FAMILY_GENERIC','marketId':mid,'elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTISLOT_PARENT_FAMILY_GENERIC_START','marketId':mid}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};cr=co[mid];bj=json.load(open(a.baseline_json,encoding='utf-8'));b={int(x['marketId']):x for x in bj['rows']}[mid]
  models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz';s=pe.make(fam.ParentFamilySafetyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_family(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
  finally:s.close()
  m=base.slim(r);ss=pe.safety(r);occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;events=r.get('parentFamilyEvents') or [];ex=any(x.get('event')=='PARENT_FAMILY_ACTIVE_SCOPE' for x in events)
  gates={'familyActiveScopeExercised':ex,'zeroParentFamilyRealizedOverfill':float(r.get('parentFamilyRealizedOverfill') or 0)<=EPS,'zeroParentFamilyWorstCaseOverfill':float(r.get('parentFamilyWorstCaseOverfill') or 0)<=EPS,'legacySafetyZero':all(float(x or 0)<=EPS for x in ss.values()),'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':bool(occok),'noNewRepairParent':m['repairParentBirths']==int(b['repairParentBirths'])}
  if not all(v for k,v in gates.items() if k!='familyActiveScopeExercised'):decision='PARENT_FAMILY_REPLICATION_SAFETY_FAIL'
  elif not ex:decision='PARENT_FAMILY_REPLICATION_NOT_EXERCISED'
  else:decision='PARENT_FAMILY_REPLICATION_PASS'
  out={'version':'ETH_MULTISLOT_PARENT_FAMILY_SAFETY_GENERIC_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'decision':decision,'baseline':{'pnlDiagnosticOnly':float(b['pnlDiagnosticOnly']),'floor':float(b['floor']),'fills':int(b['fills']),'rounds':int(b['rounds'])},'candidate':m,'delta':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-float(b['pnlDiagnosticOnly']),'floor':m['floor']-float(b['floor']),'fills':m['fills']-int(b['fills']),'rounds':m['rounds']-int(b['rounds'])},'gates':gates,'safety':ss,'parentFamilyRealizedOverfill':r.get('parentFamilyRealizedOverfill'),'parentFamilyWorstCaseOverfill':r.get('parentFamilyWorstCaseOverfill'),'parentFamilyEvents':events[:300],'economicEvents':(r.get('economicHandoffLeaseEvents') or [])[:200],'allocationParents':parents,'occupancyParents':occ,'boundary':['one-market replication of parent-family Active/Passive safety semantics','same EconomicHandoffLeaseLock execution behavior','no threshold/model/qty/price/timing change','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'baseline':out['baseline'],'candidate':m,'delta':out['delta'],'gates':gates,'safety':ss,'familyEvents':events[:20]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
