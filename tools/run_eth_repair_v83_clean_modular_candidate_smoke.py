from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9
FIXED=[1823894,1824747,1823769,1827223,1827903]

def sib(name,file):
    p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v80=sib('eth_v80_for_v83','run_eth_repair_v80_modular_management_kernel.py')
v52=sib('eth_v52_for_v83','run_eth_repair_v52_generation_payment_epoch_multi_active.py')
v38=v80.v38;v1=v80.v1

class V83CleanModularCandidate(v80.V80ModularManagementKernel):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.v83AdmissionChecks=0;self.v83AdmissionAllows=0;self.v83AdmissionBlocks=0
        self.v83Admissions=[];self.v83DisabledEarlyReservations=0

    # V38 fixed venue-min incremental Repair sizing is not restored.
    def _repair_payoff_budget(self,p):
        return v38.v36.V36EventConfirmedActive._repair_payoff_budget(self,p)

    # V70F early Repair-submit authority is explicitly disabled.
    def _reserve_expand_at_repair_submit(self,t,repair_key,repair_entry,pE):
        self.v83DisabledEarlyReservations+=1
        if hasattr(self,'v70fClockRows'):
            self.v70fClockRows.append({'t':int(t),'repairKey':repair_key,'pExpand':float(pE) if pE is not None else None,'eligible':False,'submit':False,'reason':'V83_V70F_EARLY_AUTHORITY_DISABLED'})
        return None

    # Keep V49/V50/V51 first-generation active Repair authority. V52 remains accounting-only until repeat-active coverage exists.
    def _maybe_hard_active(self,t):
        # Preserve the native V80/V52->V51->V50->V49->V36 MRO, but hide V52 epoch context for this decision so unvalidated repeat-active authority cannot fire.
        saved=getattr(self,'v52EpochCtx',None)
        if saved is None:
            return super()._maybe_hard_active(t)
        self.v52EpochCtx={}
        try:
            return super()._maybe_hard_active(t)
        finally:
            self.v52EpochCtx=saved

    def _ownership_if_needed(self,t,pE,qv):
        if getattr(self,'thesis',None) is not None:return
        side=self._signal_side(qv);rec=self._v75_recoverability(t,side,qv)
        ctx=v80.OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=False,p_expand=float(pE),signal_side=side,recoverable=bool(rec.get('recoverable')))
        dec=self.policyProfile.ownership.evaluate(ctx)
        row={**rec,'event':'V83_OWNERSHIP_CHECK','pExpand':float(pE),'signalSide':side,'decision':dec.reason,'createThesis':bool(dec.create_thesis)}
        self.v75Checks+=1
        if dec.create_thesis:
            self.thesis={'id':self.nextThesisId,'side':dec.side,'bornAt':int(t),'materialized':False,'recoveries':0,'opens':0,'birthKind':'V83_MODULAR_OWNERSHIP'}
            self.nextThesisId+=1;self.v75Births+=1;self.v75Recoverable+=1;row['event']='V83_THESIS_BIRTH';row['thesisId']=self.thesis['id']
        self.v75Events.append(dict(row));self.v80OwnershipEvents.append(dict(row))

    def _score_state(self,t,after_kind):
        # V83 owns the Expand admission seam; do not call legacy V44/V65/V70F scoring authority.
        self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
        if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return None
        f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
        qv=v1.quotes(self.book)
        if not qv:return None
        self._ownership_if_needed(t,pE,qv)
        row={'t':int(t),'parentId':int(self.repairParent.get('id')),'pExpand':pE,'submit':False,'reason':'MODEL_REPAIR'}
        self.v83AdmissionChecks+=1
        if pE<.5:
            row['reason']='OPPORTUNITY_BELOW_FROZEN_V44_THRESHOLD';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
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
        row.update({'submit':True,'reason':'V83_ECONOMIC_ADMISSION','key':key,'objectiveId':oid,'price':px,'qty':qty})
        self.v44Decisions.append(dict(row));self.v83AdmissionAllows+=1;self.v83Admissions.append(dict(row))
        self._refresh_carrier_ledger_no_v70d()
        if key in self.carrierLedger:
            self.v70gInheritedExpandBinds+=1;self._bind_existing_expand(t,key,'V83_ECONOMIC_ADMISSION')
        return True

    def run_exam_v83(self,models,winner):
        r=super().run_exam_v80(models,winner)
        r.update({'v83AdmissionChecks':self.v83AdmissionChecks,'v83AdmissionAllows':self.v83AdmissionAllows,'v83AdmissionBlocks':self.v83AdmissionBlocks,'v83Admissions':self.v83Admissions[:160],'v83DisabledEarlyReservations':self.v83DisabledEarlyReservations})
        return r

