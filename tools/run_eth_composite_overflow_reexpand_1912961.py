from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,os,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9;MID=1912961;MARKET_DURATION_MS=300000

def sib(name,file):
    p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
prev=sib('recursive_reexpand_prev','run_eth_recursive_composite_recoverability_reexpand_1912961.py')
v90d=prev.v90d;v90=prev.v90;v80=prev.v80;v38=prev.v38;v1=prev.v1

class CompositeOverflowReexpand(prev.RecursiveRecoverabilityReexpand):
    def __init__(self,*a,**kw):
        self.corSubmits=0;self.corKeys=set();self.corEvents=[]
        super().__init__(*a,**kw)
    def _phase2(self,t):
        start=int(self.capEnd)-MARKET_DURATION_MS
        return max(0.0,min(1.0,(int(t)-start)/float(MARKET_DURATION_MS)))
    def _repair_objective_id(self):
        for name in ('repairLaneObjective','activeObjective'):
            x=getattr(self,name,None)
            if isinstance(x,dict) and str(x.get('role') or 'REPAIR')=='REPAIR' and x.get('id') is not None:return x.get('id')
        return None
    def _score_state(self,t,after_kind):
        # Before the confirmed V90D partial fill, preserve V90D/V83 behavior exactly.
        if self._confirmed_v90d_fill()<=EPS:
            return super()._score_state(t,after_kind)
        # After partial Repair, this coordinator owns only the re-Expand materialization seam.
        self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
        if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return None
        f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
        qv=v1.quotes(self.book)
        if not qv:return None
        self._ownership_if_needed(t,pE,qv)
        row={'t':int(t),'normalizedPhase':self._phase2(t),'secondsLeftDiagnosticOnly':(int(self.capEnd)-int(t))/1000.0,'parentId':int(self.repairParent.get('id')),'pExpand':pE,'submit':False,'reason':'MODEL_REPAIR'}
        self.v83AdmissionChecks+=1
        if pE<.5:
            row['reason']='OPPORTUNITY_BELOW_FROZEN_V44_THRESHOLD';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        if int(self.capEnd)-int(t)<=180000:
            row['reason']='OUR_LATE_NEW_EXPOSURE_SAFETY_FENCE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
        if self.v70gGenerationAuthorized:
            self.v70gDuplicateObjectiveBlocks+=1;row['reason']='GENERATION_ALREADY_OWNS_EXPAND';row['carrier']=self.v70gGenerationCarrier;self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        if self._expand_occupied():
            row['reason']='GLOBAL_EXPAND_OCCUPIED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        if self.corSubmits>=1:
            row['reason']='DEVELOPMENT_ONE_COMPOSITE_REEXPAND_CAP';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        th=getattr(self,'thesis',None);side=th.get('side') if th else None
        if side not in ('UP','DOWN'):
            row['reason']='NO_RECOVERABLE_THESIS';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        rec=self._v75_recoverability(t,side,qv);row.update({'side':side,'recoverability':rec})
        if not bool(rec.get('recoverable')):
            row['reason']='WHOLE_PORTFOLIO_UNRECOVERABLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        ai=self.auth_inv();weak='UP' if float(ai['UP'])<float(ai['DOWN'])-EPS else 'DOWN' if float(ai['DOWN'])<float(ai['UP'])-EPS else None
        pid=int(self.repairParent.get('id'));parent_side=self.repairParent.get('side')
        if weak is None or side!=weak or side!=parent_side:
            row.update({'reason':'COMPOSITE_OVERFLOW_SIDE_NOT_CURRENT_REPAIR_SIDE','weakSide':weak,'parentSide':parent_side});self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        px=float(qv[side]['bid']);qty=1.0/px if px>EPS else math.inf;gap=abs(float(ai['UP'])-float(ai['DOWN']))
        if not math.isfinite(qty) or qty<=EPS or qty>12.+EPS:
            row['reason']='VENUE_MIN_INFEASIBLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        if qty<=gap+EPS:
            row.update({'reason':'NO_OVERFLOW_REEXPAND_CAPACITY','managerDebt':gap,'physicalQty':qty});self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        # Crucial semantic correction: physical carrier remains REPAIR. Only confirmed overflow is EXPAND.
        roid=self._repair_objective_id();n0=self.n
        self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=roid;self._pendingParentId=pid;self._pendingLane='COMPOSITE_REPAIR_WITH_EXPAND_OVERFLOW'
        ok=self.submit(t,side,px,qty)
        self.packageRepairEval+=1
        if not ok:
            row['reason']='SUBMIT_FAILED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
        key=f'{side}_{n0}';self.packageRepairAccept+=1;self.v84CompositeSubmits+=1;self.corSubmits+=1;self.corKeys.add(key)
        self.v84Composite[key]={'key':key,'side':side,'parentId':pid,'price':px,'submittedQty':qty,'gapAtSubmit':gap,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'COMPOSITE_REPAIR_WITH_EXPAND_OVERFLOW'}
        self.postPartialExpandKeys.add(key);self.postPartialExpandSide=side
        row.update({'submit':True,'reason':'COMPOSITE_REPAIR_WITH_EXPAND_OVERFLOW_ADMISSION','key':key,'physicalRole':'REPAIR','intendedOverflowRole':'EXPAND','repairObjectiveId':roid,'price':px,'physicalQty':qty,'managerDebt':gap,'prospectiveOverflowQty':qty-gap})
        self.v83AdmissionAllows+=1;self.v83Admissions.append(dict(row));self.corEvents.append(dict(row))
        return True
    def run_composite(self,models,winner):
        r=self.run_candidate(models,winner)
        states=[]
        for key in sorted(self.corKeys):
            m=dict(self.v84Composite.get(key,{}));states.append(m)
        pay=[]
        for e in getattr(self,'v84Events',[]) or []:
            if e.get('event')=='OVERFLOW_REPAIR_PAYMENT' and e.get('compositeKey') in self.corKeys and float(e.get('paid') or 0)>EPS:
                z=dict(e);z['normalizedPhase']=self._phase2(int(e['t']));z['secondsLeftDiagnosticOnly']=(int(self.capEnd)-int(e['t']))/1000.0;pay.append(z)
        r.update({'compositeOverflowReexpandSubmits':self.corSubmits,'compositeOverflowReexpandKeys':sorted(self.corKeys),'compositeOverflowReexpandEvents':self.corEvents[:80],'compositeOverflowReexpandStates':states,'compositeOverflowLaterRepairPayments':pay})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=MID:raise ValueError(a.market_id)
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='eth_composite_overflow_reexpand_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'COMPOSITE_OVERFLOW_REEXPAND','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'COMPOSITE_OVERFLOW_REEXPAND_START','market':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        c=CompositeOverflowReexpand(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        try:rr=c.run_composite(models,cr['winner'])
        finally:c.close()
        safety=v90.safety_summary(rr);cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        states=rr.get('compositeOverflowReexpandStates') or [];filled=sum(float(x.get('fillSeen') or 0) for x in states);repair=sum(float(x.get('repairAllocated') or 0) for x in states);overflow=sum(float(x.get('overflowAllocated') or 0) for x in states);paid=sum(float(x.get('paid') or 0) for x in rr.get('compositeOverflowLaterRepairPayments') or [])
        out={'version':'COMPOSITE_OVERFLOW_REEXPAND_1912961_CANDIDATE','date':'2026-09-04','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],
             'candidate':{'fills':rr.get('actualFillEvents'),'pnlDiagnosticOnly':rr.get('pnlDiagnosticOnly'),'terminalFloor':rr.get('floor'),'worstObservedFloor':v90.actual_floor_min(rr),'shareSettlements':rr.get('v80ShareRepairSettlements'),'rounds':rr.get('v70dSemanticRounds'),'v90dFillQty':rr.get('v90dFillQty'),'v90dResidualDebt':rr.get('v90dTerminalAbsGap'),'recursiveRecoverabilityPasses':rr.get('recursiveRecoverabilityPasses'),'compositeSubmits':rr.get('compositeOverflowReexpandSubmits'),'compositeFillQty':filled,'repairAllocatedQty':repair,'overflowExpandQty':overflow,'laterOverflowRepairPaidQty':paid},
             'compositeStates':states,'compositeEvents':rr.get('compositeOverflowReexpandEvents'),'laterRepairPayments':rr.get('compositeOverflowLaterRepairPayments'),'v83Admissions':rr.get('v83Admissions',[])[:240],
             'safety':safety,'allocationConservation':cons,
             'timeSemantics':{'managerClock':'event/progress + normalized phase','secondsLeft':'diagnostic only','our180Fence':'safety only; not Target lifecycle timing'},
             'boundary':['single consumed development market','one composite-overflow re-Expand carrier maximum','physical role REPAIR; overflow role EXPAND only after confirmed fill','recursive recoverability policy only authorizes intent','pExpand 0.50 frozen','V90D tranche frozen','AllocationLedger V2 frozen','<=180s OUR speculative-overflow safety fence frozen','no Target runtime input','realistic HFT only','no dream fill','no 8781']}
        op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'candidate':out['candidate'],'safety':safety,'allocationConservation':cons,'states':states,'laterRepairPayments':out['laterRepairPayments']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
