from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
from collections import Counter
import numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1917324;THRESHOLD=0.45

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

batch=sibling('guarded_threshold045_batch_base',Path(__file__).resolve().with_name('run_eth_continuous_guarded_scheduler_modular_batch.py'))
guard=batch.guard;front=batch.front;v38=batch.v38;compact=batch.compact
v1=front.g.v1
try:
    from tools.eth_repair_modular import economic_continuous_guarded_scheduler_candidate_v1_profile
except ImportError:
    from eth_repair_modular import economic_continuous_guarded_scheduler_candidate_v1_profile

class Threshold045Candidate(batch.Candidate):
    opportunityThreshold=THRESHOLD
    def _score_state(self,t,after_kind):
        # Preserve current guarded scheduler stack; only lower the V83 admission threshold.
        self._refresh_carrier_ledger(int(t))
        debt,rows=self._live_repair_debt_by_side();th=getattr(self,'thesis',None);existing_side=th.get('side') if isinstance(th,dict) else None
        d=self.transitionPolicy.evaluate(front.trans.ResponsibilityTransitionContext(existing_side,debt['UP'],debt['DOWN']))
        self.transitionChecks+=1
        tev={'t':int(t),'event':'RESPONSIBILITY_TRANSITION_CHECK','afterKind':after_kind,'thesisSide':existing_side,'repairDebtBySide':debt,'debtRows':rows,'allowExpandOwnership':d.allow_expand_ownership,'bindRole':d.bind_role,'reason':d.reason,'liveRepairDebt':d.live_repair_debt}
        if after_kind=='REPAIR' and not d.allow_expand_ownership and d.bind_role=='REPAIR':
            self.transitionBlocks+=1;tev['event']='EXPAND_OWNERSHIP_SUSPENDED_REPAIR_FIRST';self.transitionEvents.append(tev);return None
        self.transitionEvents.append(tev)

        self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
        if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return None
        f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);qv=v1.quotes(self.book)
        if not qv:return None
        # Inherited method includes the prospective Ownership -> Transition guard.
        self._ownership_if_needed(t,pE,qv)
        row={'t':int(t),'parentId':int(self.repairParent.get('id')),'pExpand':pE,'submit':False,'reason':'MODEL_REPAIR','opportunityThreshold':self.opportunityThreshold}
        self.v83AdmissionChecks+=1
        if pE<self.opportunityThreshold:
            row['reason']='OPPORTUNITY_BELOW_DEV_THRESHOLD';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        if int(self.capEnd)-int(t)<=180000:
            row['reason']='LATE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
        if self.v70gGenerationAuthorized:
            self.v70gDuplicateObjectiveBlocks+=1;row['reason']='GENERATION_ALREADY_OWNS_EXPAND';row['carrier']=self.v70gGenerationCarrier;self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        if self._expand_occupied():
            row['reason']='GLOBAL_EXPAND_OCCUPIED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        th=getattr(self,'thesis',None);side=th.get('side') if th else None
        if side not in ('UP','DOWN'):
            row['reason']='NO_RECOVERABLE_THESIS';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        rec=self._v75_recoverability(t,side,qv);row.update({'side':side,'recoverability':rec})
        if not bool(rec.get('recoverable')):
            row['reason']='WHOLE_PORTFOLIO_UNRECOVERABLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        px=float(qv[side]['bid']);qty=1.0/px if px>EPS else math.inf
        if not math.isfinite(qty) or qty<=EPS or qty>12.+EPS:
            row['reason']='VENUE_MIN_INFEASIBLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        oid=self._new_objective('EXPAND',side)['id'];n0=self.n
        self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V83_ECONOMIC_PARALLEL_EXPAND'
        ok=self.submit(t,side,px,qty)
        if not ok:
            row['reason']='SUBMIT_FAILED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        key=f'{side}_{n0}';self.v44Keys.add(key);self.v44Submits+=1
        row.update({'submit':True,'reason':'V83_ECONOMIC_ADMISSION_DEV_THRESHOLD','key':key,'objectiveId':oid,'price':px,'qty':qty})
        self.v44Decisions.append(dict(row));self.v83AdmissionAllows+=1;self.v83Admissions.append(dict(row));self._refresh_carrier_ledger_no_v70d()
        if key in self.carrierLedger:
            self.v70gInheritedExpandBinds+=1;self._bind_existing_expand(t,key,'V83_ECONOMIC_ADMISSION_DEV_THRESHOLD')
        return True

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='guarded_threshold045_smoke1_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'GUARDED_THRESHOLD045_SMOKE1','ts':time.time(),'market':FIXED}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'GUARDED_THRESHOLD045_SMOKE1_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        c=batch.mk(Threshold045Candidate,tape,models,life,cap,tim,econ,price,sur,t44,t47,economic_continuous_guarded_scheduler_candidate_v1_profile())
        try:
            rr=c.run_candidate_guard(models,cr['winner'])
            full_admissions=list(getattr(c,'v83Admissions',[]))
        finally:c.close()
        ss=front.safety(rr);sm=rr.get('schedulerAdapter') or {};reasons=Counter(str(x.get('reason')) for x in full_admissions)
        cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        behavior=compact(rr)
        control={'fills':6,'submits':20,'repairExpandRepairRounds':2,'floor':-1.1995814307458144,'pnlDiagnosticOnly':1.4413861617578307,'v83Allows':1}
        floor_delta=float(behavior['floor'])-control['floor'];round_delta=int(behavior['repairExpandRepairRounds'])-control['repairExpandRepairRounds'];fill_delta=int(behavior['fills'])-control['fills']
        allsafe=all(float(v)<=EPS for v in ss.values()) and cons
        gates={'allSafetyZero':allsafe,'allocationConservation':cons,'roundsNonDecreasing':round_delta>=0,'floorWithinDevelopmentTolerance':floor_delta>=-0.25,'physicalActionSupport':int(behavior['fills'])>control['fills'] or int(behavior['repairExpandRepairRounds'])>control['repairExpandRepairRounds']}
        if not gates['allSafetyZero'] or not gates['roundsNonDecreasing'] or not gates['floorWithinDevelopmentTolerance']:decision='REJECT_THRESHOLD045_TIER0'
        elif gates['physicalActionSupport']:decision='KEEP_THRESHOLD045_FOR_ONE_REPLICATION_ONLY'
        else:decision='STOP_THRESHOLD_LINE_NO_PHYSICAL_SUPPORT'
        out={'version':'ETH_CONTINUOUS_GUARDED_SCHEDULER_THRESHOLD045_SMOKE1_1917324','date':'2026-09-04','researchOnly':True,'tier':'TIER_0_SINGLE_MARKET_SINGLE_VARIABLE','marketId':FIXED,'winnerPostHocOnly':cr['winner'],'threshold':THRESHOLD,'controlThreshold':0.50,'controlFrozenArtifact':control,'candidate':behavior,'delta':{'fills':fill_delta,'rounds':round_delta,'floor':floor_delta,'pnlDiagnosticOnly':float(behavior['pnlDiagnosticOnly'])-control['pnlDiagnosticOnly'],'v83Allows':int(behavior['v83Allows'])-control['v83Allows']},'safety':ss,'allocationConservation':cons,'gates':gates,'decision':decision,'v83ReasonCounts':dict(reasons),'scheduler':{k:v for k,v in sm.items() if k!='events'},'prospectiveTransitionChecks':rr.get('prospectiveTransitionChecks'),'prospectiveTransitionBlocks':rr.get('prospectiveTransitionBlocks'),'profile':rr.get('v80PolicyProfile'),'admissionSubmits':[x for x in full_admissions if x.get('submit')][:40],'boundary':['single market','single variable threshold 0.50->0.45','0.50 control reused from exact fresh3 artifact; not rerun','all current guarded scheduler modules frozen','realistic HFT only','winner/PnL post-hoc only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':behavior,'delta':out['delta'],'safety':ss,'gates':gates,'reasons':dict(reasons),'scheduler':out['scheduler']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
