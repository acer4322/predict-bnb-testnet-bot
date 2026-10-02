from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;MID=1912961

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v85=sib('eth_v85e_for_v86b','run_eth_repair_v85e_recoverable_raw_carrier_hft.py');v84=v85.v84;v80=v85.v80;v38=v85.v38;v1=v84.v83.v1

class V86B(v85.V85E):
 def __init__(self,*a,**kw):
  self.v86OverflowBirthClocks=set();self.v86SingleDisconnectTriggers=0;self.v86Events=[]
  super().__init__(*a,**kw)
 def _scan_v84(self,t):
  before={k:m.get('overflowBornAt') for k,m in getattr(self,'v84Composite',{}).items()}
  out=super()._scan_v84(t)
  for k,m in getattr(self,'v84Composite',{}).items():
   b=m.get('overflowBornAt')
   if b is not None and before.get(k) is None:
    self.v86OverflowBirthClocks.add(int(b));self.v86Events.append({'t':int(b),'event':'V86_OVERFLOW_RESPONSIBILITY_BIRTH','compositeKey':k,'overflowDebt':float(m.get('overflowDebt') or 0.0),'repairSide':'DOWN' if m.get('side')=='UP' else 'UP'})
  return out
 def _maybe_hard_active(self,t):
  rp=self.repairParent
  if rp is None:return False
  pid=int(rp.get('id'));side=rp.get('side');born=int(rp.get('bornAt') or -1)
  is_overflow=born in self.v86OverflowBirthClocks
  if not is_overflow:return super()._maybe_hard_active(t)
  if pid not in self._armedParents or pid in self.activeByParent or pid in self.hardConfirmed or side not in ('UP','DOWN'):return False
  opp=[x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid]
  if len(opp)<1:return False
  first=opp[0]
  if bool(first.get('connected')):return super()._maybe_hard_active(t)
  base=float(self.armFillBase.get(pid,self._parent_actual_fill(pid)));now=self._parent_actual_fill(pid)
  if now>base+EPS:
   self.blockedPaymentProgress+=1;return False
  pay=self._current_payoffs()
  if pay['floor']>=-EPS or pay['gap']<=EPS:return False
  psv=self._find_passive(pid)
  if psv is None:return False
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(float(legal),float(pay['gap']),float(rem))
  if q<=EPS or q+EPS<legal:
   self.blockedNoLegalSlice+=1;return False
  self.hardConfirmed.add(pid);self.hardEventConfirmedCount+=1;self.v86SingleDisconnectTriggers+=1;oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  ev={'event':'V86_OVERFLOW_SINGLE_DISCONNECT_CONFIRMED','t':int(t),'parentId':pid,'bornAt':born,'side':side,'postArmOpportunityCount':len(opp),'firstDisconnected':True,'parentFillAtArm':base,'parentFillNow':now,'floor':pay['floor'],'gap':pay['gap'],'passiveRemaining':rem,'liveAsk':ask,'legalMinSlice':legal,'activeQty':q}
  self.activeEvents.append(dict(ev));self.v86Events.append(dict(ev));return self._submit_active(t,pid,passive_key,side,ask,q,oid)
 def run_v86b(self,models,winner):
  r=self.run_v85e(models,winner);r.update({'v86SingleDisconnectTriggers':self.v86SingleDisconnectTriggers,'v86OverflowBirthClocks':sorted(self.v86OverflowBirthClocks),'v86Events':self.v86Events[:120]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v86b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V86B_OVERFLOW_SINGLE_DISCONNECT','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V86B_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  b=mk(v85.V85E)
  try:br=b.run_v85e(models,cr['winner'])
  finally:b.close()
  c=mk(V86B)
  try:rr=c.run_v86b(models,cr['winner'])
  finally:c.close()
  legacy=int(rr.get('overOwnedSubmitViolations') or 0);comps=int(rr.get('v84CompositeSubmits') or 0);unexpl=max(0,legacy-comps);other=sum(float(rr.get(k) or 0) for k in ['authorizedSubmitWithTruthRoleMismatch','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt']);deltas=[float(x) for x in rr.get('v36FloorFillDeltas',[])];shared=float(rr.get('v36SharedRealizedOverfill') or 0.0)
  improvement=float(rr.get('v84OverflowPaidQty') or 0)>float(br.get('v84OverflowPaidQty') or 0)+EPS or float(rr.get('floor') or 0)>float(br.get('floor') or 0)+EPS
  gates={'overflowBornParentObserved':len(rr.get('v86OverflowBirthClocks') or [])>0,'singleDisconnectTriggerExercised':int(rr.get('v86SingleDisconnectTriggers') or 0)>0,'activeSubmitExercised':int(rr.get('v36ActiveSubmitCount') or 0)>0,'activeFillExercised':float(rr.get('v36ActiveFillQty') or 0)>EPS,'allActiveFillFloorDeltasNonnegative':all(x>=-EPS for x in deltas),'zeroSharedRealizedOverfill':shared<=EPS,'zeroUnexplainedLegacyOverOwned':unexpl==0,'zeroOtherSafetyViolations':other<=EPS,'overflowPaymentOrTerminalFloorImprovesVsV85E':improvement}
  decision='KEEP_V86B_OVERFLOW_EXECUTION_HANDOFF_FOR_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_V86B_OVERFLOW_EXECUTION_HANDOFF'
  out={'version':'ETH_REPAIR_V86B_OVERFLOW_PARENT_SINGLE_DISCONNECT_ACTIVE','date':'2026-09-03','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'v85e':{'floor':br.get('floor'),'pnl':br.get('pnlDiagnosticOnly'),'overflowAllocated':br.get('v84OverflowAllocatedQty'),'overflowPaid':br.get('v84OverflowPaidQty'),'activeSubmit':br.get('v36ActiveSubmitCount'),'activeFillQty':br.get('v36ActiveFillQty')},'v86b':{'floor':rr.get('floor'),'pnl':rr.get('pnlDiagnosticOnly'),'overflowAllocated':rr.get('v84OverflowAllocatedQty'),'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'singleDisconnectTriggers':rr.get('v86SingleDisconnectTriggers'),'activeSubmit':rr.get('v36ActiveSubmitCount'),'activeFillQty':rr.get('v36ActiveFillQty'),'activeFloorDeltas':rr.get('v36FloorFillDeltas'),'sharedOverfill':shared,'shareSettlements':rr.get('v80ShareRepairSettlements'),'repairParentBirths':rr.get('repairParentBirths'),'unexplainedOverOwned':unexpl},'v86Events':rr.get('v86Events',[]),'activeEvents':rr.get('v36ActiveEvents',[])[:120],'parentLedger':rr.get('v34ParentLedger',[]),'fullCandidate':rr,'boundary':['one-market realistic HFT','passive first, active only after overflow-born parent soft-arm + one disconnected materialization','V36 MIN_SLICE/shared-budget/500ms mechanic unchanged','ordinary parents retain V36 two-disconnect rule','no Target runtime input','no tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'v85e':out['v85e'],'v86b':out['v86b'],'v86Events':out['v86Events'],'activeEvents':out['activeEvents'][:20]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
