from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v76=sib('eth_v76_for_fresh4','run_eth_repair_v76_recoverability_gated_active_handoff_fresh1911708.py');v70g=v76.v70g;v38=v76.v38
FIXED=[1911716,1912007,1912012,1912015]
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(f'fixed cohort must be {FIXED}, got {mids}')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v76_fresh4_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V76_FRESH_UNSEEN4','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V76_FRESH_UNSEEN4_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v70g.V70GGenerationScopedRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v70g(models,cr['winner'])
   finally:b.close()
   c=v76.V76RecoverabilityGate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v76(models,cr['winner'])
   finally:c.close()
   blocked={(int(x.get('t') or 0),str(x.get('sourceKey'))) for x in rr.get('v76Events',[]) if x.get('event')=='RECOVERABILITY_ACTIVE_HANDOFF_BLOCK'}
   submits={(int(x.get('t') or 0),str(x.get('sourceKey'))) for x in rr.get('v64Events',[]) if x.get('event')=='ACTIVE_EXPAND_FALLBACK_SUBMIT'}
   blockLeak=len(blocked & submits)
   late=sum(1 for x in rr.get('v64Events',[]) if x.get('event')=='ACTIVE_EXPAND_FALLBACK_SUBMIT' and int(cr['windowEndMs'])-int(x.get('t') or 0)<=180000)
   row={'marketId':mid,'winnerPostHoc':cr['winner'],'baseline':br,'candidate':rr,'blockLeak':blockLeak,'lateActiveSubmits':late};rows.append(row)
   print(json.dumps({'marketId':mid,'winner':cr['winner'],'baseline':{'pnl':br.get('pnlDiagnosticOnly'),'floor':br.get('floor'),'fills':br.get('actualFillEvents'),'rounds':br.get('v70dSemanticRounds')},'candidate':{'pnl':rr.get('pnlDiagnosticOnly'),'floor':rr.get('floor'),'fills':rr.get('actualFillEvents'),'rounds':rr.get('v70dSemanticRounds'),'thesisBirths':rr.get('v75PreSafeThesisBirths'),'v44Submits':rr.get('v44Submits'),'v44FillQty':rr.get('v44ActualFillQty'),'activeBlocks':rr.get('v76ActiveBlocks'),'activeAllows':rr.get('v76ActiveAllows'),'activeFillQty':rr.get('v64FillQty')},'blockLeak':blockLeak},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0) for x in rows)
  safety={'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'preBirthPaymentLeak':sm('candidate','v70dPreBirthPaymentLeak'),'duplicateGenerationDebt':sm('candidate','v70dDuplicateGenerationDebt')}
  context=sum(int(x['candidate'].get('v75PreSafeThesisBirths') or 0)+int(x['candidate'].get('v76ActiveBlocks') or 0)+int(x['candidate'].get('v76ActiveAllows') or 0) for x in rows)
  gates={'marketsComplete':len(rows)==4,'zeroTruthMismatch':safety['truthMismatch']==0,'zeroOverOwned':safety['overOwned']==0,'zeroResponsibilityOverfill':safety['responsibilityOverfill']<=EPS,'zeroRepairDrift':safety['repairDrift']==0,'zeroPreBirthPaymentLeak':safety['preBirthPaymentLeak']<=EPS,'zeroDuplicateGenerationDebt':safety['duplicateGenerationDebt']<=EPS,'oneResponsibilityPerGeneration':all(int(x['candidate'].get('v70gMaxResponsibilitiesPerGeneration') or 0)<=1 for x in rows),'noUnrecoverableActiveFallbackSubmitLeak':sum(x['blockLeak'] for x in rows)==0,'noLateActiveFallback':sum(x['lateActiveSubmits'] for x in rows)==0}
  decision=('KEEP_V76_ARCHITECTURE_BROADER_FRESH_TEST' if all(gates.values()) and context>0 else 'SAFE_BUT_INCONCLUSIVE_NO_V76_CONTEXT' if all(gates.values()) else 'REJECT_V76_FRESH_REPLICATION')
  active=[x for x in rows if float(x['candidate'].get('buyNotional') or 0)>EPS]
  diag={'markets':4,'v76SpecificContexts':context,'activeMarkets':len(active),'candidatePnlSum':sum(float(x['candidate'].get('pnlDiagnosticOnly') or 0) for x in rows),'candidateFloorSum':sum(float(x['candidate'].get('floor') or 0) for x in rows),'candidatePositiveOrZeroFloorMarkets':sum(float(x['candidate'].get('floor') or 0)>=-EPS for x in rows),'baselinePnlSum':sum(float(x['baseline'].get('pnlDiagnosticOnly') or 0) for x in rows),'baselineFloorSum':sum(float(x['baseline'].get('floor') or 0) for x in rows),'preSafeThesisBirths':int(sm('candidate','v75PreSafeThesisBirths')),'activeBlocks':int(sm('candidate','v76ActiveBlocks')),'activeAllows':int(sm('candidate','v76ActiveAllows')),'activeFallbackFillQty':sm('candidate','v64FillQty')}
  out={'version':'ETH_REPAIR_V76_FRESH_UNSEEN4_REPLICATION','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'fixedCohort':FIXED,'gates':gates,'decision':decision,'safety':safety,'diagnostics':diag,'rows':rows,'boundary':['fresh unseen4 after 1911713','winner post-hoc diagnostic only','strict-past V44/signal/recoverability runtime','no Target action clock','no numeric tuning','realistic-HFT/Predict Tape','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'diagnostics':diag,'safety':safety},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
