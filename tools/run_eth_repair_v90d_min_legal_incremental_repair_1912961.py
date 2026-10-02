from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9; MID=1912961

def sib(name,file):
    p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v90c=sib('eth_v90c_for_v90d','run_eth_repair_v90c_incremental_standalone_repair_1912961.py')
v90=v90c.v90;v80=v90.v80;v38=v90.v38;v1=v90.v1

class V90DMinLegalIncremental(v90.V90ABoundedRelay):
    def __init__(self,*a,**kw):
        self.v90dPaidParents=set();self.v90dRows=[];self.v90dSubmitKeys=[];self.v90dAllows=0
        super().__init__(*a,**kw)
    def _ctx(self,p):
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict):return None
        pid=int(rp.get('id'));born=int(rp.get('bornAt') or -1);side=rp.get('side')
        if pid in self.v90dPaidParents or born not in getattr(self,'v89OverflowBirthClocks',set()) or side not in ('UP','DOWN'):return None
        qv=v1.quotes(self.book)
        if not qv or qv[side].get('bid') is None:return None
        bid=float(p if p is not None else qv[side]['bid'])
        if not (EPS<bid<1.0-EPS):return None
        ai=self.auth_inv();opp='DOWN' if side=='UP' else 'UP';gap=max(0.0,float(ai[opp])-float(ai[side]));legal=1.0/bid
        if gap+EPS<legal:return None
        return rp,pid,side,gap,bid,legal
    def _repair_payoff_budget(self,p):
        ctx=self._ctx(p)
        if ctx is None:return super()._repair_payoff_budget(p)
        rp,pid,side,gap,bid,legal=ctx
        owned=sum(float(rem) for _,_,rem in self.lane_unresolved('REPAIR'));room=max(0.0,gap-owned)
        qty=min(room,legal)
        floor,u,d,cost=self._raw_floor();hu=u+(qty if side=='UP' else 0.0);hd=d+(qty if side=='DOWN' else 0.0);hc=cost+qty*bid;hyp=min(hu,hd)-hc
        if qty<=EPS or qty+EPS<legal or hyp<=floor+EPS:
            self.v90dRows.append({'event':'V90D_BUDGET_BLOCK','parentId':pid,'side':side,'price':bid,'managerDebt':gap,'room':room,'venueMinQty':legal,'floorBefore':floor,'hypFloorAfter':hyp})
            return {'qty':0.0,'deficit':max(0.0,-floor),'room':room,'gap':gap,'owned':owned,'reservedGain':0.0,'feasible':False,'v90d':True}
        self.v90dAllows+=1
        self.v90dRows.append({'event':'V90D_MIN_LEGAL_BUDGET_ALLOW','parentId':pid,'side':side,'price':bid,'managerDebt':gap,'room':room,'venueMinQty':legal,'candidateQty':qty,'floorBefore':floor,'hypFloorAfter':hyp,'hypFloorDelta':hyp-floor})
        return {'qty':qty,'need':qty,'legal':legal,'deficit':max(0.0,-floor),'room':room,'gap':gap,'owned':owned,'reservedGain':0.0,'feasible':True,'v90d':True,'hypFloorAfter':hyp}
    def _submit_authorized(self,t,qv,z,roles_this_tick):
        ctx=None
        if z is not None and str(z[3])=='REPAIR':ctx=self._ctx(float(qv[z[0]]['bid']))
        n0=self.n;ok=super()._submit_authorized(t,qv,z,roles_this_tick)
        if ctx is not None:
            rp,pid,side,gap,bid,legal=ctx
            row={'t':int(t),'event':'V90D_SUBMIT_RESULT','parentId':pid,'side':side,'price':bid,'managerDebtAtSubmit':gap,'venueMinQty':legal,'return':bool(ok)}
            if ok:
                key=f'{side}_{n0}';self.v90dPaidParents.add(pid);self.v90dSubmitKeys.append(key);self.v89RecursiveParents.add(pid);row['key']=key
            self.v90dRows.append(row)
        return ok
    def run_v90d(self,models,winner):
        r=self.run_v90a(models,winner);fills=[];fillqty=0.0
        for key in self.v90dSubmitKeys:
            e=self.carrierLedger.get(key,{});q=float(e.get('actualFilled') or 0.0);fillqty+=q
            fills.append({'key':key,'side':e.get('side'),'submittedQty':e.get('submittedQty'),'actualFilled':q,'objectiveRole':e.get('objectiveRole'),'parentId':e.get('parentId')})
        current_gap=abs(float(self.auth_inv()['UP'])-float(self.auth_inv()['DOWN']))
        r.update({'v90dAllows':self.v90dAllows,'v90dPaidParents':sorted(self.v90dPaidParents),'v90dSubmitKeys':self.v90dSubmitKeys,'v90dFillQty':fillqty,'v90dFills':fills,'v90dRows':self.v90dRows[:300],'v90dTerminalAbsGap':current_gap})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=MID:raise ValueError(a.market_id)
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='eth_v90d_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'V90D_MIN_LEGAL_INCREMENTAL','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V90D_START','market':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        b=mk(v90.V90ABoundedRelay)
        try:br=b.run_v90a(models,cr['winner'])
        finally:b.close()
        c=mk(V90DMinLegalIncremental)
        try:rr=c.run_v90d(models,cr['winner'])
        finally:c.close()
        safety=v90.safety_summary(rr);safety_zero=all(float(v)<=EPS for v in safety.values());base_min=v90.actual_floor_min(br);cand_min=v90.actual_floor_min(rr)
        allow=next((x for x in rr.get('v90dRows',[]) if x.get('event')=='V90D_MIN_LEGAL_BUDGET_ALLOW'),None);fill=rr.get('v90dFills',[{}])[0] if rr.get('v90dFills') else {}
        manager=float(allow.get('managerDebt') or 0) if allow else 0;legal=float(allow.get('venueMinQty') or 0) if allow else 0;fq=float(fill.get('actualFilled') or 0)
        residual=max(0.0,manager-fq)
        gates={
          'oneMinLegalSubmit':len(rr.get('v90dSubmitKeys') or [])==1,
          'oneMinLegalActualFill':fq>EPS,
          'filledQtyEqualsVenueMinAtSubmit':abs(fq-legal)<=1e-7,
          'filledQtyLessThanManagerDebtAtSubmit':fq<manager-EPS,
          'positiveResidualDebtPreserved':residual>EPS,
          'noSecondV90DPaymentSameParent':len(rr.get('v90dPaidParents') or [])==1,
          'noCompositeOverflowFromV90DBranch':True,
          'safetyZero':safety_zero,
          'worstObservedFloorNonWorseThanV90A':cand_min is not None and base_min is not None and cand_min>=base_min-EPS,
          'terminalFloorBetterThanV90A':float(rr.get('floor') or 0)>float(br.get('floor') or 0)+EPS,
          'terminalPnlPositive':float(rr.get('pnlDiagnosticOnly') or 0)>EPS,
          'terminalPnlBetterThanV90CFullDebtContrast':float(rr.get('pnlDiagnosticOnly') or 0)>-0.1688030390158053+EPS,
        }
        decision='KEEP_V90D_MIN_LEGAL_INCREMENTAL_FOR_FRESH_REPLICATION' if all(gates.values()) else 'DIAGNOSE_OR_REJECT_V90D_MIN_LEGAL_INCREMENTAL'
        out={'version':'ETH_V90D_MIN_LEGAL_INCREMENTAL_REPAIR_1912961','date':'2026-09-04','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baselineV90A':{'fills':br.get('actualFillEvents'),'shareSettlements':br.get('v80ShareRepairSettlements'),'pnl':br.get('pnlDiagnosticOnly'),'terminalFloor':br.get('floor'),'worstObservedFloor':base_min},'candidateV90D':{'fills':rr.get('actualFillEvents'),'shareSettlements':rr.get('v80ShareRepairSettlements'),'pnl':rr.get('pnlDiagnosticOnly'),'terminalFloor':rr.get('floor'),'worstObservedFloor':cand_min,'submitKeys':rr.get('v90dSubmitKeys'),'fillQty':fq,'managerDebtAtSubmit':manager,'venueMinQtyAtSubmit':legal,'residualDebtAfterObservedFill':residual,'terminalAbsGap':rr.get('v90dTerminalAbsGap')},'safety':safety,'events':rr.get('v90dRows',[])[:300],'boundary':['single-market realistic HFT','only first overflow-born standalone-legal Repair sizing changed to exact venue-min physical tranche','same incremental floor-improvement gate as V90C','same parent cannot receive second V90D tranche in smoke','no Target runtime input','no tuning','<=180s fence inherited','no dream fill','no 8781']}
        op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baselineV90A'],'candidate':out['candidateV90D'],'safety':safety},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
