from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
EPS=1e-9;MID=1946653;pe=base.pe
BASE={'fills':6,'rounds':1,'pnlDiagnosticOnly':-0.8757699543430029,'floor':-0.8757699543430029,'activeRepairFillQty':1.639344262295082,'initialDebt':5.0,'repairPaid':5.0,'overflowBorn':2.4390243902439033}

class GenerationDebtAttachCurrentHFT(mod.EconomicHandoffLeaseLockHFT):
 def __init__(self,*a,**kw):
  self.generationDebtAttachEvents=[];self._generationDebtHandled=set();super().__init__(*a,**kw);self.allocationLedgerV2=GenerationAwareSharedParentDebtAllocationLedgerV3()
 def _attach_new_epoch_after_resolution(self,t):
  out=super()._attach_new_epoch_after_resolution(t)
  for ev in list(getattr(self,'generationEpochEvents',[])):
   if ev.get('event')!='EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH':continue
   src=str(ev.get('sourceKey'));epoch=int(ev.get('epoch') or 0);token=f'{src}:epoch:{epoch}'
   if token in self._generationDebtHandled:continue
   self._generationDebtHandled.add(token);pid=int(ev.get('parentId'));add=float(ev.get('addedDebt') or 0.0)
   ar=self.allocationLedgerV2.attach_generation_debt(pid,token,add)
   self.generationDebtAttachEvents.append({'t':int(t),'event':'GENERATION_DEBT_LEDGER_ATTACH','sourceKey':src,'parentId':pid,'epoch':epoch,**ar.__dict__})
  return out
 def run_candidate(self,models,winner):
  r=self.run_locked(models,winner);r.update({'generationAwareAllocationLedger':getattr(self.allocationLedgerV2,'name',None),'generationDebtAttachEvents':self.generationDebtAttachEvents,'generationDebtAttachApplied':sum(bool(x.get('applied')) for x in self.generationDebtAttachEvents)});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='multislot_gen_debt_1946653_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'MULTISLOT_GENERATION_DEBT_ATTACH_1946653','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTISLOT_GENERATION_DEBT_ATTACH_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(GenerationDebtAttachCurrentHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_candidate(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {};physical=[]
  finally:s.close()
  for key,e in getattr(s,'carrierLedger',{}).items() if False else []:pass
  m=base.slim(r);ss=pe.safety(r);occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;p=parents.get('1') or parents.get(1) or {}
  parity=(m['fills']==BASE['fills'] and m['rounds']==BASE['rounds'] and abs(m['pnlDiagnosticOnly']-BASE['pnlDiagnosticOnly'])<=1e-9 and abs(m['floor']-BASE['floor'])<=1e-9 and abs(m['activeRepairFillQty']-BASE['activeRepairFillQty'])<=1e-9)
  gates={'generationDebtAttachExercised':int(r.get('generationDebtAttachApplied') or 0)>0,'behaviorParityVsCurrent':parity,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':bool(occok),'safetyZero':all(float(v or 0)<=EPS for v in ss.values()),'initialDebtIncreased':float(p.get('initialDebt') or 0)>BASE['initialDebt']+EPS,'overflowReduced':float(p.get('overflowBorn') or 0)<BASE['overflowBorn']-EPS,'terminalGapZero':abs(float(r.get('gap') or 0))<=EPS}
  if not gates['generationDebtAttachExercised']:decision='GENERATION_DEBT_ATTACH_NOT_EXERCISED'
  elif not all(v for k,v in gates.items() if k!='behaviorParityVsCurrent'):decision='GENERATION_DEBT_ATTACH_SAFETY_OR_ALIGNMENT_FAIL'
  elif parity:decision='GENERATION_DEBT_ATTACH_BEHAVIOR_INERT_SEMANTIC_FIX_PASS'
  else:decision='GENERATION_DEBT_ATTACH_BEHAVIOR_CHANGED_DIAGNOSE'
  out={'version':'ETH_MULTISLOT_GENERATION_DEBT_ATTACH_1946653_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'decision':decision,'baselineFrozen':BASE,'candidate':m,'delta':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-BASE['pnlDiagnosticOnly'],'floor':m['floor']-BASE['floor'],'fills':m['fills']-BASE['fills'],'rounds':m['rounds']-BASE['rounds']},'gates':gates,'safety':ss,'allocationParents':parents,'generationDebtAttachEvents':r.get('generationDebtAttachEvents') or [],'generationEpochEvents':(r.get('generationEpochEvents') or [])[:120],'v84OverflowAllocatedQty':float(r.get('v84OverflowAllocatedQty') or 0),'v84OverflowPaidQty':float(r.get('v84OverflowPaidQty') or 0),'transitionEvents':(r.get('transitionEvents') or [])[:200],'boundary':['single variable over EconomicHandoffLeaseLock: GenerationAwareSharedParentDebtAllocationLedgerV3','new generation debt attaches idempotently to same parent ledger without resetting paid debt','Active ownership/price/qty/timing/multislot behavior frozen','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'gates':gates,'allocationParent':p,'attachEvents':out['generationDebtAttachEvents'],'overflowAllocated':out['v84OverflowAllocatedQty'],'overflowPaid':out['v84OverflowPaidQty']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
