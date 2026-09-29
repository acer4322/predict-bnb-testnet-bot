from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.parent_family_active_passive_handoff import FamilyCarrierState,ParentFamilyHandoffContext,ParentFamilyActivePassiveHandoffPolicyV1
EPS=1e-9;MID=1946784;pe=base.pe
CONTROL={'fills':3,'rounds':0,'pnlDiagnosticOnly':0.41326530612244916,'floor':-0.760204081632653,'activeRepairFillQty':1.7857142857142856,'legacyResponsibilityOverfill':2.0408163265306123,'legacySharedOverfill':2.0408163265306123}

class ParentFamilySafetyHFT(mod.EconomicHandoffLeaseLockHFT):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.familyPolicy=ParentFamilyActivePassiveHandoffPolicyV1();self.familyContexts={};self.familyEvents=[];self.familyRealizedOverfill=0.;self.familyWorstCaseOverfill=0.
 def _family_snapshot(self,pid,objective_id,submit_at):
  d={}
  for k,e in getattr(self,'carrierLedger',{}).items():
   try:
    if int(e.get('parentId'))!=int(pid) or str(e.get('objectiveRole') or '').upper()!='REPAIR':continue
   except Exception:continue
   if str(e.get('lane') or '').upper().startswith('ACTIVE_') or 'ACTIVE_REPAIR' in str(e.get('lane') or '').upper():continue
   if objective_id is not None and e.get('objectiveId') is not None and e.get('objectiveId')!=objective_id:continue
   d[str(k)]=float(e.get('actualFilled') or 0.)
  return d
 def _submit_active(self,t,pid,passive_key,side,ask,q,objective_id):
  cap=float(self._parent_debt_now(int(pid)))
  bases=self._family_snapshot(int(pid),objective_id,int(t))
  ok=super()._submit_active(t,pid,passive_key,side,ask,q,objective_id)
  if ok:
   a=self.activeByParent.get(int(pid))
   if a is not None:
    self.familyContexts[int(pid)]={'cap':cap,'bases':bases,'objectiveId':objective_id,'activeKey':str(a.get('key')),'submitAt':int(t)}
    self.familyEvents.append({'t':int(t),'event':'PARENT_FAMILY_ACTIVE_SCOPE','parentId':int(pid),'activeKey':str(a.get('key')),'anchorPassiveKey':str(passive_key),'responsibilityCapAtActiveSubmit':cap,'passiveBaselines':bases})
  return ok
 def _family_decision(self,pid,a):
  ctx=self.familyContexts.get(int(pid))
  if ctx is None:return None
  carriers=[];oid=ctx.get('objectiveId');submit_at=int(ctx['submitAt']);active_key=str(ctx['activeKey']);bases=ctx['bases']
  for k,e in getattr(self,'carrierLedger',{}).items():
   try:
    if int(e.get('parentId'))!=int(pid) or str(e.get('objectiveRole') or '').upper()!='REPAIR':continue
   except Exception:continue
   if oid is not None and e.get('objectiveId') is not None and e.get('objectiveId')!=oid:continue
   lane=str(e.get('lane') or '');is_active=(str(k)==active_key or lane.upper().startswith('ACTIVE_') or 'ACTIVE_REPAIR' in lane.upper())
   if is_active and str(k)!=active_key:continue
   now=float(e.get('actualFilled') or 0.);sub=float(e.get('submittedQty') or 0.)
   if str(k)==active_key:base_fill=0.
   elif str(k) in bases:base_fill=float(bases[str(k)])
   else:
    sat=int(e.get('submittedAt') or getattr(self,'orders',{}).get(k,{}).get('placed') or 0)
    base_fill=0. if sat>=submit_at else now
   terminal=bool(e.get('terminalConfirmed'));cancel=bool(e.get('cancelRequested')) and not terminal
   carriers.append(FamilyCarrierState(str(k),'ACTIVE' if str(k)==active_key else 'PASSIVE',sub,now,base_fill,not terminal,cancel))
  return self.familyPolicy.evaluate(ParentFamilyHandoffContext(int(pid),float(ctx['cap']),tuple(carriers)))
 def _reconcile_active(self,t):
  # Preserve V51 action behavior exactly; only replace its singleton overfill measurement.
  for pid,a in list(self.activeByParent.items()):
   ak=a['key'];pk=a['passiveKey'];ae=self.carrierLedger.get(ak,{});pe=self.carrierLedger.get(pk,{})
   af=float(ae.get('actualFilled') or 0.0);pf=max(0.0,float(pe.get('actualFilled') or 0.0)-float(a.get('passiveBaseActual') or 0.0));old=float(a.get('fillSeen') or 0.0)
   if af>old+EPS:
    before=float(a.get('floorBefore')) if old<=EPS else float(a.get('floorAfter') if a.get('floorAfter') is not None else self._current_payoffs()['floor']);inc=af-old;a['fillSeen']=af;self.activeFillQty+=inc;after=self._current_payoffs()['floor'];a['floorAfter']=after;self.floorFillDeltas.append(after-before);self.activeEvents.append({'event':'V36_ACTIVE_SHARED_FILL','t':int(t),'parentId':pid,'incQty':inc,'cumQty':af,'floorBeforeObserved':before,'floorAfterObserved':after,'floorDelta':after-before,'passiveFillAfterArm':pf})
   if a.get('v51RetainedPassive'):
    seen=float(a.get('v51PassiveSeen') or 0.0)
    if pf>seen+EPS:
     inc=pf-seen;a['v51PassiveSeen']=pf;self.v51RetainedPassiveFillQty+=inc;self.v51Events.append({'t':int(t),'event':'GENERATION_RETAINED_PASSIVE_FILL','generationId':a.get('generationId'),'parentId':pid,'passiveKey':pk,'incQty':inc,'cumPostActivePassiveQty':pf})
   a['maxPassiveAfter']=max(float(a.get('maxPassiveAfter') or 0.0),pf)
   fd=self._family_decision(pid,a)
   if fd is not None:
    self.familyRealizedOverfill=max(self.familyRealizedOverfill,float(fd.realized_overfill));self.familyWorstCaseOverfill=max(self.familyWorstCaseOverfill,float(fd.worst_case_overfill));self.v51ResponsibilityOverfill=max(self.v51ResponsibilityOverfill,float(fd.realized_overfill));self.sharedRealizedOverfill=max(self.sharedRealizedOverfill,float(fd.realized_overfill))
    if (fd.realized_after_active>EPS or fd.unresolved_inflight>EPS) and (not self.familyEvents or self.familyEvents[-1].get('signature')!=(round(fd.realized_after_active,9),round(fd.unresolved_inflight,9))):
     sig=(round(fd.realized_after_active,9),round(fd.unresolved_inflight,9));self.familyEvents.append({'t':int(t),'event':'PARENT_FAMILY_RECONCILE','parentId':int(pid),'realizedAfterActive':fd.realized_after_active,'unresolvedInflight':fd.unresolved_inflight,'worstCaseOwned':fd.worst_case_owned,'cap':self.familyContexts[int(pid)]['cap'],'realizedOverfill':fd.realized_overfill,'worstCaseOverfill':fd.worst_case_overfill,'signature':sig})
   else:
    realized=af+pf;cap=float(a.get('responsibilityCapAtSubmit') if a.get('generationId') is not None else a.get('qty') or 0.0);self.v51ResponsibilityOverfill=max(self.v51ResponsibilityOverfill,max(0.0,realized-cap));
    if a.get('generationId') is None:self.sharedRealizedOverfill=max(self.sharedRealizedOverfill,max(0.0,realized-float(a.get('qty') or 0.0)))
   # Exact V51 retention/resize behavior below.
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
      retained=True;a['resizeDone']=True;a['v51RetainedPassive']=True;self.v51RetainedPassive+=1;self.v51RetainedLive+=1;self.v51Events.append({'t':int(t),'event':'GENERATION_PASSIVE_QUEUE_RETAINED_AFTER_ACTIVE_FILL','generationId':a.get('generationId'),'parentId':pid,'passiveKey':pk,'passiveRemaining':prem,'currentRepairGap':float(pay['gap']),'activePending':active_pending,'handoffCapacity':handoff_cap,'passiveLive':True})
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
 def run_family(self,models,winner):
  r=self.run_locked(models,winner);r.update({'parentFamilyHandoffPolicy':self.familyPolicy.name,'parentFamilyRealizedOverfill':self.familyRealizedOverfill,'parentFamilyWorstCaseOverfill':self.familyWorstCaseOverfill,'parentFamilyEvents':self.familyEvents[:300]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='multislot_family_safety_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'MULTISLOT_PARENT_FAMILY_SAFETY_1946784','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTISLOT_PARENT_FAMILY_SAFETY_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};cr=co[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(ParentFamilySafetyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_family(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
  finally:s.close()
  m=base.slim(r);ss=pe.safety(r);occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True
  parity=abs(m['pnlDiagnosticOnly']-CONTROL['pnlDiagnosticOnly'])<=1e-9 and abs(m['floor']-CONTROL['floor'])<=1e-9 and m['fills']==CONTROL['fills'] and m['rounds']==CONTROL['rounds'] and abs(m['activeRepairFillQty']-CONTROL['activeRepairFillQty'])<=1e-9
  gates={'behaviorParityVsLegacyControl':parity,'parentFamilyExercised':len(r.get('parentFamilyEvents') or [])>0,'zeroParentFamilyRealizedOverfill':float(r.get('parentFamilyRealizedOverfill') or 0)<=EPS,'zeroParentFamilyWorstCaseOverfill':float(r.get('parentFamilyWorstCaseOverfill') or 0)<=EPS,'legacySafetyNowZero':all(float(x or 0)<=EPS for x in ss.values()),'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':bool(occok)}
  decision='PARENT_FAMILY_SAFETY_SEMANTICS_PASS' if all(gates.values()) else 'PARENT_FAMILY_SAFETY_SEMANTICS_FAIL'
  out={'version':'ETH_MULTISLOT_PARENT_FAMILY_SAFETY_1946784_V1','date':'2026-09-05','researchOnly':True,'decision':decision,'marketId':MID,'control':CONTROL,'candidate':m,'gates':gates,'safety':ss,'parentFamilyRealizedOverfill':r.get('parentFamilyRealizedOverfill'),'parentFamilyWorstCaseOverfill':r.get('parentFamilyWorstCaseOverfill'),'parentFamilyEvents':r.get('parentFamilyEvents') or [],'allocationParents':parents,'occupancyParents':occ,'boundary':['single consumed negative-control market 1946784','action behavior frozen from EconomicHandoffLeaseLockHFT','only V36/V51 overfill audit semantics upgraded from single-passiveKey cap to same-parent Repair family cap at Active submit','cancel/price/qty/timing/ownership/AllocationLedger unchanged','cancel-pending remains in family worst-case until terminal','new post-Active same-parent Repair carriers are family members','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'gates':gates,'safety':ss,'familyRealizedOverfill':out['parentFamilyRealizedOverfill'],'familyWorstCaseOverfill':out['parentFamilyWorstCaseOverfill'],'events':out['parentFamilyEvents'][:20]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