def safety(r):
    return {
      'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0.0),
      'overOwned':float(r.get('overOwnedSubmitViolations') or 0.0),
      'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0.0),
      'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0.0),
      'preBirthPaymentLeak':float(r.get('v70dPreBirthPaymentLeak') or 0.0),
      'duplicateGenerationDebt':float(r.get('v70dDuplicateGenerationDebt') or 0.0),
    }

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if mids!=FIXED:raise ValueError(f'fixed cohort mismatch {mids}')
    tmp=Path(tempfile.mkdtemp(prefix='eth_v83_clean_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'V83_CLEAN_MODULAR','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V83_CLEAN_MODULAR_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for mid in mids:
            cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=v80.V80ModularManagementKernel(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try:br=b.run_exam_v80(models,cr['winner'])
            finally:b.close()
            c=V83CleanModularCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try:rr=c.run_exam_v83(models,cr['winner'])
            finally:c.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baselineV80':br,'candidateV83':rr})
            print(json.dumps({'marketId':mid,'floor':[br.get('floor'),rr.get('floor')],'pnl':[br.get('pnlDiagnosticOnly'),rr.get('pnlDiagnosticOnly')],'v36Fill':rr.get('v36ActiveFillQty'),'genActivePaid':rr.get('v49GenerationActivePaidQty'),'v64Fill':rr.get('v64FillQty'),'rounds':[br.get('v70dSemanticRounds'),rr.get('v70dSemanticRounds')],'v83Admission':[rr.get('v83AdmissionAllows'),rr.get('v83AdmissionBlocks')],'v70fEarlyDisabled':rr.get('v83DisabledEarlyReservations')},ensure_ascii=False),flush=True)
        ss={k:sum(safety(x['candidateV83'])[k] for x in rows) for k in safety(rows[0]['candidateV83'])}
        maxresp=max(int(x['candidateV83'].get('v70gMaxResponsibilitiesPerGeneration') or 0) for x in rows)
        bfloor=sum(float(x['baselineV80'].get('floor') or 0.0) for x in rows);cfloor=sum(float(x['candidateV83'].get('floor') or 0.0) for x in rows)
        nonw=sum(float(x['candidateV83'].get('floor') or 0.0)+EPS>=float(x['baselineV80'].get('floor') or 0.0) for x in rows)
        allows=sum(int(x['candidateV83'].get('v83AdmissionAllows') or 0) for x in rows);blocks=sum(int(x['candidateV83'].get('v83AdmissionBlocks') or 0) for x in rows)
        v36fill=sum(float(x['candidateV83'].get('v36ActiveFillQty') or 0) for x in rows);genpaid=sum(float(x['candidateV83'].get('v49GenerationActivePaidQty') or 0) for x in rows);v64fill=sum(float(x['candidateV83'].get('v64FillQty') or 0) for x in rows)
        early=sum(int(x['candidateV83'].get('v70dParallelReservations') or 0) for x in rows)
        # v70dParallelReservations should remain zero because V83 binds only inherited passive V83 carriers, not V70F/V70D early active reservations.
        gates={'marketsComplete':len(rows)==len(FIXED),'zeroTruthMismatch':ss['truthMismatch']==0,'zeroOverOwned':ss['overOwned']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroRepairDrift':ss['repairDrift']==0,'zeroPreBirthPaymentLeak':ss['preBirthPaymentLeak']<=EPS,'zeroDuplicateGenerationDebt':ss['duplicateGenerationDebt']<=EPS,'oneResponsibilityPerGeneration':maxresp<=1,'economicAdmissionExercised':allows>0,'v70fEarlyAuthorityDisabled':early==0,'noObviousAggregateFloorRegression':cfloor+EPS>=bfloor}
        plumbing={'v36RepairExecutionFillQty':v36fill,'generationActiveRepairPaidQty':genpaid,'activeExpandFallbackFillQty':v64fill,'economicAdmissionAllows':allows,'economicAdmissionBlocks':blocks}
        decision='KEEP_V83_CLEAN_MODULAR_FOR_FRESH_HFT' if all(gates.values()) else 'DIAGNOSE_V83_MODULE_SEAM_BEFORE_FRESH_HFT'
        out={'version':'ETH_REPAIR_V83_CLEAN_MODULAR_CANDIDATE_SMOKE','date':'2026-09-03','researchOnly':True,'fixedCohort':FIXED,'decision':decision,'aggregate':{'baselineFloorSum':bfloor,'candidateFloorSum':cfloor,'floorDelta':cfloor-bfloor,'perMarketFloorNonWorse':nonw,'markets':len(rows)},'plumbing':plumbing,'safety':ss,'gates':gates,'rows':rows,'boundary':['V80 economic authority','V36 active Repair retained','V49/V50/V51 retained; V52 repeat-active disabled but payment epoch accounting inherited','V48 ledger retained','V64/V65/V70G retained under V76 active handoff','V44 signal only; V83 economic Maker admission replaces legacy immediate submit','V38 fixed incremental sizing disabled','V70F early Repair-submit reservation disabled','no tuning','realistic HFT only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':out['aggregate'],'plumbing':plumbing,'gates':gates,'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
