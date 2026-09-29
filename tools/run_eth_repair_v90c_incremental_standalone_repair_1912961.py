from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9; MID=1912961

def sib(name,file):
    p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v90=sib('eth_v90a_for_v90c','run_eth_repair_v90a_bounded_recursive_composite_relay_1912961.py')
v80=v90.v80;v38=v90.v38;v1=v90.v1

class V90CIncrementalStandaloneRepair(v90.V90ABoundedRelay):
    def __init__(self,*a,**kw):
        self.v90cBudgetChecks=0;self.v90cBudgetAllows=0;self.v90cSubmitAttempts=0;self.v90cSubmitSuccess=0;self.v90cKeys=[];self.v90cEvents=[]
        super().__init__(*a,**kw)

    def _is_overflow_standalone_parent(self,p):
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict):return None
        born=int(rp.get('bornAt') or -1);side=rp.get('side')
        if born not in getattr(self,'v89OverflowBirthClocks',set()) or side not in ('UP','DOWN'):return None
        qv=v1.quotes(self.book)
        if not qv or side not in qv or qv[side].get('bid') is None:return None
        bid=float(qv[side]['bid']) if p is None else float(p)
        if not (EPS<bid<1.0-EPS):return None
        ai=self.auth_inv();opp='DOWN' if side=='UP' else 'UP'
        gap=max(0.0,float(ai[opp])-float(ai[side]))
        legal=1.0/bid
        if gap+EPS<legal:return None
        return rp,side,gap,bid,legal

    def _repair_payoff_budget(self,p):
        ctx=self._is_overflow_standalone_parent(p)
        if ctx is None:return super()._repair_payoff_budget(p)
        rp,side,gap,bid,legal=ctx
        rows=self.lane_unresolved('REPAIR');owned=0.0
        for _,_,rem in rows:owned+=float(rem)
        room=max(0.0,gap-owned)
        self.v90cBudgetChecks+=1
        floor,u,d,cost=self._raw_floor()
        qty=room
        if qty+EPS<legal or qty<=EPS:
            self.v90cEvents.append({'event':'V90C_INCREMENTAL_BUDGET_BLOCK','parentId':int(rp.get('id')),'side':side,'price':bid,'managerDebt':gap,'ownedRepair':owned,'room':room,'venueMinQty':legal,'floorBefore':floor,'reason':'ROOM_BELOW_LEGAL'})
            return {'qty':0.0,'deficit':max(0.0,-floor),'room':room,'gap':gap,'owned':owned,'reservedGain':0.0,'feasible':False,'v90c':True}
        hu=float(u)+(qty if side=='UP' else 0.0);hd=float(d)+(qty if side=='DOWN' else 0.0);hc=float(cost)+qty*bid
        hyp=min(hu,hd)-hc
        if hyp<=float(floor)+EPS:
            self.v90cEvents.append({'event':'V90C_INCREMENTAL_BUDGET_BLOCK','parentId':int(rp.get('id')),'side':side,'price':bid,'managerDebt':gap,'ownedRepair':owned,'room':room,'venueMinQty':legal,'floorBefore':floor,'hypFloorAfter':hyp,'reason':'NO_FLOOR_IMPROVEMENT'})
            return {'qty':0.0,'deficit':max(0.0,-floor),'room':room,'gap':gap,'owned':owned,'reservedGain':0.0,'feasible':False,'v90c':True}
        self.v90cBudgetAllows+=1
        self.v90cEvents.append({'event':'V90C_INCREMENTAL_BUDGET_ALLOW','parentId':int(rp.get('id')),'side':side,'price':bid,'managerDebt':gap,'ownedRepair':owned,'room':room,'venueMinQty':legal,'floorBefore':floor,'hypFloorAfterFullDebt':hyp,'hypFloorDelta':hyp-floor})
        return {'qty':qty,'need':qty,'legal':legal,'deficit':max(0.0,-floor),'room':room,'gap':gap,'owned':owned,'reservedGain':0.0,'feasible':True,'v90c':True,'hypFloorAfter':hyp}

    def _submit_authorized(self,t,qv,z,roles_this_tick):
        ctx=None
        if z is not None and str(z[3])=='REPAIR':ctx=self._is_overflow_standalone_parent(float(qv[z[0]]['bid']))
        n0=self.n
        if ctx is not None:self.v90cSubmitAttempts+=1
        ok=super()._submit_authorized(t,qv,z,roles_this_tick)
        if ctx is not None:
            rp,side,gap,bid,legal=ctx
            ev={'t':int(t),'event':'V90C_INCREMENTAL_SUBMIT_RESULT','parentId':int(rp.get('id')),'side':side,'price':bid,'managerDebt':gap,'venueMinQty':legal,'return':bool(ok)}
            if ok:
                key=f'{side}_{n0}';self.v90cSubmitSuccess+=1;self.v90cKeys.append(key);ev['key']=key
            self.v90cEvents.append(ev)
        return ok

    def run_v90c(self,models,winner):
        r=self.run_v90a(models,winner)
        fill=0.0;rows=[]
        for key in self.v90cKeys:
            e=self.carrierLedger.get(key,{})
            q=float(e.get('actualFilled') or 0.0);fill+=q
            rows.append({'key':key,'side':e.get('side'),'price':e.get('price'),'submittedQty':e.get('submittedQty'),'actualFilled':q,'objectiveRole':e.get('objectiveRole'),'parentId':e.get('parentId')})
        r.update({'v90cBudgetChecks':self.v90cBudgetChecks,'v90cBudgetAllows':self.v90cBudgetAllows,'v90cSubmitAttempts':self.v90cSubmitAttempts,'v90cSubmitSuccess':self.v90cSubmitSuccess,'v90cActualFillQty':fill,'v90cCarrierRows':rows,'v90cEvents':self.v90cEvents[:400]})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=MID:raise ValueError(a.market_id)
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='eth_v90c_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'V90C_INCREMENTAL_STANDALONE','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V90C_START','market':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        b=mk(v90.V90ABoundedRelay)
        try:br=b.run_v90a(models,cr['winner'])
        finally:b.close()
        c=mk(V90CIncrementalStandaloneRepair)
        try:rr=c.run_v90c(models,cr['winner'])
        finally:c.close()
        safety=v90.safety_summary(rr);safety_zero=all(float(v)<=EPS for v in safety.values())
        conservation=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        qty_bound=all(float(x.get('submittedQty') or 0)<=2.1399445867530975+1e-7 for x in rr.get('v90cCarrierRows',[]))
        no_new_composite=all(str(x.get('key')) not in (rr.get('v84CompositeState') or {}) for x in rr.get('v90cCarrierRows',[]))
        base_min=v90.actual_floor_min(br);cand_min=v90.actual_floor_min(rr)
        gates={
          'candidateBranchExercised':int(rr.get('v90cBudgetAllows') or 0)>0,
          'atLeastOneStandaloneRepairSubmit':int(rr.get('v90cSubmitSuccess') or 0)>0,
          'atLeastOneStandaloneRepairActualFill':float(rr.get('v90cActualFillQty') or 0)>EPS,
          'repairQtyNeverExceedsManagerDebt':qty_bound,
          'noNewCompositeOverflowFromThisBranch':no_new_composite,
          'physicalAllocationConservation':conservation,
          'safetyZero':safety_zero,
          'worstObservedFloorNonWorseThanV90A':cand_min is not None and base_min is not None and cand_min>=base_min-EPS,
          'terminalFloorStrictlyBetterThanV90A':float(rr.get('floor') or 0)>float(br.get('floor') or 0)+EPS,
          'terminalPnlPositive':float(rr.get('pnlDiagnosticOnly') or 0)>EPS,
          'fillsOrShareSettlementsIncrease':int(rr.get('actualFillEvents') or 0)>int(br.get('actualFillEvents') or 0) or int(rr.get('v80ShareRepairSettlements') or 0)>int(br.get('v80ShareRepairSettlements') or 0),
        }
        decision='KEEP_V90C_INCREMENTAL_STANDALONE_FOR_REPLICATION' if all(gates.values()) else 'DIAGNOSE_OR_REJECT_V90C_INCREMENTAL_STANDALONE'
        out={'version':'ETH_V90C_INCREMENTAL_STANDALONE_REPAIR_1912961','date':'2026-09-04','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baselineV90A':{'fills':br.get('actualFillEvents'),'shareSettlements':br.get('v80ShareRepairSettlements'),'pnl':br.get('pnlDiagnosticOnly'),'terminalFloor':br.get('floor'),'worstObservedFloor':base_min},'candidateV90C':{'fills':rr.get('actualFillEvents'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'pnl':rr.get('pnlDiagnosticOnly'),'terminalFloor':rr.get('floor'),'worstObservedFloor':cand_min,'budgetChecks':rr.get('v90cBudgetChecks'),'budgetAllows':rr.get('v90cBudgetAllows'),'submitAttempts':rr.get('v90cSubmitAttempts'),'submitSuccess':rr.get('v90cSubmitSuccess'),'actualFillQty':rr.get('v90cActualFillQty'),'carrierRows':rr.get('v90cCarrierRows')},'safety':safety,'events':rr.get('v90cEvents',[])[:400],'boundary':['single-market realistic HFT','only overflow-born standalone-legal Repair full-payoff feasibility replaced with incremental floor-improving responsibility payment','no composite overflow from V90C branch','all Expand/scheduler/ownership/price modules frozen','no tuning','no Target runtime input','<=180s fence inherited','no dream fill','no 8781']}
        op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV90A'],'candidate':out['candidateV90C'],'safety':safety},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
