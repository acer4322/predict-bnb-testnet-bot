from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1916869

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v2=sibling('scheduler_frontier_v2_for_guard',Path(__file__).resolve().with_name('run_eth_continuous_scheduler_authoritative_repair_frontier_v2_1916869.py'))
base=v2.base;front=v2.front;v38=v2.v38;v80=v2.v80;compact=v2.compact
try:
    from tools.eth_repair_modular import economic_continuous_scheduler_only_v1_profile
    from tools.eth_repair_modular.ownership_transition_guard import ProspectiveOwnershipTransitionGuardV1,ProspectiveOwnershipTransitionContext
except ImportError:
    from eth_repair_modular import economic_continuous_scheduler_only_v1_profile
    from eth_repair_modular.ownership_transition_guard import ProspectiveOwnershipTransitionGuardV1,ProspectiveOwnershipTransitionContext

class ProspectiveOwnershipTransitionGuardCandidate(v2.SchedulerAuthoritativeRepairFrontierV2):
    def __init__(self,*a,**kw):
        self.prospectiveOwnershipTransitionGuard=ProspectiveOwnershipTransitionGuardV1();self.prospectiveTransitionChecks=0;self.prospectiveTransitionBlocks=0;self.prospectiveTransitionEvents=[]
        super().__init__(*a,**kw)
    def _ownership_if_needed(self,t,pE,qv):
        if getattr(self,'thesis',None) is not None:return
        side=self._signal_side(qv);rec=self._v75_recoverability(t,side,qv)
        ctx=v80.OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=False,p_expand=float(pE),signal_side=side,recoverable=bool(rec.get('recoverable')))
        dec=self.policyProfile.ownership.evaluate(ctx)
        row={**rec,'event':'V83_OWNERSHIP_CHECK','pExpand':float(pE),'signalSide':side,'decision':dec.reason,'createThesis':bool(dec.create_thesis)}
        self.v75Checks+=1
        allow_birth=bool(dec.create_thesis)
        if dec.create_thesis:
            debt,debt_rows=self._live_repair_debt_by_side()
            td=self.transitionPolicy.evaluate(front.trans.ResponsibilityTransitionContext(dec.side,debt['UP'],debt['DOWN']))
            gd=self.prospectiveOwnershipTransitionGuard.evaluate(ProspectiveOwnershipTransitionContext(dec.side,True,bool(td.allow_expand_ownership),td.bind_role,td.reason))
            self.prospectiveTransitionChecks+=1
            allow_birth=bool(gd.allow_thesis_birth)
            pev={'t':int(t),'event':'PROSPECTIVE_OWNERSHIP_TRANSITION_CHECK','candidateSide':dec.side,'repairDebtBySide':dict(debt),'debtRows':debt_rows,'transitionAllow':bool(td.allow_expand_ownership),'transitionBindRole':td.bind_role,'transitionReason':td.reason,'guardReason':gd.reason,'allowThesisBirth':allow_birth}
            if not allow_birth:
                self.prospectiveTransitionBlocks+=1;self.transitionBlocks+=1;pev['event']='PROSPECTIVE_THESIS_BIRTH_BLOCKED_REPAIR_FIRST'
            self.prospectiveTransitionEvents.append(dict(pev));self.transitionEvents.append(dict(pev))
            row.update({'prospectiveTransitionAllow':bool(td.allow_expand_ownership),'prospectiveTransitionReason':td.reason,'prospectiveGuardReason':gd.reason,'createThesis':allow_birth})
        if allow_birth:
            self.thesis={'id':self.nextThesisId,'side':dec.side,'bornAt':int(t),'materialized':False,'recoveries':0,'opens':0,'birthKind':'V83_MODULAR_OWNERSHIP'}
            self.nextThesisId+=1;self.v75Births+=1;self.v75Recoverable+=1;row['event']='V83_THESIS_BIRTH';row['thesisId']=self.thesis['id']
        self.v75Events.append(dict(row));self.v80OwnershipEvents.append(dict(row))
    def run_candidate_guard(self,models,winner):
        r=self.run_candidate_v2(models,winner);r.update({'prospectiveOwnershipTransitionGuard':self.prospectiveOwnershipTransitionGuard.name,'prospectiveTransitionChecks':self.prospectiveTransitionChecks,'prospectiveTransitionBlocks':self.prospectiveTransitionBlocks,'prospectiveTransitionEvents':self.prospectiveTransitionEvents[:260]});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='prospective_transition_guard_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'PROSPECTIVE_OWNERSHIP_TRANSITION_GUARD','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'PROSPECTIVE_OWNERSHIP_TRANSITION_GUARD_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=economic_continuous_scheduler_only_v1_profile())
        b=mk(v2.SchedulerAuthoritativeRepairFrontierV2)
        try:br=b.run_candidate_v2(models,cr['winner'])
        finally:b.close()
        c=mk(ProspectiveOwnershipTransitionGuardCandidate)
        try:rr=c.run_candidate_guard(models,cr['winner'])
        finally:c.close()
        bs=front.safety(br);ss=front.safety(rr);sm=rr.get('schedulerAdapter') or {};conflict_t=1788450016707
        conflict_rows=[x for x in (rr.get('v83Admissions') or []) if int(x.get('t') or -1)==conflict_t];conflict_submit=any(bool(x.get('submit')) for x in conflict_rows)
        pev=[x for x in (rr.get('prospectiveTransitionEvents') or []) if int(x.get('t') or -1)==conflict_t]
        later_legal=[x for x in (rr.get('v83Admissions') or []) if int(x.get('t') or 0)>conflict_t and bool(x.get('submit'))]
        gates={'guardModuleActive':rr.get('prospectiveOwnershipTransitionGuard')=='prospective_ownership_transition_guard_v1','prospectiveTransitionExercised':int(rr.get('prospectiveTransitionChecks') or 0)>0,'knownConflictGuardExercised':any(not bool(x.get('allowThesisBirth')) for x in pev),'knownConflictNoSubmit':not conflict_submit,'zeroCandidateTruthMismatch':float(ss['truthMismatch'])==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS,'schedulerStillExercised':int(sm.get('reevaluations') or 0)>0}
        decision='KEEP_PROSPECTIVE_OWNERSHIP_TRANSITION_GUARD_FOR_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_PROSPECTIVE_OWNERSHIP_TRANSITION_GUARD'
        out={'version':'ETH_CONTINUOUS_SCHEDULER_PROSPECTIVE_OWNERSHIP_TRANSITION_GUARD_V1_1916869','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'controlFrontierV2':compact(br),'candidateGuard':compact(rr),'controlSafety':bs,'candidateSafety':ss,'schedulerAdapter':sm,'prospectiveTransitionChecks':rr.get('prospectiveTransitionChecks'),'prospectiveTransitionBlocks':rr.get('prospectiveTransitionBlocks'),'knownConflictGuardEvents':pev,'knownConflictV83Rows':conflict_rows,'laterLegalSchedulerAdmissions':later_legal[:20],'prospectiveTransitionEvents':rr.get('prospectiveTransitionEvents',[])[:260],'candidateV83Admissions':(rr.get('v83Admissions') or [])[:300],'boundary':['single module replacement relative to Frontier V2: ProspectiveOwnershipTransitionGuard only','Ownership side selection unchanged','RepairFirst transition rule unchanged','authoritative Repair frontier frozen','continuous scheduler frozen','all accounting/admission/qty/price/execution modules frozen','realistic HFT only','winner/PnL post-hoc only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'control':out['controlFrontierV2'],'candidate':out['candidateGuard'],'controlSafety':bs,'candidateSafety':ss,'prospectiveChecks':out['prospectiveTransitionChecks'],'prospectiveBlocks':out['prospectiveTransitionBlocks'],'laterLegalAdmissions':len(later_legal),'scheduler':{k:v for k,v in sm.items() if k!='events'}},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
