from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
from collections import Counter
import numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9
FIXED=[1916845,1916847,1916869]

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

front=sibling('continuous_parallel_front',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
g=front.g;v38=front.v38;v80=front.v80;v1=g.v1

class ContinuousParallelSchedulerShadow(front.ResponsibilityTransitionCandidate):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.continuousShadowRows=[]
        self._shadowSeenT=set()

    def _shadow_tick(self,t):
        t=int(t)
        if t in self._shadowSeenT:return
        self._shadowSeenT.add(t)
        sec=(int(self.capEnd)-t)/1000.0
        parent=getattr(self,'repairParent',None)
        sdec=self._scheduler_decision(t,after_kind=None,receipt_advanced=True)
        if sdec is None or not bool(sdec.reevaluate_management):
            return
        if not isinstance(parent,dict) or self.teacher is None or float(getattr(self,'_coordDebt',0.0) or 0.0)<=EPS:
            return
        qv=v1.quotes(self.book)
        if not qv:return
        try:
            f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32)
            pE=float(self.teacher['model'].predict_proba(x)[0,1])
        except Exception:
            return
        repair_rows=[];repair_qty=0.0
        try:
            for key,e,rem in self.lane_unresolved('REPAIR'):
                if float(rem)<=EPS:continue
                repair_rows.append({'key':str(key),'side':e.get('side'),'remaining':float(rem),'role':e.get('objectiveRole')})
                repair_qty+=float(rem)
        except Exception:pass
        th=getattr(self,'thesis',None);thside=th.get('side') if isinstance(th,dict) else None
        signal=self._signal_side(qv)
        side=thside if thside in ('UP','DOWN') else signal
        rec=self._v75_recoverability(t,side,qv) if side in ('UP','DOWN') else {'recoverable':False,'reason':'NO_SIDE'}
        ownership_would_exist=bool(thside in ('UP','DOWN'))
        ownership_reason='EXISTING_THESIS' if ownership_would_exist else 'NO_THESIS'
        if not ownership_would_exist and side in ('UP','DOWN'):
            try:
                ctx=v80.OwnershipContext(t=t,seconds_left=sec,has_thesis=False,p_expand=pE,signal_side=side,recoverable=bool(rec.get('recoverable')))
                dec=self.policyProfile.ownership.evaluate(ctx);ownership_would_exist=bool(dec.create_thesis);ownership_reason=dec.reason
            except Exception as exc:
                ownership_reason=f'OWNERSHIP_EVAL_ERROR:{type(exc).__name__}'
        debt,debrows=self._live_repair_debt_by_side()
        try:
            td=self.transitionPolicy.evaluate(front.trans.ResponsibilityTransitionContext(side,debt['UP'],debt['DOWN']))
            transition_allow=bool(td.allow_expand_ownership);transition_reason=td.reason
        except Exception as exc:
            transition_allow=False;transition_reason=f'TRANSITION_EVAL_ERROR:{type(exc).__name__}'
        gen_owned=bool(getattr(self,'v70gGenerationAuthorized',False))
        try:expand_occ=bool(self._expand_occupied())
        except Exception:expand_occ=False
        current_checks=[r for r in getattr(self,'v83Admissions',[]) if int(r.get('t') or -1)==t]
        current_check=bool(current_checks);current_allow=any(bool(r.get('submit')) for r in current_checks)

        alloc_diag=False;alloc_reason=None
        if bool(rec.get('recoverable')):
            alloc_diag=True;alloc_reason='LEGACY_RECOVERABLE'
        elif str(rec.get('reason'))=='FUTURE_REPAIR_QTY_EXCEEDS_ROOM':
            need=rec.get('futureNeedQty');room=rec.get('repairRoomAfterOwned')
            if need is not None and room is not None and float(need)<=float(room)+EPS:
                alloc_diag=True;alloc_reason='MANAGER_DEBT_RECOVERABLE_PHYSICAL_CARRIER_OVERSHOOT'

        if pE<0.5:terminal='OPPORTUNITY_BELOW_FROZEN_THRESHOLD'
        elif not transition_allow:terminal='RESPONSIBILITY_TRANSITION_BLOCK'
        elif not ownership_would_exist:terminal='NO_RECOVERABLE_THESIS_OWNERSHIP'
        elif not bool(rec.get('recoverable')):terminal='LEGACY_WHOLE_PORTFOLIO_UNRECOVERABLE'
        elif gen_owned:terminal='GENERATION_ALREADY_OWNS_EXPAND'
        elif expand_occ:terminal='GLOBAL_EXPAND_OCCUPIED'
        else:terminal='CORE_ECONOMIC_ELIGIBLE'
        core=terminal=='CORE_ECONOMIC_ELIGIBLE'
        row={
            't':t,'secondsLeft':sec,'pExpand':pE,
            'repairParentId':parent.get('id'),'repairParentSide':parent.get('side'),
            'liveRepairCarrierCount':len(repair_rows),'liveRepairCarrierQty':repair_qty,
            'liveRepairRows':repair_rows[:8],
            'existingThesisSide':thside,'signalSide':signal,'evaluatedExpandSide':side,
            'ownershipWouldExist':ownership_would_exist,'ownershipReason':ownership_reason,
            'repairDebtBySide':debt,'transitionAllow':transition_allow,'transitionReason':transition_reason,
            'legacyRecoverable':bool(rec.get('recoverable')),'legacyRecoverabilityReason':rec.get('reason'),
            'allocationAwareDebtRecoverableDiagnostic':alloc_diag,'allocationAwareDiagnosticReason':alloc_reason,
            'generationExpandOwned':gen_owned,'globalExpandOccupied':expand_occ,
            'currentV83CheckAtSameReceipt':current_check,'currentV83AllowAtSameReceipt':current_allow,
            'terminalShadowReason':terminal,'coreEconomicEligible':core,
            'parallelWithLiveRepairCarrier':bool(core and len(repair_rows)>0),
            'schedulerModule':getattr(self.policyProfile.scheduler,'name',type(self.policyProfile.scheduler).__name__),
            'schedulerReason':sdec.reason,
        }
        self.continuousShadowRows.append(row)

    def process(self,t):
        out=super().process(t)
        self._shadow_tick(int(t))
        return out

    def run_shadow(self,models,winner):
        r=self.run_transition(models,winner)
        rows=self.continuousShadowRows
        eligible=[z for z in rows if z['coreEconomicEligible']]
        off=[z for z in eligible if not z['currentV83CheckAtSameReceipt']]
        bins=sorted(set(int(z['t'])//1000 for z in eligible));offbins=sorted(set(int(z['t'])//1000 for z in off))
        windows=0;last=None
        for b in bins:
            if last is None or b-last>1:windows+=1
            last=b
        reasons=Counter(z['terminalShadowReason'] for z in rows)
        r.update({
            'continuousSchedulerShadow':{
                'repairLiveReceipts':len(rows),
                'currentV83CheckReceipts':sum(bool(z['currentV83CheckAtSameReceipt']) for z in rows),
                'currentV83AllowReceipts':sum(bool(z['currentV83AllowAtSameReceipt']) for z in rows),
                'coreEconomicEligibleClocks':len(eligible),
                'coreEconomicEligible1sBins':len(bins),
                'coreEconomicEligibilityWindows':windows,
                'eligibleWhileRepairCarrierLiveClocks':sum(bool(z['parallelWithLiveRepairCarrier']) for z in eligible),
                'eligibleWithoutSameReceiptV83CheckClocks':len(off),
                'eligibleWithoutSameReceiptV83Check1sBins':len(offbins),
                'serializationGapRatio':len(off)/max(len(eligible),1),
                'terminalReasons':dict(reasons),
                'rows':rows[:600],
            }
        })
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if any(x not in FIXED for x in mids):raise ValueError(f'only preregistered development markets allowed: {mids}')
    tmp=Path(tempfile.mkdtemp(prefix='continuous_parallel_shadow_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'CONTINUOUS_PARALLEL_SCHEDULER_SHADOW','ts':time.time(),'markets':mids}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'CONTINUOUS_PARALLEL_SCHEDULER_SHADOW_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for mid in mids:
            cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            c=ContinuousParallelSchedulerShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=__import__('tools.eth_repair_modular',fromlist=['economic_continuous_scheduler_shadow_v1_profile']).economic_continuous_scheduler_shadow_v1_profile())
            try:r=c.run_shadow(models,cr['winner'])
            finally:c.close()
            sh=r['continuousSchedulerShadow'];ss=front.safety(r)
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'behavior':{'fills':r.get('actualFillEvents'),'floor':r.get('floor'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'v83Checks':r.get('v83AdmissionChecks'),'v83Allows':r.get('v83AdmissionAllows'),'transitionBlocks':r.get('transitionBlocks')},'shadow':{k:v for k,v in sh.items() if k!='rows'},'shadowRows':sh['rows'],'safety':ss};rows.append(row)
            print(json.dumps({'marketId':mid,'behavior':row['behavior'],'shadow':row['shadow'],'safety':ss},ensure_ascii=False),flush=True)
        agg={
            'markets':len(rows),
            'repairLiveReceipts':sum(x['shadow']['repairLiveReceipts'] for x in rows),
            'currentV83CheckReceipts':sum(x['shadow']['currentV83CheckReceipts'] for x in rows),
            'coreEconomicEligibleClocks':sum(x['shadow']['coreEconomicEligibleClocks'] for x in rows),
            'coreEconomicEligible1sBins':sum(x['shadow']['coreEconomicEligible1sBins'] for x in rows),
            'coreEconomicEligibilityWindows':sum(x['shadow']['coreEconomicEligibilityWindows'] for x in rows),
            'eligibleWhileRepairCarrierLiveClocks':sum(x['shadow']['eligibleWhileRepairCarrierLiveClocks'] for x in rows),
            'eligibleWithoutSameReceiptV83CheckClocks':sum(x['shadow']['eligibleWithoutSameReceiptV83CheckClocks'] for x in rows),
            'eligibleWithoutSameReceiptV83Check1sBins':sum(x['shadow']['eligibleWithoutSameReceiptV83Check1sBins'] for x in rows),
        }
        agg['serializationGapRatio']=agg['eligibleWithoutSameReceiptV83CheckClocks']/max(agg['coreEconomicEligibleClocks'],1)
        allsafe=all(all(float(v)<=EPS for v in x['safety'].values()) for x in rows)
        supported=any(x['shadow']['eligibleWithoutSameReceiptV83Check1sBins']>0 for x in rows) and agg['eligibleWithoutSameReceiptV83Check1sBins']>agg['currentV83CheckReceipts']
        decision='SUPPORT_CONTINUOUS_PARALLEL_SCHEDULER_BEHAVIOR_SMOKE' if supported and allsafe else ('SERIAL_CLOCK_NOT_PRIMARY_BOTTLENECK' if allsafe else 'SHADOW_BASELINE_SAFETY_NOT_CLEAN')
        out={'version':'ETH_CONTINUOUS_PARALLEL_SCHEDULER_SHADOW_V1','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'fixedDevelopmentMarkets':mids,'decision':decision,'aggregate':agg,'allSafetyZero':allsafe,'rows':rows,'boundary':['behavior-inert shadow','current transition-correct behavior preserved','continuous reevaluation borrows cadence concept only from R2/R3','no action submit from shadow','pExpand 0.50 frozen','legacy recoverability frozen for core eligibility','allocation-aware recoverability diagnostic separate','no Target future runtime','realistic HFT only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'allSafetyZero':allsafe},ensure_ascii=False),flush=True)
    finally:
        stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
