from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v50=sib('eth_v50_for_v51','run_eth_repair_v50_generation_active_passive_handoff_recoverability.py');v49=v50.v49;v48=v50.v48;v38=v50.v38;v1=v50.v1;EPS=1e-9

class V51QueueHandoff(v50.V50HandoffRecoverable):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v51RetainChecks=0;self.v51RetainedPassive=0;self.v51RetainedLive=0;self.v51InheritedResize=0;self.v51RetainedPassiveFillQty=0.;self.v51ResponsibilityOverfill=0.;self.v51Events=[]
 def _submit_active(self,t,pid,passive_key,side,ask,q,objective_id):
  ok=super()._submit_active(t,pid,passive_key,side,ask,q,objective_id)
  if not ok:return False
  g=self._latest_gen();a=self.activeByParent.get(pid)
  if g is not None and a is not None:
   gid=int(g.get('id'))
   ctx=self.v49RearmContexts.get(gid)
   if gid in self.v49GenHardConfirmed and ctx is not None and int(ctx.get('parentId'))==int(pid):
    a['generationId']=gid;a['responsibilityCapAtSubmit']=float(self._current_payoffs()['gap']);a['v51PassiveSeen']=0.;a['v51RetainedPassive']=False
    self.v51Events.append({'t':int(t),'event':'GENERATION_ACTIVE_HANDOFF_SCOPE','generationId':gid,'parentId':int(pid),'passiveKey':passive_key,'responsibilityCapAtSubmit':a['responsibilityCapAtSubmit']})
  return True
 def _reconcile_active(self,t):
  for pid,a in list(self.activeByParent.items()):
   ak=a['key'];pk=a['passiveKey'];ae=self.carrierLedger.get(ak,{});pe=self.carrierLedger.get(pk,{})
   af=float(ae.get('actualFilled') or 0.0);pf=max(0.0,float(pe.get('actualFilled') or 0.0)-float(a.get('passiveBaseActual') or 0.0));old=float(a.get('fillSeen') or 0.0)
   if af>old+EPS:
    before=float(a.get('floorBefore')) if old<=EPS else float(a.get('floorAfter') if a.get('floorAfter') is not None else self._current_payoffs()['floor']);inc=af-old;a['fillSeen']=af;self.activeFillQty+=inc;after=self._current_payoffs()['floor'];a['floorAfter']=after;self.floorFillDeltas.append(after-before);self.activeEvents.append({'event':'V36_ACTIVE_SHARED_FILL','t':int(t),'parentId':pid,'incQty':inc,'cumQty':af,'floorBeforeObserved':before,'floorAfterObserved':after,'floorDelta':after-before,'passiveFillAfterArm':pf})
   if a.get('v51RetainedPassive'):
    seen=float(a.get('v51PassiveSeen') or 0.0)
    if pf>seen+EPS:
     inc=pf-seen;a['v51PassiveSeen']=pf;self.v51RetainedPassiveFillQty+=inc;self.v51Events.append({'t':int(t),'event':'GENERATION_RETAINED_PASSIVE_FILL','generationId':a.get('generationId'),'parentId':pid,'passiveKey':pk,'incQty':inc,'cumPostActivePassiveQty':pf})
   a['maxPassiveAfter']=max(float(a.get('maxPassiveAfter') or 0.0),pf);realized=af+pf
   cap=float(a.get('responsibilityCapAtSubmit') if a.get('generationId') is not None else a.get('qty') or 0.0)
   self.v51ResponsibilityOverfill=max(self.v51ResponsibilityOverfill,max(0.0,realized-cap));
   if a.get('generationId') is None:self.sharedRealizedOverfill=max(self.sharedRealizedOverfill,max(0.0,realized-float(a.get('qty') or 0.0)))
   if af>EPS and not a.get('resizeDone'):
    po=self.orders.get(pk);prem=0.0
    if po:
     try:prem=max(0.0,float(po.get('qty') or 0.0)-float(pe.get('actualFilled') or 0.0))
     except Exception:pass
    retained=False
    if prem>EPS and a.get('generationId') is not None:
     self.v51RetainChecks+=1;pay=self._current_payoffs();ao=self.orders.get(ak);livea=False
     try:livea=bool(ao and v1.live(self.snap(ao).get('status')))
     except Exception:pass
     active_pending=max(0.0,float(a.get('qty') or 0.0)-af) if livea else 0.0;handoff_cap=max(0.0,float(pay['gap'])-active_pending)
     try:plive=bool(v1.live(self.snap(po).get('status')))
     except Exception:plive=False
     if prem<=handoff_cap+EPS and plive:
      retained=True;a['resizeDone']=True;a['v51RetainedPassive']=True;self.v51RetainedPassive+=1;self.v51RetainedLive+=1
      self.v51Events.append({'t':int(t),'event':'GENERATION_PASSIVE_QUEUE_RETAINED_AFTER_ACTIVE_FILL','generationId':a.get('generationId'),'parentId':pid,'passiveKey':pk,'passiveRemaining':prem,'currentRepairGap':float(pay['gap']),'activePending':active_pending,'handoffCapacity':handoff_cap,'passiveLive':True})
    if not retained:
     if prem>EPS:
      if self._cancel_key(t,pk):self.passiveResizeAfterActiveFill+=1;self.v51InheritedResize+=1;self.activeEvents.append({'event':'V36_PASSIVE_RESIZE_CANCEL_AFTER_ACTIVE_FILL','t':int(t),'parentId':pid,'passiveKey':pk,'passiveRemaining':prem,'activeFilled':af})
     a['resizeDone']=True
   if pf>EPS and af<=EPS:
    ao=self.orders.get(ak)
    try:
     if ao and v1.live(self.snap(ao).get('status')) and ak not in self.cancelRequestedAt:
      if self._cancel_key(t,ak):self.activeEvents.append({'event':'V36_ACTIVE_CANCEL_AFTER_PASSIVE_SHARED_FILL','t':int(t),'parentId':pid,'passiveFilled':pf})
    except Exception:pass
 def run_exam_v51(self,models,winner):
  r=super().run_exam_v50(models,winner);r.update({'v51RetainChecks':self.v51RetainChecks,'v51RetainedPassive':self.v51RetainedPassive,'v51RetainedLive':self.v51RetainedLive,'v51InheritedResize':self.v51InheritedResize,'v51RetainedPassiveFillQty':self.v51RetainedPassiveFillQty,'v51ResponsibilityOverfill':self.v51ResponsibilityOverfill,'v51Events':self.v51Events[:160]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v51_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V51','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V51_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v50.V50HandoffRecoverable(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v50(models,cr['winner'])
   finally:b.close()
   c=V51QueueHandoff(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v51(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV50':br,'candidateV51':rr});print(json.dumps({'marketId':mid,'retained':rr['v51RetainedPassive'],'retainedFill':rr['v51RetainedPassiveFillQty'],'inheritedResize':rr['v51InheritedResize'],'genPaid':[br['v48GenerationPaidQty'],rr['v48GenerationPaidQty']],'floor':[br['floor'],rr['floor']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'retainChecks':int(sm('candidateV51','v51RetainChecks')),'retainedPassive':int(sm('candidateV51','v51RetainedPassive')),'retainedLive':int(sm('candidateV51','v51RetainedLive')),'retainedPassiveFillQty':sm('candidateV51','v51RetainedPassiveFillQty'),'responsibilityOverfill':sm('candidateV51','v51ResponsibilityOverfill'),'baselineFloorSum':sm('baselineV50','floor'),'candidateFloorSum':sm('candidateV51','floor'),'baselineGenPaid':sm('baselineV50','v48GenerationPaidQty'),'candidateGenPaid':sm('candidateV51','v48GenerationPaidQty'),'v50StrandingBlocks':int(sm('candidateV51','v50StrandingBlocks')),'truthMismatch':sm('candidateV51','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV51','overOwnedSubmitViolations'),'repairDrift':sm('candidateV51','repairToExpandAtFirstFill')}
  gates={'queueRetentionExercised':agg['retainedPassive']>0,'retainedCarrierWasLive':agg['retainedLive']==agg['retainedPassive'],'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'v50StrandingProtectionPreserved':agg['v50StrandingBlocks']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V51_GENERATION_ACTIVE_PASSIVE_QUEUE_HANDOFF','researchOnly':True,'priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['initial/non-generation V36 resize behavior unchanged','generation-active may retain compatible existing passive carrier','physical active+post-submit-passive fill bounded by authoritative Repair gap at active submit','no threshold/qty/delay/price tuning','floor and payment totals diagnostic only','realistic HFT consumed development only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
