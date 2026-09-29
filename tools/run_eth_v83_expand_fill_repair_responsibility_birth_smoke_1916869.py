from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
base=sibling('allocaware_direct_for_expand_fill_birth',Path(__file__).resolve().with_name('run_eth_v83_allocation_aware_direct_recoverability_smoke_1916869.py'))
EPS=1e-9;FIXED=1916869;v38=base.v38;v80=base.v80

class ExpandFillRepairResponsibilityBirth(base.AllocationAwareDirectRecoverability):
    def __init__(self,*a,**kw):
        self.v83SeenCum={};self.expandFillBirthPending=[];self.expandFillBirthEvents=[];self.expandFillBirths=0
        super().__init__(*a,**kw)
    def _router_snapshot(self):
        return {
            'repairParentBirths':int(getattr(self,'repairParentBirths',0) or 0),
            'repairChildCommits':int(getattr(self,'repairChildCommits',0) or 0),
            'timingRepairSubmits':int(getattr(self,'timingRepairSubmits',0) or 0),
            'v38PassiveRepairFillEvents':(len(getattr(self,'v38PassiveRepairFillEvents',[])) if isinstance(getattr(self,'v38PassiveRepairFillEvents',0),list) else int(getattr(self,'v38PassiveRepairFillEvents',0) or 0)),
            'v36ActiveFillQty':float(getattr(self,'v36ActiveFillQty',0.0) or 0.0),
            'modularActiveCompositeSubmits':int(getattr(self,'modularActiveCompositeSubmits',0) or 0),
        }
    def _matching_live_parent(self,side):
        p=getattr(self,'repairParent',None)
        return isinstance(p,dict) and p.get('side')==side
    def _activate_pending_after_process(self,t):
        ai=self.auth_inv();weak='UP' if float(ai['UP'])<float(ai['DOWN'])-EPS else 'DOWN' if float(ai['DOWN'])<float(ai['UP'])-EPS else None
        for p in self.expandFillBirthPending:
            if p.get('terminal') or int(t)<=int(p['fillAt']):continue
            side=p['repairSide'];rp=getattr(self,'repairParent',None)
            ev={'event':'EXPAND_FILL_REPAIR_RESPONSIBILITY_RESOLUTION','t':int(t),'sourceKey':p['sourceKey'],'sourceObjectiveId':p.get('sourceObjectiveId'),'fillAt':int(p['fillAt']),'expandSide':p['expandSide'],'repairSide':side,'confirmedExpandFillQty':float(p['qty']),'weakSideAtResolution':weak,'routerBefore':self._router_snapshot()}
            if self._matching_live_parent(side):
                p['terminal']='ALREADY_OWNED';ev['terminal']='ALREADY_OWNED';ev['repairParent']=dict(rp)
            elif isinstance(rp,dict):
                p['terminal']='OWNERSHIP_CONFLICT';ev['terminal']='OWNERSHIP_CONFLICT';ev['repairParent']=dict(rp)
            elif weak!=side:
                p['terminal']='SELF_SETTLED';ev['terminal']='SELF_SETTLED'
            else:
                before=int(getattr(self,'repairParentBirths',0) or 0)
                self._maybe_birth_parent(int(t),side,{'repair_obligation_30s':1.0})
                after=int(getattr(self,'repairParentBirths',0) or 0)
                if after>before and self._matching_live_parent(side):
                    self.expandFillBirths+=1;p['terminal']='BORN';p['birthAt']=int(t);ev['terminal']='BORN';ev['repairParent']=dict(self.repairParent);ev['strictlyLaterBirth']=int(t)>int(p['fillAt'])
                else:
                    p['terminal']='BIRTH_FAILED';ev['terminal']='BIRTH_FAILED';ev['repairParent']=dict(self.repairParent) if isinstance(getattr(self,'repairParent',None),dict) else None
            ev['routerAfterBirth']=self._router_snapshot();self.expandFillBirthEvents.append(ev)
    def _detect_v83_fill_after_process(self,t):
        admitted={str(r.get('key')):r for r in getattr(self,'v83Admissions',[]) if r.get('submit') and r.get('key')}
        for key,row in admitted.items():
            o=getattr(self,'orders',{}).get(key)
            if not isinstance(o,dict):continue
            cur=float(o.get('cum') or 0.0);old=float(self.v83SeenCum.get(key,0.0))
            if cur<=old+EPS:continue
            inc=cur-old;self.v83SeenCum[key]=cur;side=str(o.get('side') or row.get('side')).upper();repair='DOWN' if side=='UP' else 'UP'
            p={'sourceKey':key,'sourceObjectiveId':row.get('objectiveId'),'fillAt':int(t),'expandSide':side,'repairSide':repair,'qty':float(inc),'terminal':None}
            self.expandFillBirthPending.append(p);self.expandFillBirthEvents.append({'event':'V83_CONFIRMED_EXPAND_FILL_SCHEDULES_REPAIR_RESPONSIBILITY','t':int(t),'sourceKey':key,'sourceObjectiveId':row.get('objectiveId'),'expandSide':side,'repairSide':repair,'confirmedExpandFillQty':float(inc),'sameReceiptBirth':False,'routerAtFill':self._router_snapshot()})
    def process(self,t):
        out=super().process(t)
        # All venue fills at receipt t have already materialized. Older pending obligations may now birth; new fills only schedule for a later receipt.
        self._activate_pending_after_process(int(t))
        self._detect_v83_fill_after_process(int(t))
        return out
    def run_candidate(self,models,winner):
        r=self.run_transition(models,winner)
        end_snap=self._router_snapshot()
        for ev in self.expandFillBirthEvents:
            if ev.get('terminal')=='BORN':ev['routerAtEnd']=end_snap;ev['routerDeltaAfterBirth']={k:(float(end_snap.get(k,0))-float(ev.get('routerAfterBirth',{}).get(k,0))) for k in end_snap}
        r.update({'expandFillResponsibilityBirths':self.expandFillBirths,'expandFillResponsibilityPending':self.expandFillBirthPending,'expandFillResponsibilityEvents':self.expandFillBirthEvents[:160],'expandFillRouterEnd':end_snap})
        return r

