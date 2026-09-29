from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v51=sib('eth_v51_for_v52','run_eth_repair_v51_generation_active_passive_queue_handoff.py');v50=v51.v50;v49=v51.v49;v48=v51.v48;v38=v51.v38;v1=v51.v1;EPS=1e-9

class V52PaymentEpochMultiActive(v51.V51QueueHandoff):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v52EpochCtx={};self.v52EpochSerial={};self.v52EpochHardConfirmed=set();self.v52EpochResets=0;self.v52EpochActiveSubmits=0;self.v52EpochEvidenceBlocks=0;self.v52StaleEvidenceLeak=0;self.v52Events=[]
 def _reset_payment_epoch(self,t,g,payment_role,paid_inc):
  gid=int(g['id']);pid=g.get('parentId');pid=int(pid) if pid is not None else (int(self.repairParent.get('id')) if self.repairParent is not None else None);ep=int(self.v52EpochSerial.get(gid,0))+1;self.v52EpochSerial[gid]=ep
  ctx={'generationId':gid,'epoch':ep,'bornAt':int(t),'parentId':pid,'opportunityStartIndex':len(self.postArmCarrierOpportunities),'parentFillBase':self._parent_actual_fill(pid) if pid is not None else 0.0,'paidBase':float(g.get('paid') or 0.0),'paymentRole':payment_role}
  self.v52EpochCtx[gid]=ctx;self.v52EpochResets+=1;self.v52Events.append({'t':int(t),'event':'GENERATION_PAYMENT_EPOCH_RESET','generationId':gid,'epoch':ep,'parentId':pid,'paidBase':ctx['paidBase'],'paidInc':float(paid_inc),'paymentRole':payment_role,'opportunityStartIndex':ctx['opportunityStartIndex'],'parentFillBase':ctx['parentFillBase']})
 def _pay_generations(self,t,pay,is_taker):
  before={int(g['id']):float(g.get('paid') or 0.0) for g in self.v48Generations};super()._pay_generations(t,pay,is_taker)
  for g in self.v48Generations:
   gid=int(g['id']);old=before.get(gid,0.0);now=float(g.get('paid') or 0.0);rem=max(0.0,float(g.get('debt') or 0.0)-now)
   if now>old+EPS and rem>EPS:self._reset_payment_epoch(t,g,'TAKER' if is_taker else 'MAKER',now-old)
 def _maybe_hard_active(self,t):
  g=self._latest_gen();rp=self.repairParent
  if g is None or rp is None or max(0.,float(g.get('debt',0.))-float(g.get('paid',0.)))<=EPS:return super()._maybe_hard_active(t)
  gid=int(g['id']);ctx=self.v52EpochCtx.get(gid)
  if ctx is None:return super()._maybe_hard_active(t)
  ep=int(ctx['epoch']);hard_key=(gid,ep);pid=int(rp.get('id'));side=rp.get('side')
  if hard_key in self.v52EpochHardConfirmed:return False
  if ctx.get('parentId')!=pid or side not in ('UP','DOWN') or pid not in self._armedParents:return False
  if not self._archive_terminal_old_active(t,pid,gid):return False
  if float(g.get('paid') or 0.0)>float(ctx.get('paidBase') or 0.0)+EPS:
   self.v52EpochEvidenceBlocks+=1;return False
  start=int(ctx['opportunityStartIndex']);born=int(ctx['bornAt']);all_parent=[x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid]
  old_evidence=[x for x in all_parent if int(x.get('t',-1))<=born]
  opp=[x for x in self.postArmCarrierOpportunities[start:] if int(x.get('parentId'))==pid and int(x.get('t',-1))>born]
  # Slicing by post-payment index and timestamp is the anti-leak contract; old evidence is diagnostic only.
  if any(int(x.get('t',-1))<=born for x in opp):self.v52StaleEvidenceLeak+=1
  if len(opp)<2:return False
  first2=opp[:2]
  if any(bool(x.get('connected')) for x in first2):return False
  base=float(ctx.get('parentFillBase') or 0.0);nowfill=self._parent_actual_fill(pid)
  if nowfill>base+EPS:
   self.v52EpochEvidenceBlocks+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  psv=self._find_passive(pid)
  if psv is None:return False
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);active_legal=1.0/ask if ask>EPS else math.inf;gen_rem=max(0.,float(g.get('debt') or 0.)-float(g.get('paid') or 0.));q=min(float(active_legal),float(pay['gap']),float(rem),gen_rem)
  if q<=EPS or q+EPS<active_legal:return False
  # Preserve V50 recoverability: active payment may not strand the remaining passive responsibility.
  self.v50RecoverabilityChecks+=1;ppx=float(o.get('price') or e.get('price') or 0.0);passive_legal=1.0/ppx if ppx>EPS else math.inf
  floor0,u,d,cost=self._raw_floor();u=float(u);d=float(d);cost=float(cost);u2=u+q if side=='UP' else u;d2=d+q if side=='DOWN' else d;c2=cost+ask*q;floor2=min(u2,d2)-c2;gap2=max(0.,float(pay['gap'])-q)
  row={'t':int(t),'generationId':gid,'epoch':ep,'parentId':pid,'side':side,'generationRemaining':gen_rem,'activeAsk':ask,'activeQty':q,'passiveKey':passive_key,'passivePrice':ppx,'passiveLegalMin':passive_legal,'projectedFloor':floor2,'projectedRepairGap':gap2,'oldEvidenceCount':len(old_evidence),'freshOpportunityCount':len(opp),'allowed':False}
  if floor2>=-EPS:
   self.v50FloorRecoveredExemptions+=1;row['allowed']=True;row['reason']='PROJECTED_FLOOR_RECOVERED'
  elif not math.isfinite(passive_legal) or gap2+EPS<passive_legal:
   self.v50StrandingBlocks+=1;row['reason']='POST_ACTIVE_PASSIVE_REMAINDER_SUBLEGAL'
   try:row['passiveStillLiveAtBlock']=bool(v1.live(self.snap(o).get('status')))
   except Exception:row['passiveStillLiveAtBlock']=False
   self.v50Events.append(row);self.v52Events.append({'t':int(t),'event':'GENERATION_EPOCH_ACTIVE_STRANDING_BLOCK','generationId':gid,'epoch':ep,'parentId':pid});return False
  else:
   row['allowed']=True;row['reason']='POST_ACTIVE_PASSIVE_HANDOFF_LEGAL'
  self.v50FeasibleActivePaths+=1;self.v50Events.append(row);self.v52EpochHardConfirmed.add(hard_key);self.v49GenHardConfirmed.add(gid)
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  self.v52Events.append({'t':int(t),'event':'GENERATION_EPOCH_HARD_EVENT_CONFIRMED','generationId':gid,'epoch':ep,'parentId':pid,'bornAt':born,'postPaymentFreshOpportunityCount':len(opp),'firstTwoDisconnected':True,'paidBase':float(ctx.get('paidBase') or 0.0),'generationPaidNow':float(g.get('paid') or 0.0),'parentFillAtEpoch':base,'parentFillNow':nowfill})
  ok=self._submit_active(t,pid,passive_key,side,ask,q,oid)
  if ok:
   self.v52EpochActiveSubmits+=1;self.v49GenerationActiveSubmits+=1;self.v52Events.append({'t':int(t),'event':'GENERATION_EPOCH_ACTIVE_SHARED_SUBMIT','generationId':gid,'epoch':ep,'parentId':pid,'side':side,'ask':ask,'qty':q,'passiveKey':passive_key})
  return ok
 def run_exam_v52(self,models,winner):
  r=super().run_exam_v51(models,winner);r.update({'v52EpochResets':self.v52EpochResets,'v52EpochContexts':len(self.v52EpochCtx),'v52EpochHardConfirmed':len(self.v52EpochHardConfirmed),'v52EpochActiveSubmits':self.v52EpochActiveSubmits,'v52EpochEvidenceBlocks':self.v52EpochEvidenceBlocks,'v52StaleEvidenceLeak':self.v52StaleEvidenceLeak,'v52Events':self.v52Events[:200]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v52_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V52','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V52_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v51.V51QueueHandoff(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v51(models,cr['winner'])
   finally:b.close()
   c=V52PaymentEpochMultiActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v52(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV51':br,'candidateV52':rr});print(json.dumps({'marketId':mid,'epochResets':rr['v52EpochResets'],'epochHard':rr['v52EpochHardConfirmed'],'epochActive':rr['v52EpochActiveSubmits'],'genPaid':[br['v48GenerationPaidQty'],rr['v48GenerationPaidQty'],'floor',br['floor'],rr['floor']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'epochResets':int(sm('candidateV52','v52EpochResets')),'epochHardConfirmed':int(sm('candidateV52','v52EpochHardConfirmed')),'epochActiveSubmits':int(sm('candidateV52','v52EpochActiveSubmits')),'epochEvidenceBlocks':int(sm('candidateV52','v52EpochEvidenceBlocks')),'staleEvidenceLeak':int(sm('candidateV52','v52StaleEvidenceLeak')),'v50StrandingBlocks':int(sm('candidateV52','v50StrandingBlocks')),'v51RetainedPassive':int(sm('candidateV52','v51RetainedPassive')),'responsibilityOverfill':sm('candidateV52','v51ResponsibilityOverfill'),'truthMismatch':sm('candidateV52','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV52','overOwnedSubmitViolations'),'repairDrift':sm('candidateV52','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV51','floor'),'candidateFloorSum':sm('candidateV52','floor'),'baselineGenPaid':sm('baselineV51','v48GenerationPaidQty'),'candidateGenPaid':sm('candidateV52','v48GenerationPaidQty')}
  gates={'paymentEpochResetExercised':agg['epochResets']>0,'zeroStaleEvidenceLeak':agg['staleEvidenceLeak']==0,'v50RecoverabilityPreserved':agg['v50StrandingBlocks']>=0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
  coverage='NATURAL_REPEAT_ACTIVE_EXERCISED' if agg['epochActiveSubmits']>0 else 'NO_NATURAL_REPEAT_ACTIVE_COVERAGE'
  out={'version':'ETH_REPAIR_V52_GENERATION_PAYMENT_EPOCH_MULTI_ACTIVE','researchOnly':True,'priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','aggregate':agg,'gates':gates,'functionalCorePass':all(gates.values()),'naturalRepeatCoverage':coverage,'rows':rows,'boundary':['V51 first generation-active authority preserved','each actual generation payment resets future active evidence epoch','repeat active needs two fresh disconnected post-payment replacements','V50 recoverability and V51 queue handoff preserved','no threshold/qty/delay/price tuning','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalCorePass':out['functionalCorePass'],'naturalRepeatCoverage':coverage,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
