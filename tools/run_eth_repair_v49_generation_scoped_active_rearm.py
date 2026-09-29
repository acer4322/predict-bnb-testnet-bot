from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v48=sib('eth_v48_for_v49','run_eth_repair_v48_generation_ledger_functional.py');v38=v48.v38;EPS=1e-9
v1=v38.v36.v1

class V49GenerationScopedActive(v48.V48GenerationLedger):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v49RearmContexts={};self.v49GenHardConfirmed=set();self.v49GenerationActiveSubmits=0
  self.v49BlockedOlderActiveLive=0;self.v49BlockedGenerationPaymentProgress=0;self.v49PreBirthOpportunityLeak=0
  self.v49Events=[]
 def _active_live(self,pid):
  a=self.activeByParent.get(pid)
  if not a:return False
  o=self.orders.get(a.get('key'))
  if not o:return False
  try:return bool(v1.live(self.snap(o).get('status')))
  except Exception:return False
 def _archive_terminal_old_active(self,t,pid,gid):
  a=self.activeByParent.get(pid)
  if not a:return True
  if self._active_live(pid):
   self.v49BlockedOlderActiveLive+=1;return False
  self.v49Events.append({'t':int(t),'event':'GENERATION_ARCHIVE_OLDER_ACTIVE','generationId':gid,'parentId':pid,'activeKey':a.get('key'),'fillSeen':float(a.get('fillSeen') or 0.0)})
  self.activeByParent.pop(pid,None);return True
 def _sync_parallel_generation_fills(self,t):
  before=set(self.v48GenByKey)
  super()._sync_parallel_generation_fills(t)
  for k,g in self.v48GenByKey.items():
   if k in before or int(g['id']) in self.v49RearmContexts:continue
   rp=self.repairParent;pid=int(rp.get('id')) if rp is not None else None
   ctx={'generationId':int(g['id']),'key':k,'bornAt':int(g['bornAt']),'parentId':pid,'opportunityStartIndex':len(self.postArmCarrierOpportunities),'parentFillBase':self._parent_actual_fill(pid) if pid is not None else 0.0}
   self.v49RearmContexts[int(g['id'])]=ctx;g['parentId']=pid
   self.v49Events.append({'t':int(t),'event':'GENERATION_ACTIVE_REARM_CONTEXT','generationId':int(g['id']),'parentId':pid,'opportunityStartIndex':ctx['opportunityStartIndex'],'parentFillBase':ctx['parentFillBase']})
 def _maybe_hard_active(self,t):
  g=self._latest_gen();rp=self.repairParent
  if g is None or max(0.,float(g.get('debt',0.))-float(g.get('paid',0.)))<=EPS or rp is None:
   return super()._maybe_hard_active(t)
  gid=int(g['id']);ctx=self.v49RearmContexts.get(gid);pid=int(rp.get('id'));side=rp.get('side')
  if ctx is None or ctx.get('parentId')!=pid or side not in ('UP','DOWN'):
   return super()._maybe_hard_active(t)
  if gid in self.v49GenHardConfirmed:return False
  if pid not in self._armedParents:return False
  if not self._archive_terminal_old_active(t,pid,gid):return False
  if float(g.get('paid') or 0.0)>EPS:
   self.v49BlockedGenerationPaymentProgress+=1;return False
  start=int(ctx['opportunityStartIndex']);born=int(ctx['bornAt'])
  opp=[x for x in self.postArmCarrierOpportunities[start:] if int(x.get('parentId'))==pid and int(x.get('t',-1))>born]
  # Any event at/before generation birth is old-generation evidence and must never count.
  if any(int(x.get('t',-1))<=born for x in opp):self.v49PreBirthOpportunityLeak+=1
  if len(opp)<2:return False
  first2=opp[:2]
  if any(bool(x.get('connected')) for x in first2):return False
  base=float(ctx.get('parentFillBase') or 0.0);now=self._parent_actual_fill(pid)
  if now>base+EPS:
   self.v49BlockedGenerationPaymentProgress+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  psv=self._find_passive(pid)
  if psv is None:return False
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(float(legal),float(pay['gap']),float(rem))
  if q<=EPS or q+EPS<legal:return False
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  self.v49GenHardConfirmed.add(gid)
  self.v49Events.append({'t':int(t),'event':'GENERATION_HARD_EVENT_CONFIRMED','generationId':gid,'parentId':pid,'side':side,'bornAt':born,'postBirthOpportunityCount':len(opp),'firstTwoDisconnected':True,'parentFillAtBirth':base,'parentFillNow':now,'generationPaid':float(g.get('paid') or 0.0),'liveAsk':ask,'legalMinSlice':legal})
  ok=self._submit_active(t,pid,passive_key,side,ask,q,oid)
  if ok:
   self.v49GenerationActiveSubmits+=1;self.v49Events.append({'t':int(t),'event':'GENERATION_ACTIVE_SHARED_SUBMIT','generationId':gid,'parentId':pid,'side':side,'ask':ask,'qty':q,'passiveKey':passive_key})
  return ok
 def run_exam_v49(self,models,winner):
  r=super().run_exam_v48(models,winner)
  taker_paid=sum(float(x.get('paidQty') or 0.0) for x in self.v48PaymentEvents if x.get('executionRole')=='TAKER')
  r.update({'v49RearmContexts':len(self.v49RearmContexts),'v49GenerationHardConfirmed':len(self.v49GenHardConfirmed),'v49GenerationActiveSubmits':self.v49GenerationActiveSubmits,'v49GenerationActivePaidQty':taker_paid,'v49BlockedOlderActiveLive':self.v49BlockedOlderActiveLive,'v49BlockedGenerationPaymentProgress':self.v49BlockedGenerationPaymentProgress,'v49PreBirthOpportunityLeak':self.v49PreBirthOpportunityLeak,'v49Events':self.v49Events[:160]})
  return r

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
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v48.V48GenerationLedger(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v48(models,cr['winner'])
   finally:b.close()
   c=V49GenerationScopedActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v49(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV48':br,'candidateV49':rr})
   print(json.dumps({'marketId':mid,'births':rr['v48GenerationBirths'],'rearm':rr['v49RearmContexts'],'hard':rr['v49GenerationHardConfirmed'],'activeSubmits':rr['v49GenerationActiveSubmits'],'activePaid':rr['v49GenerationActivePaidQty'],'genPaid':[br['v48GenerationPaidQty'],rr['v48GenerationPaidQty']],'floor':[br['floor'],rr['floor']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'generationBirths':int(sm('candidateV49','v48GenerationBirths')),'rearmContexts':int(sm('candidateV49','v49RearmContexts')),'generationHardConfirmed':int(sm('candidateV49','v49GenerationHardConfirmed')),'generationActiveSubmits':int(sm('candidateV49','v49GenerationActiveSubmits')),'generationActivePaidQty':sm('candidateV49','v49GenerationActivePaidQty'),'baselineGenerationPaidQty':sm('baselineV48','v48GenerationPaidQty'),'candidateGenerationPaidQty':sm('candidateV49','v48GenerationPaidQty'),'preBirthOpportunityLeak':int(sm('candidateV49','v49PreBirthOpportunityLeak')),'sharedOverfill':sm('candidateV49','v36SharedRealizedOverfill'),'truthMismatch':sm('candidateV49','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV49','overOwnedSubmitViolations'),'repairDrift':sm('candidateV49','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV48','floor'),'candidateFloorSum':sm('candidateV49','floor')}
  gates={'generationContextBorn':agg['generationBirths']>0 and agg['rearmContexts']==agg['generationBirths'],'zeroPreBirthOpportunityLeak':agg['preBirthOpportunityLeak']==0,'generationHardConfirmExercised':agg['generationHardConfirmed']>0,'generationActiveSubmitExercised':agg['generationActiveSubmits']>0,'generationActivePaymentExercised':agg['generationActivePaidQty']>EPS and agg['candidateGenerationPaidQty']>agg['baselineGenerationPaidQty']+EPS,'zeroSharedOverfill':agg['sharedOverfill']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V49_GENERATION_SCOPED_ACTIVE_REARM','researchOnly':True,'priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['V48 generation ledger preserved','same V36 two-disconnected-replacement semantics reapplied only to post-generation evidence','old live active child blocks rather than being cancelled','no probability/qty/time/price tuning','floor diagnostic only','realistic HFT consumed development cohort','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