def slim(r):
    return {'fills':int(r.get('actualFillEvents') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairChildCommits':int(r.get('repairChildCommits') or 0),'timingRepairSubmits':int(r.get('timingRepairSubmits') or 0),'passiveRepairFillEvents':int(r.get('v38PassiveRepairFillEvents') or 0),'activeRepairFillQty':float(r.get('v36ActiveFillQty') or 0.0),'activeCompositeSubmits':int(r.get('modularActiveCompositeSubmits') or 0),'v83Allows':int(r.get('v83AdmissionAllows') or 0),'v83Blocks':int(r.get('v83AdmissionBlocks') or 0),'rounds':int(r.get('v70dSemanticRounds') or 0),'overflowPaid':float(r.get('v84OverflowPaidQty') or 0.0),'overflowRemaining':float(r.get('v84OverflowRemainingQty') or 0.0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='v83_expand_fill_birth_1916869_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'V83_EXPAND_FILL_REPAIR_BIRTH_1916869','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V83_EXPAND_FILL_REPAIR_BIRTH_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        b=mk(base.AllocationAwareDirectRecoverability)
        try:br=b.run_candidate(models,cr['winner'])
        finally:b.close()
        c=mk(ExpandFillRepairResponsibilityBirth)
        try:rr=c.run_candidate(models,cr['winner'])
        finally:c.close()
        ss=base.front.safety(rr);cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        events=rr.get('expandFillResponsibilityEvents',[]);fills=[e for e in events if e.get('event')=='V83_CONFIRMED_EXPAND_FILL_SCHEDULES_REPAIR_RESPONSIBILITY'];births=[e for e in events if e.get('terminal')=='BORN']
        strict=all(int(e.get('t'))>int(e.get('fillAt')) for e in births);same_receipt=any(e.get('sameReceiptBirth') for e in events)
        router_keys=['repairChildCommits','timingRepairSubmits','passiveRepairFillEvents','activeRepairFillQty','activeCompositeSubmits','rounds']
        bs=slim(br);cs=slim(rr);router_progress=any(float(cs[k])>float(bs[k])+EPS for k in router_keys)
        actual_repair_fill=(cs['passiveRepairFillEvents']>bs['passiveRepairFillEvents'] or cs['activeRepairFillQty']>bs['activeRepairFillQty']+EPS)
        safety_zero=all(float(v)<=EPS for v in ss.values())
        gates={'v83ConfirmedExpandFillDetected':len(fills)>0,'responsibilityResolutionObserved':any(e.get('terminal') in ('BORN','ALREADY_OWNED','SELF_SETTLED','OWNERSHIP_CONFLICT','BIRTH_FAILED') for e in events),'zeroSameReceiptBirth':not same_receipt,'allBirthsStrictlyLaterThanFill':strict,'allocationConservation':cons,'safetyZero':safety_zero}
        if not safety_zero or not cons or same_receipt or not strict:
            decision='REJECT_EXPAND_FILL_RESPONSIBILITY_BIRTH'
        elif births and actual_repair_fill:
            decision='FUNCTIONAL_PASS_REPAIR_FILL_REACHED_KEEP_FOR_UNSEEN_SMOKE'
        elif births and router_progress:
            decision='BIRTH_AND_ROUTER_PROGRESS_EXECUTION_INCONCLUSIVE'
        elif births:
            decision='BIRTH_PASS_ROUTER_UNREACHABLE_DIAGNOSE_ROUTER'
        elif any(e.get('terminal')=='ALREADY_OWNED' for e in events):
            decision='EXISTING_PARENT_OWNED_BUT_ROUTER_UNREACHABLE_DIAGNOSE_ROUTER'
        else:
            decision='NO_BIRTH_DIAGNOSE_RESPONSIBILITY_RESOLUTION'
        out={'version':'ETH_V83_EXPAND_FILL_REPAIR_RESPONSIBILITY_BIRTH_SMOKE_1916869','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baselineAllocationAware':bs,'candidateBirthSeam':cs,'safety':ss,'routerProgressVsBaseline':router_progress,'actualRepairFillProgress':actual_repair_fill,'birthEvents':events,'candidateV83Admissions':rr.get('v83Admissions',[]),'transitionEvents':rr.get('transitionEvents',[]),'allocationEvents':rr.get('allocationV2Events',[]),'boundary':['baseline already contains allocation-aware direct recoverability','single added module: confirmed V83 Expand fill -> deferred Repair responsibility birth','birth only at later receipt','no direct Repair submit injection','ResponsibilityTransition/AllocationLedgerV2/RepairExecutionRouter frozen','pExpand/qty/price/delay frozen','winner post-hoc only','realistic HFT only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':bs,'candidate':cs,'routerProgress':router_progress,'actualRepairFill':actual_repair_fill,'events':events[:12],'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
