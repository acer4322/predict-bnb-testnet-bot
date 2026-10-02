from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9; MID=1912961

def sib(name,file):
    p=Path(__file__).with_name(file); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v90=sib('eth_v90a_for_funnel','run_eth_repair_v90a_bounded_recursive_composite_relay_1912961.py')
v89d=v90.v89d; v80=v90.v80; v38=v90.v38; v1=v90.v1

class Funnel(v90.V90ABoundedRelay):
    def __init__(self,*a,**kw):
        self.funnel=[];self.packageCalls=[];self.physicalCalls=[];self._diagCtx=None
        super().__init__(*a,**kw)
    def _num(self):
        names=['packageRepairEval','packageRepairAccept','packageRepairBlock','packageRepairFallbackEval','payoffInfeasiblePriceBlocks','remainingCapBlocks','globalOwnershipBlocks','submits','actualFillEvents','repairChildCommits','v84CompositeSubmits','v89RecursiveSubmits']
        return {k:float(getattr(self,k,0) or 0) for k in names}
    @staticmethod
    def _delta(b,a):return {k:a[k]-b.get(k,0.0) for k in a if abs(a[k]-b.get(k,0.0))>EPS}
    def _ctx(self,t,qv=None,z=None):
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict): return None
        pid=int(rp.get('id')); born=int(rp.get('bornAt') or -1); side=str(rp.get('side') or '')
        if born not in getattr(self,'v89OverflowBirthClocks',set()) or side not in ('UP','DOWN'): return None
        if qv is None:
            try:qv=v1.quotes(self.book)
            except Exception:return None
        if not qv or side not in qv or qv[side].get('bid') is None:return None
        bid=float(qv[side]['bid']);ask=float(qv[side].get('ask')) if qv[side].get('ask') is not None else None
        legal=1.0/bid if bid>EPS else math.inf
        ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']))
        if not math.isfinite(legal) or gap+EPS<legal:return None
        floor=float(self._raw_floor()[0]);rb=getattr(self,'reserveBuilder',None)
        def lane(role):
            try:return [{'key':str(k),'remaining':float(rem),'side':e.get('side'),'state':e.get('state'),'cancelRequested':bool(e.get('cancelRequested')),'price':e.get('price'),'submittedQty':e.get('submittedQty'),'actualFilled':e.get('actualFilled'),'parentId':e.get('parentId')} for k,e,rem in self.lane_unresolved(role)]
            except Exception as ex:return [{'error':type(ex).__name__+':'+str(ex)}]
        return {'t':int(t),'parentId':pid,'bornAt':born,'side':side,'managerDebt':gap,'bid':bid,'ask':ask,'venueMinQtyAtBid':legal,'secondsLeft':(int(self.capEnd)-int(t))/1000.0,'floor':floor,'z':({'side':z[0],'qty':float(z[1]),'role':str(z[3]),'objectiveId':z[4]} if z is not None else None),'reserveBuilder':({'firstFillAt':rb.get('firstFillAt'),'pairDeadline':rb.get('pairDeadline'),'firstPrice':rb.get('firstPrice'),'repairKeys':list(rb.get('repairKeys') or [])} if isinstance(rb,dict) else None),'repairUnresolved':lane('REPAIR'),'expandUnresolved':lane('EXPAND'),'activeOwned':pid in getattr(self,'activeByParent',{}),'armed':pid in getattr(self,'_armedParents',set())}
    def choose_authorized(self,t,end,proposed_side,proposed_qty):
        z=super().choose_authorized(t,end,proposed_side,proposed_qty)
        c=self._ctx(t,z=z)
        if c is not None and len(self.funnel)<1200:
            c.update({'stage':'CHOOSE_AUTHORIZED','proposedSide':str(proposed_side),'proposedQty':float(proposed_qty),'authorizationReturned':z is not None})
            self.funnel.append(c)
        return z
    def submit(self,t,side,p,q):
        b=self._num();r=super().submit(t,side,p,q);a=self._num()
        if self._diagCtx is not None and len(self.physicalCalls)<300:
            self.physicalCalls.append({'t':int(t),'parentId':self._diagCtx.get('parentId'),'side':str(side),'price':float(p),'qty':float(q),'return':bool(r),'counterDelta':self._delta(b,a)})
        return r
    def _submit_package_at_price(self,t,qv,z,roles_this_tick,p):
        c=self._ctx(t,qv,z)
        prior=self._diagCtx
        if c is not None:self._diagCtx=c
        b=self._num();r=super()._submit_package_at_price(t,qv,z,roles_this_tick,p);a=self._num()
        if c is not None and len(self.packageCalls)<500:
            self.packageCalls.append({**c,'stage':'SUBMIT_PACKAGE','packagePrice':float(p),'return':bool(r),'counterDelta':self._delta(b,a)})
        self._diagCtx=prior
        return r
    def _submit_authorized(self,t,qv,z,roles_this_tick):
        c=self._ctx(t,qv,z)
        prior=self._diagCtx
        if c is not None:self._diagCtx=c
        b=self._num();nphys=len(self.physicalCalls);npkg=len(self.packageCalls)
        r=super()._submit_authorized(t,qv,z,roles_this_tick);a=self._num()
        if c is not None and len(self.funnel)<1200:
            self.funnel.append({**c,'stage':'SUBMIT_AUTHORIZED','return':bool(r),'counterDelta':self._delta(b,a),'newPhysicalCalls':self.physicalCalls[nphys:],'newPackageCalls':self.packageCalls[npkg:]})
        self._diagCtx=prior
        return r

