from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9; MID=1912961

def sib(name,file):
    p=Path(__file__).with_name(file); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

v89d=sib('eth_v89d_for_v90a','run_eth_repair_v89d_two_active_composite_handoffs.py')
v89c=v89d.v89c; v89=v89c.v89; v84=v89.v84; v80=v89.v80; v38=v89.v38; v1=v89.v1
V84B=v84.V84BCompositeRepair

class V90ABoundedRelay(v89d.V89D):
    def __init__(self,*a,**kw):
        self.v90BoundedSubmits=0; self.v90BoundedParents=set(); self.v90Events=[]
        super().__init__(*a,**kw)

    def _submit_authorized(self,t,qv,z,roles_this_tick):
        if z is None:
            return super()._submit_authorized(t,qv,z,roles_this_tick)
        side,qty,oldp,role,oid=z
        rp=self.repairParent
        pid=int(rp.get('id')) if rp else None
        born=int(rp.get('bornAt') or -1) if rp else -1
        # Only replace V89B raw-precap sizing for overflow-born parents whose
        # actual manager debt is below one standalone legal passive slice.
        if role=='REPAIR' and rp is not None and born in self.v89OverflowBirthClocks and pid not in self.v89RecursiveParents:
            ai=self.auth_inv(); gap=abs(float(ai['UP'])-float(ai['DOWN']))
            p=float(qv[side]['bid']); legal=(1.0/p) if p>EPS else math.inf
            seconds_left=(int(self.capEnd)-int(t))/1000.0
            if int(self.capEnd)-int(t)<=180000:
                self.v90Events.append({'t':int(t),'event':'V90_LATE_OVERFLOW_BLOCK','parentId':pid,'side':side,'managerDebt':gap,'price':p,'venueMinQty':legal,'secondsLeft':seconds_left})
                # Do not allow the inherited V89B raw-precap composite branch.
                return V84B._submit_authorized(self,t,qv,z,roles_this_tick)
            if math.isfinite(legal) and legal>gap+EPS and legal<=12.0+EPS and 0<p<1:
                floor_before,u,d,cost=self._raw_floor()
                hu=float(u)+(legal if side=='UP' else 0.0)
                hd=float(d)+(legal if side=='DOWN' else 0.0)
                hc=float(cost)+legal*p
                hyp_floor=min(hu,hd)-hc
                self._pendingAuthorizedRole='REPAIR'; self._pendingAuthorizedObjectiveId=oid; self._pendingParentId=pid; self._pendingLane='V90_BOUNDED_MIN_LEGAL'
                n0=self.n; ok=self.submit(t,side,p,legal)
                if ok:
                    key=f'{side}_{n0}'; roles_this_tick.add('REPAIR')
                    self.v89RecursiveParents.add(pid); self.v89RecursiveSubmits+=1
                    self.v90BoundedParents.add(pid); self.v90BoundedSubmits+=1
                    self.packageRepairEval+=1; self.packageRepairAccept+=1; self.v84CompositeSubmits+=1
                    self.v84Composite[key]={'key':key,'side':side,'parentId':pid,'price':p,'submittedQty':legal,'gapAtSubmit':gap,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'V90_BOUNDED_MIN_LEGAL'}
                    ev={'t':int(t),'event':'V90_BOUNDED_MIN_LEGAL_SUBMIT','parentId':pid,'key':key,'side':side,'price':p,'managerDebt':gap,'physicalQty':legal,'overflowBudgetIfFull':legal-gap,'floorBefore':float(floor_before),'hypFloorAfterFullCarrier':float(hyp_floor),'hypFloorDelta':float(hyp_floor-floor_before),'secondsLeft':seconds_left}
                    self.v90Events.append(ev); self.v89Events.append(dict(ev)); return True
                self.v90Events.append({'t':int(t),'event':'V90_BOUNDED_SUBMIT_REJECTED','parentId':pid,'side':side,'price':p,'managerDebt':gap,'physicalQty':legal})
                return False
            if math.isfinite(legal) and gap+EPS>=legal:
                self.v90Events.append({'t':int(t),'event':'V90_STANDALONE_REPAIR_LEGAL_NO_COMPOSITE','parentId':pid,'side':side,'price':p,'managerDebt':gap,'venueMinQty':legal,'secondsLeft':seconds_left})
                # Explicitly bypass V89B raw-precap overshoot when the debt can
                # already materialize as an ordinary legal Repair.
                return V84B._submit_authorized(self,t,qv,z,roles_this_tick)
        return super()._submit_authorized(t,qv,z,roles_this_tick)

    def run_v90a(self,models,winner):
        r=self.run_v89c(models,winner)
        # run_v89c is inherited through V89D and still includes the two-active
        # handoff behavior. Derive recursive fill total including V90 lane.
        rf=0.0
        for m in self.v84Composite.values():
            if m.get('lane') in ('V89_OVERFLOW_PARENT_PRECAP','V90_BOUNDED_MIN_LEGAL'):
                rf+=float(m.get('fillSeen') or 0.0)
        self.v89RecursiveFillQty=rf
        r.update({'v90BoundedSubmits':self.v90BoundedSubmits,'v90BoundedParents':sorted(self.v90BoundedParents),'v90Events':self.v90Events[:240],'v89RecursiveFillQty':rf})
        return r

def actual_floor_min(rr):
    vals=[]
    def add(x):
        try:
            if x is not None and math.isfinite(float(x)): vals.append(float(x))
        except Exception: pass
    add(rr.get('floor'))
    for p in rr.get('v34ParentLedger',[]) or []:
        add(p.get('economicFloorAtShareSettlement')); add(p.get('armFloor'))
    for e in rr.get('v36ActiveEvents',[]) or []:
        add(e.get('floorBeforeObserved')); add(e.get('floorAfterObserved')); add(e.get('floor'))
    for e in rr.get('v80ManagementEvents',[]) or []: add(e.get('floor'))
    return min(vals) if vals else None

