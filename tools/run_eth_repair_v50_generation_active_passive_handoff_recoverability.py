from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v49=sib('eth_v49_for_v50','run_eth_repair_v49_generation_scoped_active_rearm.py');v48=v49.v48;v38=v49.v38;EPS=1e-9;v1=v49.v1

class V50HandoffRecoverable(v49.V49GenerationScopedActive):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v50RecoverabilityChecks=0;self.v50StrandingBlocks=0;self.v50FeasibleActivePaths=0;self.v50FloorRecoveredExemptions=0;self.v50NoLegalGenerationSlice=0;self.v50Events=[]
 def _maybe_hard_active(self,t):
  g=self._latest_gen();rp=self.repairParent
  if g is None or max(0.,float(g.get('debt',0.))-float(g.get('paid',0.)))<=EPS or rp is None:
   return super()._maybe_hard_active(t)
  gid=int(g['id']);ctx=self.v49RearmContexts.get(gid);pid=int(rp.get('id'));side=rp.get('side')
  if ctx is None or ctx.get('parentId')!=pid or side not in ('UP','DOWN'):return super()._maybe_hard_active(t)
  if gid in self.v49GenHardConfirmed:return False
  if pid not in self._armedParents:return False
  if not self._archive_terminal_old_active(t,pid,gid):return False
  if float(g.get('paid') or 0.0)>EPS:self.v49BlockedGenerationPaymentProgress+=1;return False
  start=int(ctx['opportunityStartIndex']);born=int(ctx['bornAt']);opp=[x for x in self.postArmCarrierOpportunities[start:] if int(x.get('parentId'))==pid and int(x.get('t',-1))>born]
  if any(int(x.get('t',-1))<=born for x in opp):self.v49PreBirthOpportunityLeak+=1
  if len(opp)<2:return False
  first2=opp[:2]
  if any(bool(x.get('connected')) for x in first2):return False
  base=float(ctx.get('parentFillBase') or 0.0);now=self._parent_actual_fill(pid)
  if now>base+EPS:self.v49BlockedGenerationPaymentProgress+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  psv=self._find_passive(pid)
  if psv is None:return False
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);active_legal=1.0/ask if ask>EPS else math.inf;gen_rem=max(0.,float(g.get('debt') or 0.)-float(g.get('paid') or 0.));q=min(float(active_legal),float(pay['gap']),float(rem),gen_rem)
  if q<=EPS or q+EPS<active_legal:
   self.v50NoLegalGenerationSlice+=1;return False
  self.v50RecoverabilityChecks+=1
  ppx=float(o.get('price') or e.get('price') or 0.0);passive_legal=1.0/ppx if ppx>EPS else math.inf
  floor0,u,d,cost=self._raw_floor();u=float(u);d=float(d);cost=float(cost)
  u2=u+q if side=='UP' else u;d2=d+q if side=='DOWN' else d;c2=cost+ask*q;floor2=min(u2,d2)-c2;gap2=max(0.,float(pay['gap'])-q)
  row={'t':int(t),'generationId':gid,'parentId':pid,'side':side,'generationRemaining':gen_rem,'activeAsk':ask,'activeQty':q,'passiveKey':passive_key,'passivePrice':ppx,'passiveLegalMin':passive_legal,'projectedFloor':floor2,'projectedRepairGap':gap2,'allowed':False}
  if floor2>=-EPS:
   self.v50FloorRecoveredExemptions+=1;row['allowed']=True;row['reason']='PROJECTED_FLOOR_RECOVERED'
  elif not math.isfinite(passive_legal) or gap2+EPS<passive_legal:
   self.v50StrandingBlocks+=1;row['reason']='POST_ACTIVE_PASSIVE_REMAINDER_SUBLEGAL'
   try:row['passiveStillLiveAtBlock']=bool(v1.live(self.snap(o).get('status')))
   except Exception:row['passiveStillLiveAtBlock']=False
   self.v50Events.append(row);return False
  else:
   row['allowed']=True;row['reason']='POST_ACTIVE_PASSIVE_HANDOFF_LEGAL'
  self.v50FeasibleActivePaths+=1;self.v50Events.append(row)
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  self.v49GenHardConfirmed.add(gid);self.v49Events.append({'t':int(t),'event':'GENERATION_HARD_EVENT_CONFIRMED','generationId':gid,'parentId':pid,'side':side,'bornAt':born,'postBirthOpportunityCount':len(opp),'firstTwoDisconnected':True,'parentFillAtBirth':base,'parentFillNow':now,'generationPaid':float(g.get('paid') or 0.0),'liveAsk':ask,'legalMinSlice':active_legal})
  ok=self._submit_active(t,pid,passive_key,side,ask,q,oid)
  if ok:self.v49GenerationActiveSubmits+=1;self.v49Events.append({'t':int(t),'event':'GENERATION_ACTIVE_SHARED_SUBMIT','generationId':gid,'parentId':pid,'side':side,'ask':ask,'qty':q,'passiveKey':passive_key})
  return ok
 def run_exam_v50(self,models,winner):
  r=super().run_exam_v49(models,winner);r.update({'v50RecoverabilityChecks':self.v50RecoverabilityChecks,'v50StrandingBlocks':self.v50StrandingBlocks,'v50FeasibleActivePaths':self.v50FeasibleActivePaths,'v50FloorRecoveredExemptions':self.v50FloorRecoveredExemptions,'v50NoLegalGenerationSlice':self.v50NoLegalGenerationSlice,'v50Events':self.v50Events[:120]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v50_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V50','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V50_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v49.V49GenerationScopedActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v49(models,cr['winner'])
   finally:b.close()
   c=V50HandoffRecoverable(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v50(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV49':br,'candidateV50':rr});print(json.dumps({'marketId':mid,'checks':rr['v50RecoverabilityChecks'],'blocks':rr['v50StrandingBlocks'],'feasible':rr['v50FeasibleActivePaths'],'activePaid':rr['v49GenerationActivePaidQty'],'genPaid':[br['v48GenerationPaidQty'],rr['v48GenerationPaidQty']],'floor':[br['floor'],rr['floor']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'checks':int(sm('candidateV50','v50RecoverabilityChecks')),'strandingBlocks':int(sm('candidateV50','v50StrandingBlocks')),'feasibleActivePaths':int(sm('candidateV50','v50FeasibleActivePaths')),'generationActivePaidQty':sm('candidateV50','v49GenerationActivePaidQty'),'preBirthOpportunityLeak':int(sm('candidateV50','v49PreBirthOpportunityLeak')),'sharedOverfill':sm('candidateV50','v36SharedRealizedOverfill'),'truthMismatch':sm('candidateV50','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV50','overOwnedSubmitViolations'),'repairDrift':sm('candidateV50','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV49','floor'),'candidateFloorSum':sm('candidateV50','floor')}
  blocked_live=sum(1 for x in rows for e in x['candidateV50'].get('v50Events',[]) if e.get('reason')=='POST_ACTIVE_PASSIVE_REMAINDER_SUBLEGAL' and e.get('passiveStillLiveAtBlock'))
  agg['blockedWithPassiveStillLive']=blocked_live
  gates={'recoverabilityGateExercised':agg['checks']>0,'feasibleGenerationActiveRemains':agg['feasibleActivePaths']>0 and agg['generationActivePaidQty']>EPS,'strandingPathBlocked':agg['strandingBlocks']>0,'blockedPathLeavesPassiveCarrierLive':blocked_live==agg['strandingBlocks'],'zeroPreBirthOpportunityLeak':agg['preBirthOpportunityLeak']==0,'zeroSharedOverfill':agg['sharedOverfill']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V50_GENERATION_ACTIVE_PASSIVE_HANDOFF_RECOVERABILITY','researchOnly':True,'priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['same V36 active minimum slice','current passive carrier price defines legal handoff minimum','no fitted threshold/qty/delay','blocked active leaves passive untouched','realistic HFT consumed functional smoke','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
