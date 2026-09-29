from __future__ import annotations
import argparse, importlib.util, json, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
MID=1917552; EPS=1e-9

def load(name,fn,fallback=None):
    p=Path(__file__).with_name(fn)
    if not p.exists() and fallback: p=Path(__file__).with_name(fallback)/fn
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

front=load('first_handoff_transition_correct_1917552','run_eth_alignment_first_active_handoff_frontier_1917552.py')
trans=load('responsibility_transition_alignment_1917552','responsibility_transition.py','eth_repair_modular')
exec2x2=front.exec2x2;base=front.base;v38=front.v38

class FrozenTransitionMixin:
    def __init__(self,*a,**kw):
        self.transitionPolicy=trans.RepairFirstResponsibilityTransitionV1();self.transitionEvents=[];self.transitionChecks=0;self.transitionBlocks=0
        super().__init__(*a,**kw)
    def _live_repair_debt_by_side(self):
        debt={'UP':0.0,'DOWN':0.0};rows=[]
        for key,m in getattr(self,'v84Composite',{}).items():
            rem=max(0.0,float(m.get('overflowDebt') or 0.0)-float(m.get('overflowPaid') or 0.0))
            if rem<=EPS or m.get('overflowBornAt') is None: continue
            side='DOWN' if str(m.get('side')).upper()=='UP' else 'UP'
            debt[side]+=rem;rows.append({'compositeKey':key,'repairSide':side,'remainingDebt':rem,'bornAt':m.get('overflowBornAt')})
        return debt,rows
    def _score_state(self,t,after_kind):
        self._refresh_carrier_ledger(int(t));debt,rows=self._live_repair_debt_by_side();th=getattr(self,'thesis',None);side=th.get('side') if isinstance(th,dict) else None
        d=self.transitionPolicy.evaluate(trans.ResponsibilityTransitionContext(side,debt['UP'],debt['DOWN']))
        self.transitionChecks+=1
        ev={'t':int(t),'event':'RESPONSIBILITY_TRANSITION_CHECK','afterKind':after_kind,'thesisSide':side,'repairDebtBySide':debt,'debtRows':rows,'allowExpandOwnership':d.allow_expand_ownership,'bindRole':d.bind_role,'reason':d.reason,'liveRepairDebt':d.live_repair_debt}
        if after_kind=='REPAIR' and not d.allow_expand_ownership and d.bind_role=='REPAIR':
            self.transitionBlocks+=1;ev['event']='EXPAND_OWNERSHIP_SUSPENDED_REPAIR_FIRST';self.transitionEvents.append(ev);return None
        self.transitionEvents.append(ev);return super()._score_state(t,after_kind)

class TransitionCorrectFirstHandoff(FrozenTransitionMixin,front.FirstActiveHandoffFrontier):
    def run_transition_frontier(self,models,winner):
        r=self.run_frontier(models,winner);r.update({'responsibilityTransitionPolicy':self.transitionPolicy.name,'transitionChecks':self.transitionChecks,'transitionBlocks':self.transitionBlocks,'transitionEvents':self.transitionEvents[:240]});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=MID:raise ValueError(a.market_id)
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='transition_correct_first_handoff_1917552_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'TRANSITION_CORRECT_FIRST_HANDOFF_1917552','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'TRANSITION_CORRECT_FIRST_HANDOFF_1917552_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        sim=base.make_simulator(TransitionCorrectFirstHandoff,tmp/'tapes'/f'{MID}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=sim.run_transition_frontier(models,cohort[MID]['winner'])
        finally:sim.close()
        counts=r.get('firstHandoffReasonCounts',{}) or {};matched=r.get('firstHandoffReasonCountsMatchedClock',{}) or {};rows=r.get('firstHandoffRows',[]) or []
        matched_rows=[x for x in rows if x.get('overflowBirthClockKnown')];eligible=[x for x in matched_rows if x.get('reason')=='ELIGIBLE_FOR_V89D_ACTIVE_REPAIR'];ss=exec2x2.safety_summary(r,True)
        cons=abs(float(r.get('v84CompositeFillQty') or 0)-float(r.get('v84RepairAllocatedQty') or 0)-float(r.get('v84OverflowAllocatedQty') or 0))<=1e-7
        gates={'transitionConflictExercised':int(r.get('transitionBlocks') or 0)>0,'zeroTruthMismatch':float(ss.get('truthMismatch') or 0)==0,'zeroResponsibilityOverfill':float(ss.get('responsibilityOverfill') or 0)<=EPS,'zeroDuplicateDebt':float(ss.get('duplicateDebt') or 0)<=EPS,'zeroPreBirthLeak':float(ss.get('preBirthLeak') or 0)<=EPS,'zeroSharedOverfill':float(ss.get('sharedOverfill') or 0)<=EPS,'allocationConservation':cons}
        dom=max(matched.items(),key=lambda kv:kv[1])[0] if matched else None
        out={'version':'TRANSITION_CORRECT_FIRST_HANDOFF_1917552_RESULT_20260904','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'decision':'TRANSITION_RESTORED_REDIAGNOSE_FIRST_HANDOFF' if all(gates.values()) else 'TRANSITION_RESTORE_FAILED_OR_UNSAFE','gates':gates,'summary':{'transitionChecks':int(r.get('transitionChecks') or 0),'transitionBlocks':int(r.get('transitionBlocks') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'overflowBirthClockCount':len(r.get('v89OverflowBirthClocks') or []),'activeCompositeSubmits':int(r.get('v89cActiveCompositeSubmits') or 0),'matchedOverflowClockReasonCounts':matched,'dominantMatchedBlocker':dom,'matchedTransitionRows':len(matched_rows),'eligibleRows':len(eligible)},'safety':ss,'transitionEvents':r.get('transitionEvents',[])[:120],'matchedRowsSample':[front.compact(x) for x in matched_rows[:120]],'eligibleRowsSample':[front.compact(x) for x in eligible[:40]],'boundary':['restores frozen RepairFirstResponsibilityTransitionV1 only','SharedParent AllocationLedger V2 unchanged','all V89/V90 execution/admission/timing mechanics otherwise frozen','no Target runtime input','realistic HFT only','no 8781']}
        outp.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'gates':gates,'summary':out['summary'],'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