def classify(rows):
    sub=[x for x in rows if x.get('stage')=='SUBMIT_AUTHORIZED']
    choose=[x for x in rows if x.get('stage')=='CHOOSE_AUTHORIZED']
    if not any(x.get('authorizationReturned') and x.get('z',{}).get('role')=='REPAIR' for x in choose):return 'NO_REPAIR_AUTHORIZATION'
    if any(float((x.get('counterDelta') or {}).get('payoffInfeasiblePriceBlocks',0))>0 for x in sub):return 'PAYOFF_BUDGET_INFEASIBLE'
    if any(float((x.get('counterDelta') or {}).get('remainingCapBlocks',0))>0 for x in sub):return 'REMAINING_CAP_OR_VENUE_BLOCK'
    if any(float((x.get('counterDelta') or {}).get('globalOwnershipBlocks',0))>0 for x in sub):return 'OWNERSHIP_OR_OCCUPANCY_BLOCK'
    if any(x.get('newPackageCalls') and not x.get('newPhysicalCalls') for x in sub):return 'PRICE_OR_PACKAGE_PRE_SUBMIT_BLOCK'
    if any(pc.get('return') is False for x in sub for pc in x.get('newPhysicalCalls',[])):return 'PHYSICAL_SUBMIT_REJECTED'
    if any(pc.get('return') is True for x in sub for pc in x.get('newPhysicalCalls',[])):return 'SUBMITTED_NO_FILL_OR_CHURN'
    return 'OTHER'

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=MID:raise ValueError(a.market_id)
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='v90a_funnel_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'V90A_STANDALONE_FUNNEL','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V90A_STANDALONE_FUNNEL_START','market':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        c=Funnel(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        try:rr=c.run_v90a(models,cr['winner'])
        finally:c.close()
        rows=c.funnel;klass=classify(rows);sub=[x for x in rows if x.get('stage')=='SUBMIT_AUTHORIZED'];choose=[x for x in rows if x.get('stage')=='CHOOSE_AUTHORIZED']
        cnt={}
        for x in sub:
            key='RETURN_TRUE' if x.get('return') else 'RETURN_FALSE';cnt[key]=cnt.get(key,0)+1
            for k,v in (x.get('counterDelta') or {}).items():
                if v>0:cnt[k]=cnt.get(k,0)+int(v)
        out={'version':'ETH_V90A_POST_RELAY_STANDALONE_REPAIR_FUNNEL_1912961','date':'2026-09-04','researchOnly':True,'behaviorMutation':False,'marketId':MID,'classification':klass,'summary':{'eligibleChooseClocks':len(choose),'submitAuthorizedClocks':len(sub),'physicalCalls':len(c.physicalCalls),'packageCalls':len(c.packageCalls),'counterReasonCounts':cnt,'terminalFloor':rr.get('floor'),'pnlDiagnosticOnly':rr.get('pnlDiagnosticOnly'),'fills':rr.get('actualFillEvents'),'shareSettlements':rr.get('v80ShareRepairSettlements')},'rows':rows[:1200],'packageCalls':c.packageCalls[:500],'physicalCalls':c.physicalCalls[:300],'boundary':['instrumentation only','V90A behavior unchanged','single market 1912961','realistic HFT','no Target runtime input','no numeric tuning','no 8781']}
        op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'classification':klass,'summary':out['summary'],'firstSubmitRows':sub[:8],'physicalCalls':c.physicalCalls[:8]},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
