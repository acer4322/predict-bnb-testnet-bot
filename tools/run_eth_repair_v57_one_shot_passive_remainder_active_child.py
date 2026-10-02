from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v56=sib('eth_v56_for_v57','run_eth_repair_v56_target_book_hazard_parallel_residual_shadow.py');v38=v56.v38;v1=v56.v1;EPS=1e-9

class V57OneShotActive(v56.V56HazardParallelShadow):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v57Fired=set();self.v57Keys=set();self.v57GateHits=0;self.v57Submits=0;self.v57RepeatBlocks=0;self.v57Events=[]
 def _responsibility(self):
  rp=self.repairParent
  if rp is None:return None
  pay=self._current_payoffs();pay_gap=max(0.,float(pay.get('gap') or 0.));g=self._latest_gen();grem=max(0.,float(g.get('debt') or 0.)-float(g.get('paid') or 0.)) if g is not None else 0.;use_gen=g is not None and grem>EPS;rem=grem if use_gen else pay_gap
  if rem<=EPS:return None
  pid=int(rp.get('id'));gid=int(g['id']) if use_gen else -pid;ctx=self.v52EpochCtx.get(int(g['id'])) if use_gen else None;ep=int(ctx['epoch']) if ctx is not None else 0
  return rp,g,use_gen,rem,pid,gid,ep,(gid,ep)
 def _maybe_hard_active(self,t):
  z=self._responsibility()
  if z is None:return super()._maybe_hard_active(t)
  rp,g,use_gen,rem,pid,gid,ep,key=z
  if int(self.capEnd)-int(t)<=180000:return super()._maybe_hard_active(t)
  self._sync_hazard_fills();x,vals=self._feature_vector(t)
  if x is None:return super()._maybe_hard_active(t)
  score=float(self.v56Model.predict_proba(x.reshape(1,-1))[0,1])
  if score<0.5:return super()._maybe_hard_active(t)
  feas=self._parallel_feasible(t,rem,rp)
  if not feas.get('feasible'):return super()._maybe_hard_active(t)
  prem=float(feas.get('passiveRemaining') or 0.);plegal=float(feas.get('passiveLegalMin') or math.inf)
  if not math.isfinite(plegal) or plegal<=EPS:return super()._maybe_hard_active(t)
  depth=int(math.floor(prem/plegal+1e-9))
  if depth!=1:return super()._maybe_hard_active(t)
  self.v57GateHits+=1
  if key in self.v57Fired:
   self.v57RepeatBlocks+=1;return super()._maybe_hard_active(t)
  psv=self._find_passive(pid)
  if psv is None:return super()._maybe_hard_active(t)
  passive_key,e,live_rem,o=psv;side=rp.get('side');ask=float(feas.get('activeAsk') or 0.);q=float(feas.get('qty') or 0.)
  if side not in ('UP','DOWN') or ask<=EPS or q<=EPS:return super()._maybe_hard_active(t)
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  n0=self.n
  ok=self._submit_active(t,pid,passive_key,side,ask,q,oid)
  ev={'t':int(t),'event':'V57_ONE_SHOT_ACTIVE_SUBMIT' if ok else 'V57_ONE_SHOT_ACTIVE_SUBMIT_FAILED','responsibilityKind':'V48_GENERATION' if use_gen else 'REPAIR_PARENT','generationId':gid,'epoch':ep,'parentId':pid,'hazard':score,'replacementDepth':depth,'passiveRemaining':prem,'passiveLegalMin':plegal,'activeAsk':ask,'activeQty':q,'projectedFloor':feas.get('projectedFloor'),'projectedRepairGap':feas.get('projectedRepairGap'),'passiveKey':passive_key}
  self.v57Events.append(ev)
  if ok:
   self.v57Fired.add(key);self.v57Submits+=1;self.v57Keys.add(f'{side}_{n0}');return True
  return super()._maybe_hard_active(t)
 def run_exam_v57(self,models,winner):
  r=super().run_exam_v56(models,winner);fills=[x for x in r.get('v53FillEvents',[]) if str(x.get('key')) in self.v57Keys and x.get('role')=='ACTIVE_REPAIR'];r.update({'v57GateHits':self.v57GateHits,'v57ResponsibilityFired':len(self.v57Fired),'v57Submits':self.v57Submits,'v57RepeatBlocks':self.v57RepeatBlocks,'v57ActiveFillEvents':len(fills),'v57ActiveFillQty':sum(float(x.get('qty') or 0.) for x in fills),'v57Keys':sorted(self.v57Keys),'v57Events':self.v57Events[:120]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','hazard-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v57_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V57','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V57_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];haz=joblib.load(a.hazard_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v56.V56HazardParallelShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,hazard_art=haz)
   try:br=b.run_exam_v56(models,cr['winner'])
   finally:b.close()
   c=V57OneShotActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,hazard_art=haz)
   try:rr=c.run_exam_v57(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV56':br,'candidateV57':rr});print(json.dumps({'marketId':mid,'gate':rr['v57GateHits'],'submit':rr['v57Submits'],'fill':rr['v57ActiveFillEvents'],'baseTerminal':br['v34ParentTerminalUnresolved'],'candTerminal':rr['v34ParentTerminalUnresolved'],'basePassiveCompleted':br['v34ParentPassiveCompleted'],'candPassiveCompleted':rr['v34ParentPassiveCompleted'],'floor':[br['floor'],rr['floor']],'pnl':[br['pnlDiagnosticOnly'],rr['pnlDiagnosticOnly']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  rescued=sum(int(x['baselineV56'].get('v34ParentTerminalUnresolved') or 0)>int(x['candidateV57'].get('v34ParentTerminalUnresolved') or 0) for x in rows);regressed=sum(int(x['candidateV57'].get('v34ParentTerminalUnresolved') or 0)>int(x['baselineV56'].get('v34ParentTerminalUnresolved') or 0) for x in rows)
  agg={'markets':len(rows),'gateHits':int(sm('candidateV57','v57GateHits')),'responsibilityFired':int(sm('candidateV57','v57ResponsibilityFired')),'submits':int(sm('candidateV57','v57Submits')),'activeFillEvents':int(sm('candidateV57','v57ActiveFillEvents')),'activeFillQty':sm('candidateV57','v57ActiveFillQty'),'baselineTerminalParents':int(sm('baselineV56','v34ParentTerminalUnresolved')),'candidateTerminalParents':int(sm('candidateV57','v34ParentTerminalUnresolved')),'baselinePassiveCompletedParents':int(sm('baselineV56','v34ParentPassiveCompleted')),'candidatePassiveCompletedParents':int(sm('candidateV57','v34ParentPassiveCompleted')),'marketsRescuedByTerminalCount':rescued,'marketsRegressedByTerminalCount':regressed,'baselineFloorSum':sm('baselineV56','floor'),'candidateFloorSum':sm('candidateV57','floor'),'baselineAbsNetSum':sm('baselineV56','absNet'),'candidateAbsNetSum':sm('candidateV57','absNet'),'baselinePnlDiagnostic':sm('baselineV56','pnlDiagnosticOnly'),'candidatePnlDiagnostic':sm('candidateV57','pnlDiagnosticOnly'),'responsibilityOverfill':sm('candidateV57','v51ResponsibilityOverfill'),'truthMismatch':sm('candidateV57','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV57','overOwnedSubmitViolations'),'repairDrift':sm('candidateV57','repairToExpandAtFirstFill'),'passiveCancelBeforeActiveFill':sm('candidateV57','v36PassiveCancelBeforeActiveFill')}
  gates={'v57Exercised':agg['submits']>0 and agg['activeFillEvents']>0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroPassiveCancelBeforeActiveFill':agg['passiveCancelBeforeActiveFill']==0}
  out={'version':'ETH_REPAIR_V57_ONE_SHOT_PASSIVE_REMAINDER_ACTIVE_CHILD','researchOnly':True,'developmentOnly':True,'aggregate':agg,'gates':gates,'mechanicalSafetyPass':all(gates.values()),'rows':rows,'boundary':['consumed D1 high-feasible cohort only','frozen Target 1s hazard p>=0.5','natural replacementDepth=floor(passiveRemaining/passiveLegalMin)==1','one early V57 submit per responsibility key','V50/V51 shared-budget recoverability preserved','existing V52 path remains fallback','no winner/PnL trigger','no threshold/qty/delay tuning','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'gates':gates,'mechanicalSafetyPass':out['mechanicalSafetyPass']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
