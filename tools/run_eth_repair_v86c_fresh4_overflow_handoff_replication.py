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
v86=sib('eth_v86b_for_v86c','run_eth_repair_v86b_overflow_single_disconnect_active.py');v85=v86.v85;v80=v86.v80;v38=v86.v38

def safety(r):
 legacy=int(r.get('overOwnedSubmitViolations') or 0);comps=int(r.get('v84CompositeSubmits') or 0)
 return {'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'unexplainedOverOwned':max(0,legacy-comps),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0),'preBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0)+float(r.get('v84PreBirthPaymentLeak') or 0),'duplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)+float(r.get('v84DuplicateDebt') or 0),'sharedOverfill':float(r.get('v36SharedRealizedOverfill') or 0)}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();ids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if ids!=FIXED:raise ValueError(ids)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v86c_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V86C_FRESH4','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V86C_START','markets':ids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in ids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   b=mk(v85.V85E)
   try:br=b.run_v85e(models,cr['winner'])
   finally:b.close()
   c=mk(v86.V86B)
   try:rr=c.run_v86b(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baselineV85E':br,'candidateV86B':rr})
   print(json.dumps({'marketId':mid,'floor':[br.get('floor'),rr.get('floor')],'pnl':[br.get('pnlDiagnosticOnly'),rr.get('pnlDiagnosticOnly')],'overflowPaid':[br.get('v84OverflowPaidQty'),rr.get('v84OverflowPaidQty')],'triggers':rr.get('v86SingleDisconnectTriggers'),'activeFill':rr.get('v36ActiveFillQty')},ensure_ascii=False),flush=True)
  ss={k:sum(safety(x['candidateV86B'])[k] for x in rows) for k in safety(rows[0]['candidateV86B'])};bf=sum(float(x['baselineV85E'].get('floor') or 0) for x in rows);cf=sum(float(x['candidateV86B'].get('floor') or 0) for x in rows);nonw=sum(float(x['candidateV86B'].get('floor') or 0)+EPS>=float(x['baselineV85E'].get('floor') or 0) for x in rows);trig=sum(int(x['candidateV86B'].get('v86SingleDisconnectTriggers') or 0) for x in rows);af=sum(float(x['candidateV86B'].get('v36ActiveFillQty') or 0) for x in rows);deltas=[float(z) for x in rows for z in x['candidateV86B'].get('v36FloorFillDeltas',[])]
  gates={'marketsComplete':len(rows)==4,'zeroTruthMismatch':ss['truthMismatch']==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroRepairDrift':ss['repairDrift']==0,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS,'allActiveFillFloorDeltasNonnegative':all(d>=-EPS for d in deltas),'aggregateFloorNonWorse':cf+EPS>=bf,'allPerMarketFloorNonWorse':nonw==4,'triggerImpliesActiveFill':trig==0 or af>EPS}
  decision='KEEP_V86_OVERFLOW_EXECUTION_HANDOFF_MODULE' if all(gates.values()) and trig>0 else 'REJECT_OR_NARROW_V86_OVERFLOW_HANDOFF'
  out={'version':'ETH_REPAIR_V86C_FRESH4_OVERFLOW_EXECUTION_HANDOFF_REPLICATION','date':'2026-09-03','researchOnly':True,'fixedMarkets':ids,'decision':decision,'aggregate':{'baselineV85EFloorSum':bf,'candidateV86BFloorSum':cf,'floorDelta':cf-bf,'perMarketNonWorse':nonw,'triggers':trig,'activeFillQty':af,'activeFloorDeltas':deltas},'safety':ss,'gates':gates,'rows':rows,'boundary':['replication of V86B execution handoff only','does not promote V85E raw sizing','no tuning','realistic HFT only','no Target runtime input','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':out['aggregate'],'safety':ss,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
