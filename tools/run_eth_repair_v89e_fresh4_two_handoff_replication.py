from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9; FIXED=[1912941,1912944,1912946,1912961]
def sib(name,file):
 p=Path(__file__).with_name(file); s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
v89d=sib('eth_v89d_for_v89e','run_eth_repair_v89d_two_active_composite_handoffs.py'); v89c=v89d.v89c; v80=v89d.v80; v38=v89d.v38

def safety(r):
 legacy=int(r.get('overOwnedSubmitViolations') or 0); comps=int(r.get('v84CompositeSubmits') or 0)
 return {'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'unexplainedOverOwned':max(0,legacy-comps),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0),'preBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0)+float(r.get('v84PreBirthPaymentLeak') or 0),'duplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)+float(r.get('v84DuplicateDebt') or 0),'sharedOverfill':float(r.get('v36SharedRealizedOverfill') or 0)}
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); ids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if ids!=FIXED: raise ValueError(ids)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v89e_')); stop=threading.Event()
 def hb():
  while not stop.wait(10): print(json.dumps({'heartbeat':'V89E_FRESH4','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'V89E_START','markets':ids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp); by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}; models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; rows=[]
  for mid in ids:
   cr=by[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
   def mk(cls): return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   b=mk(v89c.V89C)
   try: br=b.run_v89c(models,cr['winner'])
   finally: b.close()
   c=mk(v89d.V89D)
   try: rr=c.run_v89c(models,cr['winner'])
   finally: c.close()
   rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baselineV89C':br,'candidateV89D':rr})
   print(json.dumps({'marketId':mid,'floor':[br.get('floor'),rr.get('floor')],'pnl':[br.get('pnlDiagnosticOnly'),rr.get('pnlDiagnosticOnly')],'activeComposite':rr.get('v89cActiveCompositeSubmits'),'overflowPaid':rr.get('v84OverflowPaidQty'),'fills':rr.get('actualFillEvents')},ensure_ascii=False),flush=True)
  ss={k:sum(safety(x['candidateV89D'])[k] for x in rows) for k in safety(rows[0]['candidateV89D'])}; bf=sum(float(x['baselineV89C'].get('floor') or 0) for x in rows); cf=sum(float(x['candidateV89D'].get('floor') or 0) for x in rows); bp=sum(float(x['baselineV89C'].get('pnlDiagnosticOnly') or 0) for x in rows); cp=sum(float(x['candidateV89D'].get('pnlDiagnosticOnly') or 0) for x in rows); nonw=sum(float(x['candidateV89D'].get('floor') or 0)+EPS>=float(x['baselineV89C'].get('floor') or 0) for x in rows); trig=sum(int(x['candidateV89D'].get('v89cActiveCompositeSubmits') or 0) for x in rows); paid=sum(float(x['candidateV89D'].get('v84OverflowPaidQty') or 0) for x in rows)
  gates={'marketsComplete':len(rows)==4,'zeroTruthMismatch':ss['truthMismatch']==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroRepairDrift':ss['repairDrift']==0,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS,'atLeastOneRelayTrigger':trig>0}
  decision='KEEP_V89D_RELAY_FOR_BROADER_FRESH_HFT' if all(gates.values()) else 'REJECT_OR_NARROW_V89D_RELAY'
  out={'version':'ETH_REPAIR_V89E_FRESH4_TWO_HANDOFF_REPLICATION','date':'2026-09-03','researchOnly':True,'fixedMarkets':ids,'decision':decision,'aggregate':{'baselineFloorSum':bf,'candidateFloorSum':cf,'floorDelta':cf-bf,'baselinePnlSum':bp,'candidatePnlSum':cp,'pnlDelta':cp-bp,'perMarketFloorNonWorse':nonw,'activeCompositeSubmits':trig,'overflowPaid':paid},'safety':ss,'gates':gates,'rows':rows,'boundary':['frozen V89D two-handoff smoke mechanics','fresh4 replication only','no tuning','no Target runtime input','realistic HFT only','no 8781']}; Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'aggregate':out['aggregate'],'safety':ss,'gates':gates},ensure_ascii=False),flush=True)
 finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