def overflow_payment_count(rr):
    return sum(1 for e in rr.get('v84Events',[]) or [] if e.get('event')=='OVERFLOW_REPAIR_PAYMENT' and float(e.get('paid') or 0)>EPS)

def safety_summary(rr):
    legacy=int(rr.get('overOwnedSubmitViolations') or 0); comps=int(rr.get('v84CompositeSubmits') or 0); unexpl=max(0,legacy-comps)
    other={
        'truthMismatch':float(rr.get('authorizedSubmitWithTruthRoleMismatch') or 0.0),
        'responsibilityOverfill':float(rr.get('v51ResponsibilityOverfill') or 0.0),
        'repairToExpandFirstFillDrift':float(rr.get('repairToExpandAtFirstFill') or 0.0),
        'preBirthLeak':float(rr.get('v70dPreBirthPaymentLeak') or 0.0)+float(rr.get('v84PreBirthPaymentLeak') or 0.0),
        'duplicateDebt':float(rr.get('v70dDuplicateGenerationDebt') or 0.0)+float(rr.get('v84DuplicateDebt') or 0.0),
        'sharedOverfill':float(rr.get('v36SharedRealizedOverfill') or 0.0),
        'unexplainedOverOwned':float(unexpl),
    }
    return other

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    if a.market_id!=MID: raise ValueError(a.market_id)
    out_path=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output)
    out_path.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='eth_v90a_')); stop=threading.Event()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'V90A_BOUNDED_RECURSIVE_RELAY','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'V90A_START','market':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape=tmp/'tapes'/f'{MID}.json.xz'
        def mk(cls): return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        b=mk(v89d.V89D)
        try: br=b.run_v89c(models,cr['winner'])
        finally: b.close()
        c=mk(V90ABoundedRelay)
        try: rr=c.run_v90a(models,cr['winner'])
        finally: c.close()
        safety=safety_summary(rr); safety_zero=all(v<=EPS for v in safety.values())
        conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        repair_bound=True
        for m in (rr.get('v84CompositeState') or {}).values():
            if float(m.get('repairAllocated') or 0)>float(m.get('gapAtSubmit') or 0)+1e-7: repair_bound=False
        base_min=actual_floor_min(br); cand_min=actual_floor_min(rr)
        payments=overflow_payment_count(rr)
        gates={
            'boundedBranchExercised':int(rr.get('v90BoundedSubmits') or 0)>0,
            'atLeastTwoOverflowRepairPayments':payments>=2,
            'repeatedRelayMateriallyExercised':int(rr.get('actualFillEvents') or 0)>=4 and int(rr.get('v80ShareRepairSettlements') or 0)>=3,
            'physicalAllocationConservation':conservation,
            'repairAllocationNeverExceedsManagerDebt':repair_bound,
            'safetyZero':safety_zero,
            'noLateOverflowViolation':not any(e.get('event')=='V90_LATE_OVERFLOW_SUBMIT' for e in rr.get('v90Events',[]) or []),
            'worstObservedFloorImproves':cand_min is not None and base_min is not None and cand_min>base_min+EPS,
            'terminalFloorNonWorse':float(rr.get('floor') or 0)>=float(br.get('floor') or 0)-EPS,
            'terminalPnlPositive':float(rr.get('pnlDiagnosticOnly') or 0)>EPS,
        }
        decision='KEEP_V90A_BOUNDED_RECURSIVE_RELAY_PORT_TO_CURRENT_V83' if all(gates.values()) else 'DIAGNOSE_V90A_BOUNDED_RECURSIVE_RELAY'
        out={
            'version':'ETH_REPAIR_V90A_BOUNDED_RECURSIVE_COMPOSITE_RELAY_1912961',
            'date':'2026-09-04','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,
            'baselineV89D':{'fills':br.get('actualFillEvents'),'shareSettlements':br.get('v80ShareRepairSettlements'),'repairParentBirths':br.get('repairParentBirths'),'pnlDiagnosticOnly':br.get('pnlDiagnosticOnly'),'terminalFloor':br.get('floor'),'worstObservedFloor':base_min,'overflowPaid':br.get('v84OverflowPaidQty'),'recursiveSubmits':br.get('v89RecursiveSubmits')},
            'candidateV90A':{'fills':rr.get('actualFillEvents'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'repairParentBirths':rr.get('repairParentBirths'),'pnlDiagnosticOnly':rr.get('pnlDiagnosticOnly'),'terminalFloor':rr.get('floor'),'worstObservedFloor':cand_min,'overflowPaid':rr.get('v84OverflowPaidQty'),'overflowRemaining':rr.get('v84OverflowRemainingQty'),'recursiveSubmits':rr.get('v89RecursiveSubmits'),'recursiveFillQty':rr.get('v89RecursiveFillQty'),'boundedSubmits':rr.get('v90BoundedSubmits'),'activeCompositeSubmits':rr.get('v89cActiveCompositeSubmits')},
            'overflowRepairPayments':payments,'safety':safety,'v90Events':rr.get('v90Events',[]),'relayEvents':rr.get('v84Events',[])[:320],'parentLedger':rr.get('v34ParentLedger',[]),
            'boundary':['single-market realistic HFT','only recursive passive overflow-born carrier sizing changed from raw-precap to exact venue-min when debt<legal min','V89D Active handoff frozen','Repair-first overflow-second','no numeric tuning','no Target runtime input','<=180s no new overflow','no dream fill','no 8781']
        }
        out_path.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV89D'],'candidate':out['candidateV90A'],'safety':safety,'events':out['v90Events'][:30]},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
