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
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix=f'audit_completion_{mid}_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'AUDIT_COMPLETION_SEMANTICS','marketId':mid,'elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'AUDIT_COMPLETION_SEMANTICS_START','marketId':mid}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz';s=pe.make(mod.EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:
   r=s.run_locked(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);pay=s._current_payoffs();mg=list(getattr(s,'v80ManagementEvents',[]) or [])
  finally:s.close()
  summary={
   'repairParentCompletionsLegacy':int(r.get('repairParentCompletions') or 0),
   'v80ShareRepairSettlements':int(r.get('v80ShareRepairSettlements') or 0),
   'v80ManagementCompletions':int(r.get('v80ManagementCompletions') or 0),
   'v80EconomicDeficitActiveAtEnd':int(r.get('v80EconomicDeficitActiveAtEnd') or 0),
   'v80EconomicDeficitAmountAtEnd':float(r.get('v80EconomicDeficitAmountAtEnd') or 0),
   'repairParentActiveAtEnd':int(r.get('repairParentActiveAtEnd') or 0),
   'floor':float(r.get('floor') or 0),'pnl':float(r.get('pnlDiagnosticOnly') or 0),'gap':float(pay.get('gap') or 0),
   'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0)
  }
  out={'version':'ETH_COMPLETION_SEMANTICS_AUDIT_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'summary':summary,'allocationParents':parents,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'v80ManagementEvents':mg[:200],'boundary':['behavior-inert completion/reporting audit','no strategy changes','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'marketId':mid,'summary':summary,'managementEvents':mg[:30]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
