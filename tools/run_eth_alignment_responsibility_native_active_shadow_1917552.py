from __future__ import annotations
import argparse, importlib.util, json, math, os, shutil, statistics, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
MID=1917552; EPS=1e-9

def load(name,fn):
    p=Path(__file__).with_name(fn); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

arm=load('arm_ablation_for_responsibility_native_1917552','run_eth_alignment_v34_arm_ablation_shadow_1917552.py')
tc=arm.tc; exec2x2=arm.exec2x2; base=arm.base; v38=arm.v38

class ResponsibilityNativeActiveShadow(arm.V34ArmAblationShadow):
    def __init__(self,*a,**kw):
        self.nativeRows=[]; self.nativeReasonCounts={}
        super().__init__(*a,**kw)

    def _classify_first_handoff(self,t:int):
        row=super()._classify_first_handoff(t)
        if row is None: return None
        original=str(row.get('reason') or '')
        reason=original
        # Shadow-only second ablation: a live Repair responsibility does not need
        # a prior passive-carrier churn event before Active route eligibility.
        if original in ('NO_REPAIR_CHURN_EVIDENCE','ELIGIBLE_WITHOUT_V34_ARM'):
            progress=bool(row.get('paymentProgressSinceShadowBirth'))
            floor=float(row.get('floor') or 0.0)
            gap=float(row.get('managerGap') or 0.0)
            sec=float(row.get('secondsLeft') or 0.0)
            ask=row.get('liveAsk'); legal=row.get('legalPhysicalQty')
            if progress:
                reason='PAYMENT_PROGRESS_SINCE_SHADOW_BIRTH'
            elif floor>=-EPS:
                reason='NO_NEGATIVE_FLOOR'
            elif gap<=EPS:
                reason='NO_REPAIR_GAP'
            elif sec<=180.0:
                reason='LATE_V89D_ACTIVE_BLOCK'
            elif ask is None or legal is None:
                reason='NO_ACTIVE_ASK'
            elif (not math.isfinite(float(legal))) or float(legal)<=EPS or float(legal)>12.0+EPS:
                reason='ILLEGAL_ACTIVE_SLICE'
            else:
                reason='ELIGIBLE_RESPONSIBILITY_NATIVE_ACTIVE'
        z=dict(row); z['preNativeAblationReason']=original; z['reason']=reason
        self.nativeReasonCounts[reason]=self.nativeReasonCounts.get(reason,0)+1
        if bool(z.get('overflowBirthClockKnown')) and (reason=='ELIGIBLE_RESPONSIBILITY_NATIVE_ACTIVE' or len(self.nativeRows)<1200):
            self.nativeRows.append(z)
        # Parent diagnostic expects these compatibility fields; actual behavior
        # remains governed by inherited V34/V36 path after this classifier returns.
        return z

    def run_native_shadow(self,models,winner):
        r=self.run_transition_frontier(models,winner)
        r['responsibilityNativeReasonCounts']=dict(self.nativeReasonCounts)
        r['responsibilityNativeRows']=self.nativeRows[:2000]
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    if a.market_id!=MID: raise ValueError(a.market_id)
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output); outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='responsibility_native_active_1917552_')); stop=threading.Event()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'RESPONSIBILITY_NATIVE_ACTIVE_1917552','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'RESPONSIBILITY_NATIVE_ACTIVE_1917552_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        sim=base.make_simulator(ResponsibilityNativeActiveShadow,tmp/'tapes'/f'{MID}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
        try: r=sim.run_native_shadow(models,cohort[MID]['winner'])
        finally: sim.close()
        rows=r.get('responsibilityNativeRows',[]) or []
        eligible=[x for x in rows if x.get('reason')=='ELIGIBLE_RESPONSIBILITY_NATIVE_ACTIVE']
        truth=[x for x in eligible if bool(x.get('truthSideConsistentRepair'))]
        floor_nonworse=[x for x in truth if x.get('fullCarrierFloorDelta') is not None and float(x['fullCarrierFloorDelta'])>=-EPS]
        positive_floor=[x for x in truth if x.get('fullCarrierFloorDelta') is not None and float(x['fullCarrierFloorDelta'])>EPS]
        phases=[float(x['phasePct']) for x in eligible]
        fd=[float(x['fullCarrierFloorDelta']) for x in truth if x.get('fullCarrierFloorDelta') is not None]
        bd=[float(x['fullCarrierBestDelta']) for x in truth if x.get('fullCarrierBestDelta') is not None]
        ss=exec2x2.safety_summary(r,True)
        gates={
            'actualBehaviorStillUsesOldArmAndChurn': int(r.get('v89cActiveCompositeSubmits') or 0)==0,
            'zeroTruthMismatch': float(ss.get('truthMismatch') or 0)==0,
            'shadowExercisesResponsibilityNativePath': len(eligible)>0,
            'atLeastOneTruthCorrectCandidate': len(truth)>0
        }
        decision='RESPONSIBILITY_NATIVE_ACTIVE_HAS_SHADOW_SUPPORT' if all(gates.values()) else ('NO_RESPONSIBILITY_NATIVE_SUPPORT' if not eligible else 'DIAGNOSE_RESPONSIBILITY_NATIVE_SHADOW')
        summary={
            'repairParentBirths':int(r.get('repairParentBirths') or 0),
            'overflowBirthClockCount':len(r.get('v89OverflowBirthClocks') or []),
            'actualActiveCompositeSubmits':int(r.get('v89cActiveCompositeSubmits') or 0),
            'shadowReasonCounts':r.get('responsibilityNativeReasonCounts',{}),
            'eligibleRows':len(eligible),
            'truthCorrectEligibleRows':len(truth),
            'truthCorrectFloorNonWorseRows':len(floor_nonworse),
            'truthCorrectPositiveFloorDeltaRows':len(positive_floor),
            'eligiblePhasePctMin':min(phases) if phases else None,
            'eligiblePhasePctMedian':statistics.median(phases) if phases else None,
            'eligiblePhasePctMax':max(phases) if phases else None,
            'truthCorrectFloorDeltaMedian':statistics.median(fd) if fd else None,
            'truthCorrectFloorDeltaMin':min(fd) if fd else None,
            'truthCorrectFloorDeltaMax':max(fd) if fd else None,
            'truthCorrectBestDeltaMedian':statistics.median(bd) if bd else None,
            'truthCorrectBestDeltaMin':min(bd) if bd else None,
            'truthCorrectBestDeltaMax':max(bd) if bd else None
        }
        out={
            'version':'RESPONSIBILITY_NATIVE_ACTIVE_SHADOW_1917552_RESULT_20260904',
            'date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'behaviorMutation':False,
            'decision':decision,'gates':gates,'summary':summary,'safety':ss,
            'eligibleRows':eligible[:320],'truthCorrectEligibleRows':truth[:240],'floorNonWorseEligibleRows':floor_nonworse[:160],
            'boundary':[
                'frozen ResponsibilityTransition V1 ON',
                'actual behavior retains V34 arm + passive-churn prerequisites',
                'shadow ignores only PARENT_NOT_ARMED and NO_REPAIR_CHURN_EVIDENCE',
                'payment progress / ownership / legal qty / >180s / floor-gap checks unchanged',
                'Target evidence post-hoc only; no Target runtime input',
                'realistic HFT only; no dream fill; no 8781'
            ]
        }
        outp.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'gates':gates,'summary':summary},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
