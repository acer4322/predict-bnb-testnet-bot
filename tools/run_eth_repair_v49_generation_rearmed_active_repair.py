from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,math,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v48=sib('eth_v48_for_v49','run_eth_repair_v48_generation_ledger_functional.py');v38=v48.v38;v36=v38.v36;v1=v36.v34.v30.v1;EPS=1e-9

class V49(v48.V48GenerationLedger):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v49EpochByParent={};self.v49Epochs=[];self.v49RearmCount=0;self.v49HardCount=0;self.v49ActiveSubmitCount=0;self.v49ActiveKeys=set();self.v49ActiveFillSeen={};self.v49ActiveFillQty=0.;self.v49ActiveFloorDeltas=[];self.v49BlockedOldActiveLive=0;self.v49BlockedPaymentProgress=0;self.v49BlockedNoLegalSlice=0
 def _active_live(self,a):
  if not a:return False
  o=self.orders.get(a.get('key'))
  if not o:return False
  try:return bool(v1.live(self.snap(o).get('status')))
  except Exception:return False
 def _retire_old_active_if_terminal(self,pid):
  a=self.activeByParent.get(pid)
  if not a:return True
  if self._active_live(a):return False
  self.activeByParent.pop(pid,None);return True
 def _arm_generation_epoch(self,t,g):
  rp=self.repairParent
  if rp is None:return
  pid=int(rp.get('id'));opp=[x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid]
  e={'generationId':int(g['id']),'parentId':pid,'bornAt':int(t),'oppBase':len(opp),'parentFillBase':float(self._parent_actual_fill(pid)),'hardConfirmed':False,'activeSubmitted':False,'activeKey':None,'floorAtBirth':float(self._raw_floor()[0])}
  self.v49EpochByParent[pid]=e;self.v49Epochs.append(e);self.v49RearmCount+=1
  self.activeEvents.append({'event':'V49_GENERATION_REARM','t':int(t),'parentId':pid,'generationId':e['generationId'],'oppBase':e['oppBase'],'parentFillBase':e['parentFillBase'],'floor':e['floorAtBirth']})
 def _sync_parallel_generation_fills(self,t):
  before=self.v48GenerationBirths
  super()._sync_parallel_generation_fills(t)
  if self.v48GenerationBirths>before:
   for g in self.v48Generations[before:]:self._arm_generation_epoch(t,g)
 def _maybe_generation_active(self,t):
  rp=self.repairParent
  if rp is None:return False
  pid=int(rp.get('id'));side=rp.get('side');e=self.v49EpochByParent.get(pid)
  if not e or e.get('hardConfirmed') or e.get('activeSubmitted') or side not in ('UP','DOWN'):return False
  if not self._retire_old_active_if_terminal(pid):self.v49BlockedOldActiveLive+=1;return False
  opp=[x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid]
  post=opp[int(e['oppBase']):]
  if len(post)<2:return False
  first2=post[:2]
  if any(bool(x.get('connected')) for x in first2):return False
  base=float(e['parentFillBase']);now=float(self._parent_actual_fill(pid))
  if now>base+EPS:self.v49BlockedPaymentProgress+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  psv=self._find_passive(pid)
  if psv is None:return False
  passive_key,ce,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(float(legal),float(pay['gap']),float(rem))
  if q<=EPS or q+EPS<legal:self.v49BlockedNoLegalSlice+=1;return False
  oid=ce.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  e['hardConfirmed']=True;self.v49HardCount+=1
  self.activeEvents.append({'event':'V49_GENERATION_HARD_CONFIRMED','t':int(t),'parentId':pid,'generationId':e['generationId'],'postGenerationOpportunityCount':len(post),'firstTwoDisconnected':True,'parentFillAtBirth':base,'parentFillNow':now,'floor':pay['floor'],'gap':pay['gap'],'passiveRemaining':rem,'liveAsk':ask,'legalMinSlice':legal})
  ok=self._submit_active(t,pid,passive_key,side,ask,q,oid)
  if ok:
   e['activeSubmitted']=True;e['activeKey']=self.activeByParent[pid]['key'];self.v49ActiveKeys.add(e['activeKey']);self.v49ActiveSubmitCount+=1
   self.activeEvents.append({'event':'V49_GENERATION_ACTIVE_SUBMIT','t':int(t),'parentId':pid,'generationId':e['generationId'],'key':e['activeKey'],'qty':q,'ask':ask})
  return ok
 def _scan_v49_active(self,t):
  self._refresh_carrier_ledger(t)
  for k in list(self.v49ActiveKeys):
   now=float(self.carrierLedger.get(k,{}).get('actualFilled') or 0.);old=float(self.v49ActiveFillSeen.get(k,0.))
   if now>old+EPS:
    inc=now-old;self.v49ActiveFillQty+=inc
    # V36 has already reconciled the same fill; recover its observed floor delta from the latest matching parent event if possible.
    pid=self.carrierLedger.get(k,{}).get('parentId');cand=[x for x in self.activeEvents if x.get('event')=='V36_ACTIVE_SHARED_FILL' and str(x.get('parentId'))==str(pid) and int(x.get('t',-1))==int(t)]
    if cand:
     d=float(cand[-1].get('floorDelta') or 0.);self.v49ActiveFloorDeltas.append(d)
    self.activeEvents.append({'event':'V49_GENERATION_ACTIVE_FILL','t':int(t),'key':k,'incQty':inc,'parentId':pid})
   self.v49ActiveFillSeen[k]=max(old,now)
 def process(self,t):
  super().process(t);self._scan_v49_active(t);self._maybe_generation_active(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._scan_v49_active(t);self._maybe_generation_active(t)
 def run_exam_v49(self,models,winner):
  r=super().run_exam_v48(models,winner);r.update({'v49GenerationRearms':self.v49RearmCount,'v49GenerationHardConfirmed':self.v49HardCount,'v49GenerationActiveSubmits':self.v49ActiveSubmitCount,'v49GenerationActiveFillQty':self.v49ActiveFillQty,'v49GenerationActiveFloorDeltas':self.v49ActiveFloorDeltas,'v49BlockedOldActiveLive':self.v49BlockedOldActiveLive,'v49BlockedPaymentProgress':self.v49BlockedPaymentProgress,'v49BlockedNoLegalSlice':self.v49BlockedNoLegalSlice,'v49Epochs':self.v49Epochs[:40]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v49_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V49','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V49_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v48.V48GenerationLedger(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v48(models,cr['winner'])
   finally:b.close()
   c=V49(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v49(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV48':br,'candidateV49':rr});print(json.dumps({'marketId':mid,'rearms':rr['v49GenerationRearms'],'hard':rr['v49GenerationHardConfirmed'],'activeSubmits':rr['v49GenerationActiveSubmits'],'activeFillQty':rr['v49GenerationActiveFillQty'],'repairAfter':rr['v44RepairQtyAfterExpand'],'floors':[br['floor'],rr['floor']],'absNet':[br['absNet'],rr['absNet']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  deltas=[float(d) for x in rows for d in x['candidateV49'].get('v49GenerationActiveFloorDeltas',[])]
  agg={'markets':len(rows),'generationRearms':int(sm('candidateV49','v49GenerationRearms')),'hardConfirmed':int(sm('candidateV49','v49GenerationHardConfirmed')),'activeSubmits':int(sm('candidateV49','v49GenerationActiveSubmits')),'activeFillQty':sm('candidateV49','v49GenerationActiveFillQty'),'repairAfterExpandBaseline':sm('baselineV48','v44RepairQtyAfterExpand'),'repairAfterExpandCandidate':sm('candidateV49','v44RepairQtyAfterExpand'),'harmfulActiveFillEvents':sum(d<-EPS for d in deltas),'truthMismatch':sm('candidateV49','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV49','overOwnedSubmitViolations'),'repairDrift':sm('candidateV49','repairToExpandAtFirstFill'),'sharedOverfill':sm('candidateV49','v36SharedRealizedOverfill'),'baselineFloorSum':sm('baselineV48','floor'),'candidateFloorSum':sm('candidateV49','floor'),'baselineAbsNetSum':sm('baselineV48','absNet'),'candidateAbsNetSum':sm('candidateV49','absNet')}
  gates={'generationRearmExercised':agg['generationRearms']>0,'postGenerationHardEvidenceExercised':agg['hardConfirmed']>0,'generationActiveSubmitExercised':agg['activeSubmits']>0,'generationActiveFillExercised':agg['activeFillQty']>EPS,'zeroHarmfulActiveFill':agg['harmfulActiveFillEvents']==0,'zeroSharedOverfill':agg['sharedOverfill']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'repairPaymentNotReduced':agg['repairAfterExpandCandidate']+EPS>=agg['repairAfterExpandBaseline']}
  out={'version':'ETH_REPAIR_V49_GENERATION_REARMED_ACTIVE_REPAIR','researchOnly':True,'behaviorChange':True,'priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['V48 baseline unchanged except active readiness re-armed per materialized parallel-expand generation','inherited V36 two disconnected post-generation replacement confirmations','no same-parent payment between generation birth and hard confirmation','same venue-minimum shared active sizing','no threshold/qty/time tuning','consumed realistic HFT only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
