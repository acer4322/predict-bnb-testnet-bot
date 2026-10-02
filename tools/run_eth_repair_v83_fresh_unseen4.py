from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=[1912941,1912944,1912946,1912961]

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v83=sib('eth_v83_for_fresh','run_eth_repair_v83_clean_modular_candidate_smoke.py');v80=v83.v80;v38=v83.v38

def saf(r):
 return {
  'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0.0),
  'overOwned':float(r.get('overOwnedSubmitViolations') or 0.0),
  'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0.0),
  'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0.0),
  'preBirthPaymentLeak':float(r.get('v70dPreBirthPaymentLeak') or 0.0),
  'duplicateGenerationDebt':float(r.get('v70dDuplicateGenerationDebt') or 0.0)}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(mids)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v83_fresh_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V83_FRESH_UNSEEN4','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V83_FRESH_UNSEEN4_START','markets':mids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co}
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v80.V80ModularManagementKernel(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:br=b.run_exam_v80(models,cr['winner'])
   finally:b.close()
   c=v83.V83CleanModularCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:rr=c.run_exam_v83(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baselineV80':br,'candidateV83':rr})
   print(json.dumps({'marketId':mid,'winner':cr['winner'],'baseline':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'fills':br.get('actualFillEvents')},'v83':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'v36Fill':rr.get('v36ActiveFillQty'),'genActivePaid':rr.get('v49GenerationActivePaidQty'),'v64Fill':rr.get('v64FillQty'),'admissionAllow':rr.get('v83AdmissionAllows'),'admissionBlock':rr.get('v83AdmissionBlocks'),'deficit':rr.get('v80EconomicDeficitAmountAtEnd')}},ensure_ascii=False),flush=True)
  s={k:sum(saf(x['candidateV83'])[k] for x in rows) for k in saf(rows[0]['candidateV83'])};maxresp=max(int(x['candidateV83'].get('v70gMaxResponsibilitiesPerGeneration') or 0) for x in rows)
  bfloor=sum(float(x['baselineV80'].get('floor') or 0) for x in rows);cfloor=sum(float(x['candidateV83'].get('floor') or 0) for x in rows);bpnl=sum(float(x['baselineV80'].get('pnlDiagnosticOnly') or 0) for x in rows);cpnl=sum(float(x['candidateV83'].get('pnlDiagnosticOnly') or 0) for x in rows)
  traded=sum(int(x['candidateV83'].get('actualFillEvents') or 0)>0 for x in rows);floor_nonneg=sum(float(x['candidateV83'].get('floor') or 0)>=-EPS for x in rows);pnl_pos=sum(float(x['candidateV83'].get('pnlDiagnosticOnly') or 0)>EPS for x in rows)
  early=sum(int(x['candidateV83'].get('v70dParallelReservations') or 0) for x in rows)
  gates={'marketsComplete':len(rows)==4,'zeroTruthMismatch':s['truthMismatch']==0,'zeroOverOwned':s['overOwned']==0,'zeroResponsibilityOverfill':s['responsibilityOverfill']<=EPS,'zeroRepairDrift':s['repairDrift']==0,'zeroPreBirthPaymentLeak':s['preBirthPaymentLeak']<=EPS,'zeroDuplicateGenerationDebt':s['duplicateGenerationDebt']<=EPS,'oneResponsibilityPerGeneration':maxresp<=1,'v70fEarlyAuthorityStillDisabled':early==0}
  decision='KEEP_V83_ARCHITECTURE_AFTER_FRESH_UNSEEN4' if all(gates.values()) else 'FIX_V83_SAFETY_BEFORE_MORE_FRESH_HFT'
  out={'version':'ETH_REPAIR_V83_FRESH_UNSEEN4','date':'2026-09-03','researchOnly':True,'fixedCohort':FIXED,'decision':decision,'gates':gates,'safety':s,'aggregate':{'markets':4,'marketsTraded':traded,'floorNonnegativeMarkets':floor_nonneg,'positivePnlMarkets':pnl_pos,'baselineFloorSum':bfloor,'candidateFloorSum':cfloor,'floorDelta':cfloor-bfloor,'baselinePnlSum':bpnl,'candidatePnlSum':cpnl,'pnlDelta':cpnl-bpnl,'v36FillQty':sum(float(x['candidateV83'].get('v36ActiveFillQty') or 0) for x in rows),'generationActivePaidQty':sum(float(x['candidateV83'].get('v49GenerationActivePaidQty') or 0) for x in rows),'activeExpandFillQty':sum(float(x['candidateV83'].get('v64FillQty') or 0) for x in rows),'economicAdmissionAllows':sum(int(x['candidateV83'].get('v83AdmissionAllows') or 0) for x in rows),'economicAdmissionBlocks':sum(int(x['candidateV83'].get('v83AdmissionBlocks') or 0) for x in rows)},'rows':rows,'boundary':['V83 code frozen before market settlement','winner post-hoc only','no tuning','realistic HFT only','no 8781','fresh4 is architecture replication, not profitability graduation']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':out['aggregate'],'gates':gates,'safety':s},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
