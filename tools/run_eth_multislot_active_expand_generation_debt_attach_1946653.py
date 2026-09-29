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

class ActiveExpandGenerationDebtAttachHFT(mod.EconomicHandoffLeaseLockHFT):
 def __init__(self,*a,**kw):
  self.activeExpandGenerationDebtEvents=[];super().__init__(*a,**kw);self.allocationLedgerV2=GenerationAwareSharedParentDebtAllocationLedgerV3()
 def _source_parent(self,source_key):
  for ev in reversed(getattr(self,'v44Decisions',[])):
   if str(ev.get('key'))==str(source_key) and ev.get('parentId') is not None:return int(ev.get('parentId'))
  e=getattr(self,'carrierLedger',{}).get(str(source_key),{})
  return int(e.get('parentId')) if e.get('parentId') is not None else None
 def _reconcile_active_expand(self,t):
  before={str(k):float(a.get('fillSeen') or 0.0) for k,a in getattr(self,'v64Active',{}).items()}
  out=super()._reconcile_active_expand(t)
  for k,a in list(getattr(self,'v64Active',{}).items()):
   key=str(k);cur=float(a.get('fillSeen') or 0.0);old=float(before.get(key,0.0))
   if cur<=old+EPS:continue
   inc=cur-old;src=str(a.get('sourceKey'));pid=self._source_parent(src)
   row={'t':int(t),'event':'ACTIVE_EXPAND_GENERATION_DEBT_OBSERVED','activeKey':key,'sourceKey':src,'parentId':pid,'fillInc':inc,'cumFill':cur}
   if pid is None:
    row['decision']='NO_PARENT_BIND';self.activeExpandGenerationDebtEvents.append(row);continue
   # Ensure parent exists before attaching generation debt. Existing Repair parent is authoritative.
   fallback=float(self._current_payoffs().get('gap') or 0.0)
   self._seed_parent_debt(pid,fallback)
   tok=f'{key}:cum:{cur:.12f}'
   ar=self.allocationLedgerV2.attach_generation_debt(pid,tok,inc)
   row.update({'decision':ar.reason,**ar.__dict__});self.activeExpandGenerationDebtEvents.append(row)
  return out
 def run_candidate(self,models,winner):
  r=self.run_locked(models,winner);r.update({'generationAwareAllocationLedger':getattr(self.allocationLedgerV2,'name',None),'activeExpandGenerationDebtEvents':self.activeExpandGenerationDebtEvents,'activeExpandGenerationDebtAttached':sum(bool(x.get('applied')) for x in self.activeExpandGenerationDebtEvents)});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='active_expand_gen_debt_1946653_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'ACTIVE_EXPAND_GENERATION_DEBT_1946653','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ACTIVE_EXPAND_GENERATION_DEBT_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(ActiveExpandGenerationDebtAttachHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:
   r=s.run_candidate(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {};pay=s._current_payoffs();fills=[]
   for key,e in getattr(s,'carrierLedger',{}).items():
    q=float(e.get('actualFilled') or 0.0)
    if q<=EPS:continue
    o=getattr(s,'orders',{}).get(key,{})
    fills.append({'key':key,'placed':int(o.get('placed') or e.get('submittedAt') or 0),'side':e.get('side') or o.get('side'),'price':o.get('price'),'qty':q,'role':e.get('objectiveRole'),'lane':e.get('lane'),'parentId':e.get('parentId')})
   fills.sort(key=lambda x:x['placed'])
  finally:s.close()
  m=base.slim(r);ss=pe.safety(r);occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;p=parents.get('1') or parents.get(1) or {}
  parity=(m['fills']==BASE['fills'] and m['rounds']==BASE['rounds'] and abs(m['pnlDiagnosticOnly']-BASE['pnlDiagnosticOnly'])<=1e-9 and abs(m['floor']-BASE['floor'])<=1e-9 and abs(m['activeRepairFillQty']-BASE['activeRepairFillQty'])<=1e-9)
  gates={'activeExpandDebtAttachExercised':int(r.get('activeExpandGenerationDebtAttached') or 0)>0,'behaviorParityVsCurrent':parity,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':bool(occok),'safetyZero':all(float(v or 0)<=EPS for v in ss.values()),'initialDebtIncreasedByExpandFill':abs(float(p.get('initialDebt') or 0)-(BASE['initialDebt']+2.4390243902439024))<=1e-7,'repairPaidEqualsExpandedDebt':abs(float(p.get('repairPaid') or 0)-float(p.get('initialDebt') or 0))<=1e-7,'overflowEliminated':float(p.get('overflowBorn') or 0)<=EPS,'terminalGapZero':abs(float(pay.get('gap') or 0))<=EPS}
  if not gates['activeExpandDebtAttachExercised']:decision='ACTIVE_EXPAND_GENERATION_DEBT_NOT_EXERCISED'
  elif not all(v for k,v in gates.items() if k!='behaviorParityVsCurrent'):decision='ACTIVE_EXPAND_GENERATION_DEBT_SAFETY_OR_SEMANTIC_FAIL'
  elif parity:decision='ACTIVE_EXPAND_GENERATION_DEBT_BEHAVIOR_INERT_FIX_PASS'
  else:decision='ACTIVE_EXPAND_GENERATION_DEBT_BEHAVIOR_CHANGED_DIAGNOSE'
  out={'version':'ETH_MULTISLOT_ACTIVE_EXPAND_GENERATION_DEBT_ATTACH_1946653_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'decision':decision,'baselineFrozen':BASE,'candidate':m,'delta':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-BASE['pnlDiagnosticOnly'],'floor':m['floor']-BASE['floor'],'fills':m['fills']-BASE['fills'],'rounds':m['rounds']-BASE['rounds']},'gates':gates,'terminalPayoffs':pay,'safety':ss,'allocationParents':parents,'activeExpandGenerationDebtEvents':r.get('activeExpandGenerationDebtEvents') or [],'fills':fills,'v84OverflowAllocatedQty':float(r.get('v84OverflowAllocatedQty') or 0),'v84OverflowPaidQty':float(r.get('v84OverflowPaidQty') or 0),'transitionEvents':(r.get('transitionEvents') or [])[:220],'boundary':['single variable: attach confirmed Active Expand fallback fill quantity as new generation debt to same parent AllocationLedger V3','attachment occurs only on confirmed fill increment and is idempotent by cumulative fill token','no Active ownership/price/qty/timing/multislot changes','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'gates':gates,'allocationParent':p,'attachEvents':out['activeExpandGenerationDebtEvents'],'fills':fills},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
