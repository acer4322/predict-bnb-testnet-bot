from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v76=sib('eth_v76_for_v80','run_eth_repair_v76_recoverability_gated_active_handoff_fresh1911708.py')
v75=v76.v75;v73b=v76.v73b;v70g=v76.v70g;v38=v75.v38;v1=v75.v1
try:
 from tools.eth_repair_modular import economic_v1_profile
 from tools.eth_repair_modular.contracts import CompletionContext, OwnershipContext, HandoffContext, GenerationContext, SchedulerContext
except ImportError:
 z=Path(__file__).with_name('eth_repair_modular_v1.zip')
 if z.exists() and str(z) not in sys.path:sys.path.insert(0,str(z))
 from eth_repair_modular import economic_v1_profile
 from eth_repair_modular.contracts import CompletionContext, OwnershipContext, HandoffContext, GenerationContext, SchedulerContext

class V80ModularManagementKernel(v76.V76RecoverabilityGate):
 """Authority firewall over the legacy execution substrate.

 Old V6..V76 classes remain available as execution/research helpers, but the
 four management authority points below are delegated to replaceable modules:
 completion, directional ownership, generation unlock, and active handoff.
 """
 def __init__(self,*a,policy_profile=None,**kw):
  self.policyProfile=policy_profile or economic_v1_profile()
  super().__init__(*a,**kw)
  self.v80ManagementEvents=[];self.v80OwnershipEvents=[];self.v80HandoffEvents=[]
  self.v80ShareRepairSettlements=0;self.v80ManagementCompletions=0
  self.v80LegacyStructuralCompletionSuppressed=0;self.v80CompletionDeferrals=0
  self.v80EconomicDeficit=None;self.v80EconomicDeficitBirths=0;self.v80EconomicDeficitRecoveries=0;self.v80NextDeficitId=1

 def _parent_unresolved_safe(self):
  try:return bool(self.parent_unresolved())
  except Exception:
   try:return any(float(rem)>EPS for _,_,rem in self.lane_unresolved('REPAIR'))
   except Exception:return False

 def _sync_economic_deficit(self,t,reason='STATE_REFRESH'):
  try:floor,u,d,cost=self._raw_floor();floor=float(floor)
  except Exception:return
  if floor < -EPS:
   if self.v80EconomicDeficit is None:
    self.v80EconomicDeficit={'id':self.v80NextDeficitId,'bornAt':int(t),'lastAt':int(t),'amount':-floor,'floor':floor,'shareRepairSettledAt':None,'recoveredAt':None}
    self.v80NextDeficitId+=1;self.v80EconomicDeficitBirths+=1
    self.v80ManagementEvents.append({'t':int(t),'event':'ECONOMIC_DEFICIT_BIRTH','deficitId':self.v80EconomicDeficit['id'],'amount':-floor,'reason':reason})
   else:
    self.v80EconomicDeficit.update({'lastAt':int(t),'amount':-floor,'floor':floor})
  elif self.v80EconomicDeficit is not None:
   z=dict(self.v80EconomicDeficit);z.update({'recoveredAt':int(t),'recoveredFloor':floor})
   self.v80EconomicDeficitRecoveries+=1
   self.v80ManagementEvents.append({'t':int(t),'event':'ECONOMIC_DEFICIT_RECOVERED','deficitId':z['id'],'floor':floor,'reason':reason})
   self.v80EconomicDeficit=None

 def _complete_parent_if_structural(self,t,ai):
  p=self.repairParent
  if not p:
   self._sync_economic_deficit(t,'NO_SHARE_PARENT')
   return
  floor,u,d,cost=self._raw_floor();pid=int(p.get('id')) if p.get('id') is not None else None;side=p.get('side')
  ctx=CompletionContext(t=int(t),parent_id=pid,parent_side=side,up_shares=float(ai['UP']),down_shares=float(ai['DOWN']),floor=float(floor),parent_unresolved=self._parent_unresolved_safe())
  dec=self.policyProfile.completion.evaluate(ctx)
  if not dec.share_repair_settled:
   self._sync_economic_deficit(t,'SHARE_REPAIR_OPEN');return
  if not dec.close_share_parent:
   self.v80CompletionDeferrals+=1;self.v80ManagementEvents.append({'t':int(t),'event':'COMPLETION_DEFERRED','parentId':pid,'reason':dec.reason,'floor':float(floor),'shareGap':ctx.share_gap});self._sync_economic_deficit(t,'COMPLETION_DEFERRED');return
  if dec.management_complete:
   before=int(getattr(self,'repairParentCompletions',0));out=super()._complete_parent_if_structural(t,ai)
   after=int(getattr(self,'repairParentCompletions',0))
   if self.repairParent is None or after>before:
    self.v80ManagementCompletions+=1;self.v80ManagementEvents.append({'t':int(t),'event':'MANAGEMENT_RESPONSIBILITY_COMPLETE','parentId':pid,'reason':dec.reason,'floor':float(floor),'shareGap':ctx.share_gap})
   self._sync_economic_deficit(t,'MANAGEMENT_COMPLETE');return out
  # Economic profile: retire only the share-gap Repair responsibility. Do not
  # let V6's share-neutral completion become management completion authority.
  self.v80LegacyStructuralCompletionSuppressed+=1;self.v80ShareRepairSettlements+=1
  self.repairParent=None
  try:
   if getattr(self,'repairLaneObjective',None) is not None:self.repairLaneObjective=None
   ao=getattr(self,'activeObjective',None)
   if isinstance(ao,dict) and str(ao.get('role'))=='REPAIR':self.activeObjective=None
  except Exception:pass
  self._sync_economic_deficit(t,'SHARE_REPAIR_SETTLED')
  if self.v80EconomicDeficit is not None and self.v80EconomicDeficit.get('shareRepairSettledAt') is None:self.v80EconomicDeficit['shareRepairSettledAt']=int(t)
  if hasattr(self,'parentLedger') and pid is not None:
   z=self.parentLedger.setdefault(pid,{'parentId':pid,'bornAt':None,'side':side})
   z.update({'completed':False,'shareRepairSettled':True,'shareRepairSettledAt':int(t),'completionKind':'SHARE_REPAIR_SETTLED_ECONOMIC_DEFICIT_OPEN','economicDeficitOpen':True,'economicFloorAtShareSettlement':float(floor)})
  self.v80ManagementEvents.append({'t':int(t),'event':'SHARE_REPAIR_SETTLED_ECONOMIC_DEFICIT_OPEN','parentId':pid,'floor':float(floor),'shareGap':ctx.share_gap,'deficitAmount':max(0.0,-float(floor)),'legacyCompletionSuppressed':True})
  return None

 def _score_state(self,t,after_kind):
  # Ownership authority is modular. Bypass V75's embedded birth rule after the
  # module decision, then continue through V70G/V44 execution/dedup machinery.
  if after_kind=='REPAIR' and getattr(self,'thesis',None) is None and self._coordDebt>EPS and self.repairParent is not None and self.teacher is not None:
   f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);qv=v1.quotes(self.book)
   if qv:
    side=self._signal_side(qv);rec=self._v75_recoverability(t,side,qv);ctx=OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=False,p_expand=pE,signal_side=side,recoverable=bool(rec.get('recoverable')));dec=self.policyProfile.ownership.evaluate(ctx)
    row={**rec,'event':'MODULAR_OWNERSHIP_CHECK','pExpand':pE,'signalSide':side,'decision':dec.reason,'createThesis':bool(dec.create_thesis),'profileModule':getattr(self.policyProfile.ownership,'name',type(self.policyProfile.ownership).__name__)}
    if dec.create_thesis:
     self.thesis={'id':self.nextThesisId,'side':dec.side,'bornAt':int(t),'materialized':False,'recoveries':0,'opens':0,'birthKind':'V80_MODULAR_OWNERSHIP'};self.nextThesisId+=1;self.v75Births+=1;self.v75Recoverable+=1;row['thesisId']=self.thesis['id'];row['event']='MODULAR_THESIS_BIRTH'
    self.v75Checks+=1;self.v75Events.append(dict(row));self.v80OwnershipEvents.append(row)
  return v73b.V73BRecoverabilityShadow._score_state(self,t,after_kind)

 def _scheduler_decision(self,t,after_kind=None,receipt_advanced=True):
  pol=getattr(self.policyProfile,'scheduler',None)
  if pol is None:return None
  parent=getattr(self,'repairParent',None)
  responsibility_live=bool(isinstance(parent,dict) and float(getattr(self,'_coordDebt',0.0) or 0.0)>EPS)
  ctx=SchedulerContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,responsibility_live=responsibility_live,receipt_advanced=bool(receipt_advanced),after_kind=after_kind,equivalent_child_live=False)
  return pol.evaluate(ctx)

 def _generation_unlocked(self):
  auth=bool(getattr(self,'v70gGenerationAuthorized',False))
  db=max(0.0,float(getattr(self,'v70dGenerationDebt',0.0) or 0.0)-float(getattr(self,'v70gGenerationDebtBaseline',0.0) or 0.0))
  pb=max(0.0,float(getattr(self,'v70dGenerationPaid',0.0) or 0.0)-float(getattr(self,'v70gGenerationPaidBaseline',0.0) or 0.0))
  gid=int(getattr(self,'v70gGenerationId',1));cnt=int(getattr(self,'v70gGenerationResponsibilityCount',{}).get(gid,0))
  carrier_key=getattr(self,'v70gGenerationCarrier',None);physical_live=False
  if carrier_key:
   e=getattr(self,'carrierLedger',{}).get(carrier_key,{})
   rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
   physical_live=rem>EPS and not bool(e.get('terminalConfirmed'))
  equivalent_owned=physical_live
  if not equivalent_owned:
   try: equivalent_owned=bool(self._expand_occupied())
   except Exception: equivalent_owned=False
  ctx=GenerationContext(auth,db,pb,cnt,physical_expand_live=physical_live,payment_progress_observed=pb>EPS,equivalent_expand_owned=equivalent_owned)
  dec=self.policyProfile.generation.evaluate(ctx);return bool(dec.unlocked)

 def _submit_active_expand(self,t,source_key,e,o,remaining):
  row=self._shadow_active_recoverability(t,source_key,e,o,remaining);ctx=HandoffContext(t=int(t),side=(e.get('side') or o.get('side')),recoverability_observed=row is not None,recoverable=bool(row.get('recoverable')) if row is not None else True);dec=self.policyProfile.handoff.evaluate(ctx)
  ev={'t':int(t),'sourceKey':source_key,'decision':dec.reason,'allow':bool(dec.allow_active_handoff),'profileModule':getattr(self.policyProfile.handoff,'name',type(self.policyProfile.handoff).__name__)}
  if row is not None:ev.update(row)
  if not dec.allow_active_handoff:
   self.v76Blocks+=1;ev['event']='MODULAR_ACTIVE_HANDOFF_BLOCK';self.v76Events.append(dict(ev));self.v80HandoffEvents.append(ev);return False
  self.v76Allows+=1;ok=v70g.V70GGenerationScopedRelay._submit_active_expand(self,t,source_key,e,o,remaining);ev.update({'event':'MODULAR_ACTIVE_HANDOFF_ALLOW','submitOk':bool(ok)});self.v76Events.append(dict(ev));self.v80HandoffEvents.append(ev);return ok

 def process(self,t):
  self._sync_economic_deficit(t,'PRE_PROCESS');out=super().process(t);self._sync_economic_deficit(t,'POST_PROCESS');return out
 def cancel_expired(self,t):
  self._sync_economic_deficit(t,'PRE_CANCEL');out=super().cancel_expired(t);self._sync_economic_deficit(t,'POST_CANCEL');return out

 def run_exam_v80(self,models,winner):
  r=super().run_exam_v76(models,winner);self._sync_economic_deficit(int(self.capEnd),'FINAL')
  r.update({'v80PolicyProfile':self.policyProfile.describe(),'v80ShareRepairSettlements':self.v80ShareRepairSettlements,'v80ManagementCompletions':self.v80ManagementCompletions,'v80LegacyStructuralCompletionSuppressed':self.v80LegacyStructuralCompletionSuppressed,'v80CompletionDeferrals':self.v80CompletionDeferrals,'v80EconomicDeficitBirths':self.v80EconomicDeficitBirths,'v80EconomicDeficitRecoveries':self.v80EconomicDeficitRecoveries,'v80EconomicDeficitActiveAtEnd':int(self.v80EconomicDeficit is not None),'v80EconomicDeficitAmountAtEnd':float(self.v80EconomicDeficit.get('amount') if self.v80EconomicDeficit else 0.0),'v80EconomicDeficitState':self.v80EconomicDeficit,'v80ManagementEvents':self.v80ManagementEvents[:200],'v80OwnershipEvents':self.v80OwnershipEvents[:100],'v80HandoffEvents':self.v80HandoffEvents[:100]})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 tmp=Path(tempfile.mkdtemp(prefix='eth_v80_modular_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V80_MODULAR_MANAGEMENT','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V80_MODULAR_MANAGEMENT_START','markets':mids},ensure_ascii=False),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v76.V76RecoverabilityGate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v76(models,cr['winner'])
   finally:b.close()
   c=V80ModularManagementKernel(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=economic_v1_profile())
   try:rr=c.run_exam_v80(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baselineV76':br,'candidateV80':rr});print(json.dumps({'marketId':mid,'baseline':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'repairCompletions':br.get('repairParentCompletions'),'thesis':br.get('thesisSide'),'activeBlocks':br.get('v76ActiveBlocks')},'v80':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'legacyRepairCompletions':rr.get('repairParentCompletions'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'mgmtCompletions':rr.get('v80ManagementCompletions'),'deficitActive':rr.get('v80EconomicDeficitActiveAtEnd'),'deficitAmount':rr.get('v80EconomicDeficitAmountAtEnd'),'thesis':rr.get('thesisSide'),'activeBlocks':rr.get('v76ActiveBlocks')}},ensure_ascii=False),flush=True)
  safety_keys=['authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','repairToExpandAtFirstFill','v51ResponsibilityOverfill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt']
  safety={k:sum(float(x['candidateV80'].get(k) or 0.0) for x in rows) for k in safety_keys};max_resp=max([int(x['candidateV80'].get('v70gMaxResponsibilitiesPerGeneration') or 0) for x in rows] or [0])
  byrow={x['marketId']:x for x in rows};r1708=byrow.get(1911708);r1716=byrow.get(1911716);r2012=byrow.get(1912012)
  gates={'marketsComplete':len(rows)==len(mids),'zeroTruthMismatch':safety['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':safety['overOwnedSubmitViolations']==0,'zeroRepairDrift':safety['repairToExpandAtFirstFill']==0,'zeroResponsibilityOverfill':safety['v51ResponsibilityOverfill']<=EPS,'zeroPreBirthPaymentLeak':safety['v70dPreBirthPaymentLeak']<=EPS,'zeroDuplicateGenerationDebt':safety['v70dDuplicateGenerationDebt']<=EPS,'oneResponsibilityPerGeneration':max_resp<=1}
  if r1708:gates.update({'fresh1911708OwnershipPreserved':r1708['candidateV80'].get('thesisSide')=='DOWN','fresh1911708UnrecoverableHandoffStillBlocked':int(r1708['candidateV80'].get('v76ActiveBlocks') or 0)>=1 and float(r1708['candidateV80'].get('v64FillQty') or 0)<=EPS})
  if r1716:gates.update({'fresh1911716UnrecoverablePreSafeOwnerRejected':int(r1716['candidateV80'].get('v75PreSafeThesisBirths') or 0)==0})
  if r2012:gates.update({'fresh1912012LegacyFalseCompletionRemoved':int(r2012['baselineV76'].get('repairParentCompletions') or 0)>=1 and int(r2012['candidateV80'].get('repairParentCompletions') or 0)==0,'fresh1912012ShareRepairSettledButManagementOpen':int(r2012['candidateV80'].get('v80ShareRepairSettlements') or 0)>=1 and int(r2012['candidateV80'].get('v80EconomicDeficitActiveAtEnd') or 0)==1 and int(r2012['candidateV80'].get('v80ManagementCompletions') or 0)==0})
  decision='KEEP_V80_MODULAR_AUTHORITY_FIREWALL' if all(gates.values()) else 'FIX_V80_MODULAR_MIGRATION_BEFORE_MORE_RESEARCH'
  out={'version':'ETH_REPAIR_V80_MODULAR_MANAGEMENT_KERNEL','date':'2026-09-03','researchOnly':True,'behaviorChange':'management-authority migration; legacy execution substrate retained','policyProfile':economic_v1_profile().describe(),'gates':gates,'decision':decision,'safety':safety,'rows':rows,'boundary':['No strategy threshold/qty/price/delay tuning.','Old V6..V76 files remain untouched and available as research controls.','Completion, ownership, generation unlock, and active-handoff authority are delegated through replaceable modules.','Legacy execution/carrier/HFT machinery is substrate only, not management-completion authority.','Realistic HFT/Predict Tape only; winner is post-hoc diagnostic only.','No 8781.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'safety':safety},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
