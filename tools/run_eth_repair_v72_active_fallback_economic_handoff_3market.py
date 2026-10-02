from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v72=sib('eth_v72handoff_for_3m','run_eth_repair_v72_active_fallback_economic_handoff_smoke.py');v70g=v72.v70g;v38=v72.v38
FIXED=[1823769,1827223,1827903]

def responsibilities(rr):
 ev=sorted(rr.get('v53FillEvents') or [],key=lambda x:(int(x.get('t') or 0),str(x.get('key'))));last=None;n=0
 for x in ev:
  if str(x.get('role')) not in ('PASSIVE_EXPAND','ACTIVE_EXPAND'):continue
  s=str(x.get('side'))
  if last is None or s!=last:n+=1;last=s
 return n

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(f'fixed cohort must be {FIXED}, got {mids}')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v72handoff3_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF_3M','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF_3M_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v70g.V70GGenerationScopedRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v70g(models,cr['winner'])
   finally:b.close()
   c=v72.V72EconomicFallback(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v72(models,cr['winner'])
   finally:c.close()
   rb=responsibilities(br);rc=responsibilities(rr);rows.append({'marketId':mid,'baselineV70G':br,'candidateV72':rr,'baselinePersistentResponsibilities':rb,'candidatePersistentResponsibilities':rc});print(json.dumps({'marketId':mid,'blocks':rr['v72EconomicBlocks'],'allows':rr['v72EconomicAllows'],'noEvidence':rr['v72NoEvidenceInherited'],'activeFill':[br['v64FillQty'],rr['v64FillQty']],'rawRounds':[br['v64Rounds'],rr['v64Rounds']],'persistentResponsibilities':[rb,rc],'floor':[br['floor'],rr['floor']],'pnlDiagnosticOnly':[br['pnlDiagnosticOnly'],rr['pnlDiagnosticOnly']],'events':rr['v72Events']},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.0) for x in rows)
  bad_allowed=[];late=[]
  for x in rows:
   rr=x['candidateV72']
   for e in rr.get('v72Events') or []:
    if e.get('event')=='ACTIVE_FALLBACK_ECONOMIC_ALLOW' and float(e.get('pairSum') or 0)>1.0+EPS:bad_allowed.append({'marketId':x['marketId'],**e})
   # inherited V64 itself blocks <=180; record any actual submit if result exposes a late one.
   for e in rr.get('v64Events') or []:
    if e.get('event')=='ACTIVE_EXPAND_FALLBACK_SUBMIT' and int(rr.get('windowEndMs') or 0)>0 and int(rr['windowEndMs'])-int(e.get('t') or 0)<=180000:late.append({'marketId':x['marketId'],**e})
  base_resp=sum(int(x['baselinePersistentResponsibilities']) for x in rows);cand_resp=sum(int(x['candidatePersistentResponsibilities']) for x in rows)
  agg={'markets':len(rows),'economicBlocks':int(sm('candidateV72','v72EconomicBlocks')),'economicAllows':int(sm('candidateV72','v72EconomicAllows')),'noEvidenceInherited':int(sm('candidateV72','v72NoEvidenceInherited')),'baselineActiveFallbackFillQty':sm('baselineV70G','v64FillQty'),'candidateActiveFallbackFillQty':sm('candidateV72','v64FillQty'),'baselineRawRounds':int(sm('baselineV70G','v64Rounds')),'candidateRawRounds':int(sm('candidateV72','v64Rounds')),'baselinePersistentResponsibilities':base_resp,'candidatePersistentResponsibilities':cand_resp,'truthMismatch':sm('candidateV72','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV72','overOwnedSubmitViolations'),'responsibilityOverfill':sm('candidateV72','v51ResponsibilityOverfill'),'repairDrift':sm('candidateV72','repairToExpandAtFirstFill'),'creditReuse':sm('candidateV72','v72CreditReuse'),'baselineFloorSum':sm('baselineV70G','floor'),'candidateFloorSum':sm('candidateV72','floor'),'baselinePnlDiagnosticOnly':sm('baselineV70G','pnlDiagnosticOnly'),'candidatePnlDiagnosticOnly':sm('candidateV72','pnlDiagnosticOnly')}
  gates={'allThreeMarketsComplete':len(rows)==3,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroRepairDrift':agg['repairDrift']==0,'zeroCreditReuse':agg['creditReuse']<=EPS,'zeroLateActiveFallback':len(late)==0,'noPairGt1GovernedActiveFallbackFill':len(bad_allowed)==0,'persistentResponsibilityCountNonDecreasingAggregate':cand_resp>=base_resp,'atLeastOneEconomicBlockExercised':agg['economicBlocks']>=1}
  decision='KEEP_V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF_AUTHORIZE_STAGEA16_SHADOW' if all(gates.values()) else 'REJECT_OR_LOCALIZE_V72_3MARKET_REGRESSION'
  out={'version':'ETH_REPAIR_V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF_3MARKET','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'actionAuthority':'FIXED_3_MARKET_REPLICATION_ONLY','fixedCohort':FIXED,'aggregate':agg,'gates':gates,'decision':decision,'badAllowedEvents':bad_allowed,'lateActiveFallbackEvents':late,'rows':rows,'boundary':['exact passed one-market V72EconomicFallback','V44/V65 passive/model authority frozen','pair sum <=1 structural, not tuned','raw token rounds diagnostic only','persistent surplus-side responsibility is structural no-regression metric','realistic HFT/Predict Tape only','no winner/PnL gate','no H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'gates':gates,'perMarket':[{'marketId':x['marketId'],'blocks':x['candidateV72']['v72EconomicBlocks'],'allows':x['candidateV72']['v72EconomicAllows'],'noEvidence':x['candidateV72']['v72NoEvidenceInherited'],'rawRounds':[x['baselineV70G']['v64Rounds'],x['candidateV72']['v64Rounds']],'persistent':[x['baselinePersistentResponsibilities'],x['candidatePersistentResponsibilities']],'floor':[x['baselineV70G']['floor'],x['candidateV72']['floor']]} for x in rows]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
