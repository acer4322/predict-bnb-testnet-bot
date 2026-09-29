from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v44=sib('eth_v44_for_v47','run_eth_repair_v44_coadapted_parallel_reexpand_causal.py');v38=v44.v38;v36=v38.v36;v1=v36.v34.v30.v1;EPS=1e-9
class V47(v44.V44):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v47KeyParent={};self.v47Seen={};self.v47Generation={};self.v47OppStart={};self.v47GenerationStarts=[];self.v47RearmedHard=0;self.v47RearmedActiveFillBase=0.0
 def _score_state(self,t,after_kind):
  n=len(self.v44Decisions);super()._score_state(t,after_kind)
  if len(self.v44Decisions)>n:
   z=self.v44Decisions[-1]
   if z.get('submit') and z.get('key') is not None:self.v47KeyParent[z['key']]=int(z['parentId'])
 def _clean_old_active(self,pid):
  a=self.activeByParent.get(pid)
  if not a:return
  o=self.orders.get(a.get('key'));live=False
  try:live=bool(o and v1.live(self.snap(o).get('status')))
  except Exception:live=False
  if not live:self.activeByParent.pop(pid,None)
 def _start_generation(self,t,pid,key,inc):
  self.v47Generation[pid]=int(self.v47Generation.get(pid,0))+1;g=self.v47Generation[pid]
  opp=[x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid]
  self.v47OppStart[pid]=len(opp);self.armFillBase[pid]=self._parent_actual_fill(pid);self.hardConfirmed.discard(pid);self._clean_old_active(pid);self._armedParents.add(pid)
  self.v47GenerationStarts.append({'t':int(t),'parentId':pid,'generation':g,'expandKey':key,'expandFillQty':float(inc),'repairFillBase':float(self.armFillBase[pid]),'opportunityBaseCount':len(opp),'floor':float(self._current_payoffs()['floor'])})
 def _scan_v44_fills(self,t):
  super()._scan_v44_fills(t);self._refresh_carrier_ledger(t)
  for k in list(self.v44Keys):
   now=float(self.carrierLedger.get(k,{}).get('actualFilled') or 0.0);old=float(self.v47Seen.get(k,0.0))
   if now>old+EPS:
    pid=self.v47KeyParent.get(k)
    if pid is not None:self._start_generation(t,int(pid),k,now-old)
   self.v47Seen[k]=max(old,now)
 def _maybe_hard_active(self,t):
  rp=self.repairParent
  if rp is None:return False
  pid=int(rp.get('id'));side=rp.get('side');self._clean_old_active(pid)
  if pid not in self._armedParents or pid in self.activeByParent or pid in self.hardConfirmed or side not in ('UP','DOWN'):return False
  allopp=[x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid];start=int(self.v47OppStart.get(pid,0));opp=allopp[start:]
  if len(opp)<2:return False
  if any(bool(x.get('connected')) for x in opp[:2]):return False
  base=float(self.armFillBase.get(pid,self._parent_actual_fill(pid)));now=self._parent_actual_fill(pid)
  if now>base+EPS:self.blockedPaymentProgress+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  psv=self._find_passive(pid)
  if psv is None:return False
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(float(legal),float(pay['gap']),float(rem))
  if q<=EPS or q+EPS<legal:self.blockedNoLegalSlice+=1;return False
  self.hardConfirmed.add(pid);self.hardEventConfirmedCount+=1
  if int(self.v47Generation.get(pid,0))>0:self.v47RearmedHard+=1;self.v47RearmedActiveFillBase=self.activeFillQty
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  self.activeEvents.append({'event':'V47_GENERATION_HARD_CONFIRMED','t':int(t),'parentId':pid,'generation':int(self.v47Generation.get(pid,0)),'newOpportunityCount':len(opp),'parentFillBase':base,'parentFillNow':now,'floor':pay['floor'],'gap':pay['gap'],'legalMinSlice':legal})
  return self._submit_active(t,pid,passive_key,side,ask,q,oid)
 def run_exam_v47(self,models,winner):
  r=self.run_exam_v44(models,winner);r.update({'v47GenerationCount':sum(self.v47Generation.values()),'v47GenerationStarts':self.v47GenerationStarts[:80],'v47RearmedHardConfirmed':self.v47RearmedHard,'v47RearmedActiveFillQty':max(0.0,self.activeFillQty-self.v47RearmedActiveFillBase) if self.v47RearmedHard else 0.0});return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','portability-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v47_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V47','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V47_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);teacher=joblib.load(a.portability_model)['models']['EVENT_VALUE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v44.V44(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=teacher)
   try:br=b.run_exam_v44(models,cr['winner'])
   finally:b.close()
   c=V47(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=teacher)
   try:rr=c.run_exam_v47(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV44':br,'candidateV47':rr});print(json.dumps({'marketId':mid,'gen':rr['v47GenerationCount'],'rearm':rr['v47RearmedHardConfirmed'],'activeFill':rr['v36ActiveFillQty'],'reexpandFill':rr['v44ActualFillQty'],'repairAfter':rr['v44RepairQtyAfterExpand'],'floors':[br['floor'],rr['floor']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'generationCount':int(sm('candidateV47','v47GenerationCount')),'rearmedHardConfirmed':int(sm('candidateV47','v47RearmedHardConfirmed')),'activeFillQty':sm('candidateV47','v36ActiveFillQty'),'reexpandFillQty':sm('candidateV47','v44ActualFillQty'),'repairQtyAfterExpand':sm('candidateV47','v44RepairQtyAfterExpand'),'baselineFloorSum':sm('baselineV44','floor'),'candidateFloorSum':sm('candidateV47','floor'),'baselineAbsNetSum':sm('baselineV44','absNet'),'candidateAbsNetSum':sm('candidateV47','absNet'),'truthMismatch':sm('candidateV47','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV47','overOwnedSubmitViolations'),'repairDrift':sm('candidateV47','repairToExpandAtFirstFill'),'sharedOverfill':sm('candidateV47','v36SharedRealizedOverfill')}
  out={'version':'ETH_REPAIR_V47_GENERATION_AWARE_REPAIR_CAUSAL','researchOnly':True,'behaviorChange':True,'aggregate':agg,'rows':rows,'boundary':['single architectural change over V44: actual V44 Expand fill starts a new Repair responsibility generation','new generation rebases Repair payment and post-generation replacement evidence','same V36 two-disconnected-opportunity + zero-payment trigger retained','EVENT_VALUE_NORM V44 teacher unchanged','no threshold tuning','winner/PnL excluded from trigger','realistic HFT only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
