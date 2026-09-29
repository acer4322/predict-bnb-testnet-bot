from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,statistics
from pathlib import Path
import numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1917154

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

front=sibling('v70g_partial_debt_front',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
g=front.g;v38=front.v38;v80=front.v80;v1=g.v1

class PartialDebtReexpandShadow(front.ResponsibilityTransitionCandidate):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.partialDebtShadow=[];self._seen=set()
    def _physical_expand_remaining(self):
        key=getattr(self,'v70gGenerationCarrier',None)
        if not key:return 0.0,False,None
        e=getattr(self,'carrierLedger',{}).get(key,{})
        sub=float(e.get('submittedQty') or 0.0);fill=float(e.get('actualFilled') or 0.0);rem=max(0.0,sub-fill)
        live=rem>EPS and not bool(e.get('terminalConfirmed'))
        return rem,live,{'key':key,'submittedQty':sub,'actualFilled':fill,'terminalConfirmed':bool(e.get('terminalConfirmed')),'side':e.get('side'),'role':e.get('objectiveRole')}
    def _shadow_tick(self,t):
        t=int(t)
        if t in self._seen:return
        self._seen.add(t)
        sec=(int(self.capEnd)-t)/1000.0
        if sec<=180.0 or not bool(getattr(self,'v70gGenerationAuthorized',False)):return
        db=max(0.0,float(getattr(self,'v70dGenerationDebt',0.0) or 0.0)-float(getattr(self,'v70gGenerationDebtBaseline',0.0) or 0.0))
        pb=max(0.0,float(getattr(self,'v70dGenerationPaid',0.0) or 0.0)-float(getattr(self,'v70gGenerationPaidBaseline',0.0) or 0.0))
        rem=max(0.0,db-pb)
        if rem<=EPS:return
        qv=v1.quotes(self.book)
        if not qv or self.teacher is None:return
        carrier_rem,carrier_live,carrier=self._physical_expand_remaining()
        try:
            f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
        except Exception:return
        th=getattr(self,'thesis',None);thside=th.get('side') if isinstance(th,dict) else None;signal=self._signal_side(qv);side=thside if thside in ('UP','DOWN') else signal
        rec=self._v75_recoverability(t,side,qv) if side in ('UP','DOWN') else {'recoverable':False,'reason':'NO_SIDE'}
        debt,rows=self._live_repair_debt_by_side()
        try:
            td=self.transitionPolicy.evaluate(front.trans.ResponsibilityTransitionContext(side,debt['UP'],debt['DOWN']));tr_allow=bool(td.allow_expand_ownership);tr_reason=td.reason
        except Exception as exc:
            tr_allow=False;tr_reason=f'ERROR:{type(exc).__name__}'
        try:global_occ=bool(self._expand_occupied())
        except Exception:global_occ=False
        legacy_rec=bool(rec.get('recoverable'))
        alloc_diag=legacy_rec
        alloc_reason='LEGACY_RECOVERABLE' if legacy_rec else None
        if not legacy_rec and str(rec.get('reason'))=='FUTURE_REPAIR_QTY_EXCEEDS_ROOM':
            need=rec.get('futureNeedQty');room=rec.get('repairRoomAfterOwned')
            if need is not None and room is not None and float(need)<=float(room)+EPS:
                alloc_diag=True;alloc_reason='MANAGER_DEBT_RECOVERABLE_PHYSICAL_CARRIER_OVERSHOOT'
        checks=[z for z in getattr(self,'v83Admissions',[]) if int(z.get('t') or -1)==t]
        # Strong conservative shadow: use frozen legacy recoverability, not allocation-aware override, for the primary structural test.
        otherwise=bool((not carrier_live) and pE>=0.5 and tr_allow and side in ('UP','DOWN') and legacy_rec and not global_occ)
        terminal='FULL_DEBT_GENERATION_LOCK_ONLY' if otherwise else (
            'LIVE_EXPAND_CARRIER' if carrier_live else
            'P_EXPAND_BELOW_THRESHOLD' if pE<0.5 else
            'RESPONSIBILITY_TRANSITION_BLOCK' if not tr_allow else
            'UNRECOVERABLE' if not legacy_rec else
            'GLOBAL_EXPAND_OCCUPIED' if global_occ else 'OTHER')
        repair_rows=[]
        try:
            for k,e,r in self.lane_unresolved('REPAIR'):
                if float(r)>EPS:repair_rows.append({'key':str(k),'side':e.get('side'),'remaining':float(r)})
        except Exception:pass
        self.partialDebtShadow.append({'t':t,'secondsLeft':sec,'generationId':int(getattr(self,'v70gGenerationId',0) or 0),'generationDebtIncrement':db,'generationPaidIncrement':pb,'generationRemainingDebt':rem,'partialPaymentObserved':pb>EPS and rem>EPS,'generationCarrier':carrier,'physicalExpandCarrierRemaining':carrier_rem,'physicalExpandCarrierLive':carrier_live,'repairParent':dict(self.repairParent) if isinstance(getattr(self,'repairParent',None),dict) else None,'liveRepairCarriers':repair_rows,'pExpand':pE,'thesisSide':thside,'signalSide':signal,'evaluatedExpandSide':side,'repairDebtBySide':debt,'transitionAllow':tr_allow,'transitionReason':tr_reason,'legacyRecoverable':legacy_rec,'legacyRecoverabilityReason':rec.get('reason'),'allocationAwareDebtRecoverableDiagnostic':alloc_diag,'allocationAwareDiagnosticReason':alloc_reason,'globalExpandOccupied':global_occ,'currentV83CheckAtSameReceipt':bool(checks),'currentV83RowsAtSameReceipt':checks[:4],'wouldBeEligibleExceptFullDebtGenerationLock':otherwise,'terminalShadowReason':terminal})
    def process(self,t):
        out=super().process(t);self._shadow_tick(int(t));return out
    def run_shadow(self,models,winner):
        r=self.run_transition(models,winner);rows=self.partialDebtShadow;elig=[z for z in rows if z['wouldBeEligibleExceptFullDebtGenerationLock']];bins=sorted(set(int(z['t'])//1000 for z in elig));windows=0;last=None
        for b in bins:
            if last is None or b-last>1:windows+=1
            last=b
        vals=sorted(float(z['generationRemainingDebt']) for z in elig)
        def q(p):
            if not vals:return None
            i=min(len(vals)-1,max(0,int(round((len(vals)-1)*p))));return vals[i]
        r.update({'partialDebtReexpandShadow':{'generationLockedReceipts':len(rows),'lockedWithNoLiveExpandCarrierReceipts':sum(not z['physicalExpandCarrierLive'] for z in rows),'lockedOtherwiseEligibleReceipts':len(elig),'lockedOtherwiseEligible1sBins':len(bins),'lockedOtherwiseEligibleWindows':windows,'partialPaidLockedOtherwiseEligibleReceipts':sum(z['partialPaymentObserved'] for z in elig),'remainingDebtAtEligible':{'min':min(vals) if vals else None,'p25':q(.25),'median':q(.5),'p75':q(.75),'max':max(vals) if vals else None},'rows':rows[:700]}});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='v70g_partial_debt_shadow_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'V70G_PARTIAL_DEBT_REEXPAND_SHADOW','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V70G_PARTIAL_DEBT_REEXPAND_SHADOW_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        c=PartialDebtReexpandShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        try:r=c.run_shadow(models,cr['winner'])
        finally:c.close()
        sh=r['partialDebtReexpandShadow'];ss=front.safety(r);allsafe=all(float(v)<=EPS for v in ss.values());supported=sh['lockedOtherwiseEligibleReceipts']>0 and sh['lockedOtherwiseEligibleWindows']>0
        decision='SUPPORT_REPLACING_FULL_DEBT_GENERATION_SERIALIZATION' if supported and allsafe else ('FULL_DEBT_GENERATION_LOCK_NOT_BINDING_IN_1917154' if allsafe else 'BASELINE_SAFETY_NOT_CLEAN')
        out={'version':'ETH_V70G_PARTIAL_DEBT_REEXPAND_SHADOW_1917154','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'behavior':{'fills':r.get('actualFillEvents'),'floor':r.get('floor'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'v83Checks':r.get('v83AdmissionChecks'),'v83Allows':r.get('v83AdmissionAllows'),'generationCount':r.get('v70gGenerationCount'),'generationResponsibilities':r.get('v70gGenerationResponsibilityCount'),'generationDebt':r.get('v70dGenerationDebtQty'),'generationPaid':r.get('v70dGenerationPaidQty'),'rounds':r.get('v70dSemanticRounds')},'shadow':sh,'safety':ss,'allSafetyZero':allsafe,'boundary':['behavior-inert','development market only','primary eligible test keeps legacy V75 recoverability frozen','allocation-aware recoverability is diagnostic only','no debt deletion','no threshold/qty/price/delay change','ResponsibilityTransition/AllocationLedgerV2/RepairExecutionRouter frozen','realistic HFT only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'behavior':out['behavior'],'shadow':{k:v for k,v in sh.items() if k!='rows'},'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
