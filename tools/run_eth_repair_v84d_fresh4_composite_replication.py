from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=[1912941,1912944,1912946,1912961]
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v84=sib('eth_v84b_for_v84d','run_eth_repair_v84b_composite_repair_behavior_1912961.py');v83=v84.v83;v80=v84.v80;v38=v84.v38

def metrics(r):
 legacy=int(r.get('overOwnedSubmitViolations') or 0);comps=int(r.get('v84CompositeSubmits') or 0)
 return {'legacyOverOwned':legacy,'compositeSubmits':comps,'unexplainedOverOwned':max(0,legacy-comps),'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0),'preBirthLeak':float(r.get('v84PreBirthPaymentLeak') or 0),'duplicateOverflowDebt':float(r.get('v84DuplicateDebt') or 0),'generationPreBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0),'generationDuplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(mids)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v84d_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V84D_FRESH4_COMPOSITE','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V84D_FRESH4_COMPOSITE_START','markets':mids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v83.V83CleanModularCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:br=b.run_exam_v83(models,cr['winner'])
   finally:b.close()
   c=v84.V84BCompositeRepair(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:rr=c.run_exam_v84b(models,cr['winner'])
   finally:c.close()
   m=metrics(rr);rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baselineV83':br,'candidateV84':rr,'allocationAwareSafety':m})
   print(json.dumps({'marketId':mid,'floor':[br.get('floor'),rr.get('floor')],'pnl':[br.get('pnlDiagnosticOnly'),rr.get('pnlDiagnosticOnly')],'fills':[br.get('actualFillEvents'),rr.get('actualFillEvents')],'composite':[rr.get('v84CompositeSubmits'),rr.get('v84CompositeFillQty')],'alloc':[rr.get('v84RepairAllocatedQty'),rr.get('v84OverflowAllocatedQty')],'legacyOverOwned':[m['legacyOverOwned'],m['unexplainedOverOwned']]},ensure_ascii=False),flush=True)
  bfloor=sum(float(x['baselineV83'].get('floor') or 0) for x in rows);cfloor=sum(float(x['candidateV84'].get('floor') or 0) for x in rows);bpnl=sum(float(x['baselineV83'].get('pnlDiagnosticOnly') or 0) for x in rows);cpnl=sum(float(x['candidateV84'].get('pnlDiagnosticOnly') or 0) for x in rows)
  compfills=sum(float(x['candidateV84'].get('v84CompositeFillQty') or 0) for x in rows);rep=sum(float(x['candidateV84'].get('v84RepairAllocatedQty') or 0) for x in rows);ov=sum(float(x['candidateV84'].get('v84OverflowAllocatedQty') or 0) for x in rows);debt=sum(float(x['candidateV84'].get('v84OverflowDebtQty') or 0) for x in rows)
  unexpl=sum(int(x['allocationAwareSafety']['unexplainedOverOwned']) for x in rows);safety=sum(x['allocationAwareSafety']['truthMismatch']+x['allocationAwareSafety']['responsibilityOverfill']+x['allocationAwareSafety']['repairDrift']+x['allocationAwareSafety']['preBirthLeak']+x['allocationAwareSafety']['duplicateOverflowDebt']+x['allocationAwareSafety']['generationPreBirthLeak']+x['allocationAwareSafety']['generationDuplicateDebt'] for x in rows)
  gates={'marketsComplete':len(rows)==4,'compositePhysicalFillExercised':compfills>EPS,'physicalAllocationConservation':abs(compfills-rep-ov)<=1e-7,'overflowDebtExact':abs(ov-debt)<=1e-7,'zeroUnexplainedOverOwned':unexpl==0,'zeroOtherSafetyViolations':safety<=EPS,'noAggregateFloorRegression':cfloor+EPS>=bfloor}
  decision='KEEP_V84_COMPOSITE_RELAY_AXIS_NEXT_PAYMENT_CONTINUATION' if all(gates.values()) else 'REJECT_OR_LOCALIZE_V84_FRESH4_REGRESSION'
  out={'version':'ETH_REPAIR_V84D_FRESH4_COMPOSITE_REPLICATION','date':'2026-09-03','researchOnly':True,'fixedCohort':FIXED,'decision':decision,'gates':gates,'aggregate':{'baselineFloorSum':bfloor,'candidateFloorSum':cfloor,'floorDelta':cfloor-bfloor,'baselinePnlSum':bpnl,'candidatePnlSum':cpnl,'pnlDelta':cpnl-bpnl,'compositeFillQty':compfills,'repairAllocatedQty':rep,'overflowAllocatedQty':ov,'overflowDebtQty':debt,'unexplainedOverOwned':unexpl},'rows':rows,'boundary':['Same V84B behavior frozen','Allocation-aware safety','No tuning','Winner post-hoc only','Realistic HFT only','No 8781','Target used only in separate post-market teacher audit']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
