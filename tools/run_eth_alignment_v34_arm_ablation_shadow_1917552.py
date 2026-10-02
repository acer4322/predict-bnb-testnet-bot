from __future__ import annotations
import argparse, importlib.util, json, math, os, shutil, statistics, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
MID=1917552; EPS=1e-9

def load(name,fn):
    p=Path(__file__).with_name(fn);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

tc=load('transition_correct_for_arm_ablation_1917552','run_eth_alignment_transition_correct_first_handoff_1917552.py')
front=tc.front;exec2x2=tc.exec2x2;base=tc.base;v38=tc.v38;v1=front.v1

class V34ArmAblationShadow(tc.TransitionCorrectFirstHandoff):
    def __init__(self,*a,**kw):
        self.shadowBirthFillBase={};self.armAblationRows=[];self.armAblationReasonCounts={}
        super().__init__(*a,**kw)
    def _classify_first_handoff(self,t:int):
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict): return None
        pid=int(rp.get('id') or -1);side=str(rp.get('side') or '');born=int(rp.get('bornAt') or -1)
        birth_known=born in getattr(self,'v89OverflowBirthClocks',set())
        current_fill=float(self._parent_actual_fill(pid));
        if birth_known: self.shadowBirthFillBase.setdefault(pid,current_fill)
        base_fill=float(self.shadowBirthFillBase.get(pid,current_fill));progress=current_fill>base_fill+EPS
        churn=[x for x in getattr(self,'repairChurn',[]) if int(x.get('parentId') or -1)==pid]
        hard=pid in getattr(self,'hardConfirmed',set());active_owned=pid in getattr(self,'activeByParent',{})
        pay=self._current_payoffs();qv=v1.quotes(self.book);ask=None;legal=None
        if qv and side in ('UP','DOWN') and side in qv and qv[side].get('ask') is not None:
            ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf
        sec_left=(int(self.capEnd)-int(t))/1000.0;cap_count=int(getattr(self,'v89cActiveCompositeSubmits',0) or 0)
        reason='ELIGIBLE_WITHOUT_V34_ARM'
        if cap_count>=2: reason='V89D_TWO_HANDOFF_SMOKE_CAP_REACHED'
        elif not birth_known: reason='OVERFLOW_BIRTH_CLOCK_NOT_REGISTERED'
        elif active_owned: reason='ACTIVE_ALREADY_OWNED'
        elif hard: reason='PARENT_ALREADY_HARD_CONFIRMED'
        elif side not in ('UP','DOWN'): reason='INVALID_REPAIR_SIDE'
        elif not churn: reason='NO_REPAIR_CHURN_EVIDENCE'
        elif progress: reason='PAYMENT_PROGRESS_SINCE_SHADOW_BIRTH'
        elif float(pay['floor'])>=-EPS: reason='NO_NEGATIVE_FLOOR'
        elif float(pay['gap'])<=EPS: reason='NO_REPAIR_GAP'
        elif sec_left<=180.0: reason='LATE_V89D_ACTIVE_BLOCK'
        elif ask is None or legal is None: reason='NO_ACTIVE_ASK'
        elif not math.isfinite(float(legal)) or float(legal)<=EPS or float(legal)>12.0+EPS: reason='ILLEGAL_ACTIVE_SLICE'
        floor_before,u,d,cost=self._raw_floor();best_before=max(float(u),float(d))-float(cost)
        full_floor_after=None;full_best_after=None;repair_qty=0.0;overflow_qty=0.0
        if ask is not None and legal is not None and math.isfinite(float(legal)):
            q=float(legal);ai=self.auth_inv();opp='DOWN' if side=='UP' else 'UP';manager_debt=max(0.0,float(ai[opp])-float(ai[side]));repair_qty=min(q,manager_debt);overflow_qty=max(0.0,q-repair_qty)
            hu=float(u)+(q if side=='UP' else 0.0);hd=float(d)+(q if side=='DOWN' else 0.0);hc=float(cost)+q*float(ask)
            full_floor_after=min(hu,hd)-hc;full_best_after=max(hu,hd)-hc
        ti=getattr(self,'truthInv',{}) or {};truth_weak='UP' if float(ti.get('UP',0))<float(ti.get('DOWN',0))-EPS else ('DOWN' if float(ti.get('DOWN',0))<float(ti.get('UP',0))-EPS else 'BAL')
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.capEnd);phase=((int(t)-(end-300000))/3000.0)
        row={'t':int(t),'phasePct':phase,'secondsLeft':sec_left,'parentId':pid,'parentSide':side,'parentBornAt':born,'overflowBirthClockKnown':birth_known,'oldV34Armed':pid in getattr(self,'_armedParents',set()),'armed':pid in getattr(self,'_armedParents',set()),'churnCount':len(churn),'shadowBirthFillBase':base_fill,'parentActualFill':current_fill,'paymentProgressSinceShadowBirth':progress,'paymentProgressSinceArm':progress,'activeAlreadyOwned':active_owned,'hardConfirmed':hard,'floor':float(pay['floor']),'managerGap':float(pay['gap']),'liveAsk':ask,'legalPhysicalQty':legal,'repairAllocationIfFull':repair_qty,'overflowIfFull':overflow_qty,'fullCarrierFloorAfter':full_floor_after,'fullCarrierFloorDelta':(full_floor_after-float(floor_before) if full_floor_after is not None else None),'fullCarrierBestAfter':full_best_after,'fullCarrierBestDelta':(full_best_after-best_before if full_best_after is not None else None),'truthWeakSide':truth_weak,'truthSideConsistentRepair':side==truth_weak,'reason':reason}
        self.armAblationReasonCounts[reason]=self.armAblationReasonCounts.get(reason,0)+1
        if birth_known and (reason=='ELIGIBLE_WITHOUT_V34_ARM' or len(self.armAblationRows)<1000): self.armAblationRows.append(dict(row))
        return row
    def run_shadow(self,models,winner):
        r=self.run_transition_frontier(models,winner);r['armAblationReasonCounts']=dict(self.armAblationReasonCounts);r['armAblationRows']=self.armAblationRows[:1800];return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=MID:raise ValueError(a.market_id)
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='v34_arm_ablation_1917552_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'V34_ARM_ABLATION_1917552','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V34_ARM_ABLATION_1917552_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        sim=base.make_simulator(V34ArmAblationShadow,tmp/'tapes'/f'{MID}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=sim.run_shadow(models,cohort[MID]['winner'])
        finally:sim.close()
        rows=r.get('armAblationRows',[]) or [];eligible=[x for x in rows if x.get('reason')=='ELIGIBLE_WITHOUT_V34_ARM'];safe=[x for x in eligible if x.get('truthSideConsistentRepair') and x.get('fullCarrierFloorDelta') is not None and float(x['fullCarrierFloorDelta'])>=-EPS]
        phases=[float(x['phasePct']) for x in eligible]
        ss=exec2x2.safety_summary(r,True);gates={'behaviorStillUsesOldArmGate':int(r.get('v89cActiveCompositeSubmits') or 0)==0,'zeroTruthMismatch':float(ss.get('truthMismatch') or 0)==0,'shadowExercisesArmAblation':len(eligible)>0}
        out={'version':'V34_ARM_ABLATION_SHADOW_1917552_RESULT_20260904','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'behaviorMutation':False,'decision':'ARM_IS_BINDING_AND_OTHER_GATES_HAVE_SUPPORT' if all(gates.values()) else ('ARM_ABLATION_NO_SUPPORT' if not eligible else 'DIAGNOSE_ARM_ABLATION'), 'gates':gates,'summary':{'repairParentBirths':int(r.get('repairParentBirths') or 0),'overflowBirthClockCount':len(r.get('v89OverflowBirthClocks') or []),'actualActiveCompositeSubmits':int(r.get('v89cActiveCompositeSubmits') or 0),'shadowReasonCounts':r.get('armAblationReasonCounts',{}),'eligibleWithoutArmRows':len(eligible),'truthConsistentFloorNonWorseEligibleRows':len(safe),'eligiblePhasePctMedian':statistics.median(phases) if phases else None,'eligiblePhasePctMin':min(phases) if phases else None,'eligiblePhasePctMax':max(phases) if phases else None},'safety':ss,'eligibleRows':eligible[:240],'safeEligibleRows':safe[:120],'boundary':['frozen ResponsibilityTransition V1 ON','actual behavior still retains V34 arm gate','shadow ignores only PARENT_NOT_ARMED','shadow payment baseline fixed at first matched overflow clock','no Target runtime input','normalized phase descriptive only','realistic HFT only','no 8781']}
        outp.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'gates':gates,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
