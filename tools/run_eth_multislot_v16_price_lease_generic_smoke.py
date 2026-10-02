from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_v16_price_lease_1946475 as pl
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9;pe=base.pe

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix=f'multislot_v16_price_lease_{mid}_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz';s=pe.make(pl.PriceLeaseHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_price_lease(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
  finally:s.close()
  ss=pe.safety(r);safe=all(float(x or 0)<=EPS for x in ss.values());occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=base.slim(r);pf=r.get('partitionFills') or [];partfill=sum(float(x.get('actualFilled') or 0.0) for x in pf);rollfill=float(r.get('repeatedRollingFillQty') or 0.0);econ=r.get('rollingEconomicEvents') or [];lev=r.get('v16PriceLeaseEvents') or []
  gates={'partitionAttempted':int(r.get('partitionAttempts') or 0)>0,'twoChildrenSubmitted':int(r.get('partitionApplied') or 0)>0,'anyManagedPassivePhysicalFill':partfill>EPS,'rollingPhysicalFill':rollfill>EPS,'economicPreflightExercised':len(econ)>0,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occok}
  if not(safe and cons and bound and occok):decision='MULTISLOT_REPLICATION_ACCOUNTING_FAIL'
  elif not gates['twoChildrenSubmitted']:decision='MULTISLOT_REPLICATION_NO_TWO_CHILD_SUBMIT'
  elif gates['rollingPhysicalFill']:decision='MULTISLOT_REPLICATION_ROLLING_PHYSICAL_PASS'
  elif gates['anyManagedPassivePhysicalFill']:decision='MULTISLOT_REPLICATION_INITIAL_SIBLING_PHYSICAL_PASS'
  else:decision='MULTISLOT_REPLICATION_SUBMIT_NO_FILL'
  out={'version':'ETH_MULTISLOT_V16_PRICE_LEASE_GENERIC_SMOKE_V1','date':'2026-09-05','marketId':mid,'researchOnly':True,'decision':decision,'candidate':m,'gates':gates,'partitionFills':pf,'partitionEvents':(r.get('partitionEvents') or [])[:80],'rollingFillQty':rollfill,'reanchorCycles':r.get('reanchorCycles'),'priceLeaseEvents':lev[:120],'economicEvents':econ[:240],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['structure-selected market only','same initial quota partition + rolling + V16 economic preflight + price lease','existing Active router frozen','no PnL/winner selection','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'marketId':mid,'decision':decision,'candidate':m,'gates':gates,'partitionFills':pf,'rollingFillQty':rollfill,'leaseEvents':lev[:20],'econTail':econ[-20:],'safety':ss},ensure_ascii=False))
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
