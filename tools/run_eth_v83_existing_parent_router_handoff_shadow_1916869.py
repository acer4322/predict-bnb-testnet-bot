from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def sibling(name,path):
    p=Path(path); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

birth=sibling('v83_birth_for_router_shadow',Path(__file__).resolve().with_name('run_eth_v83_expand_fill_repair_responsibility_birth_smoke_1916869.py'))
router=sibling('router_v2_for_existing_parent_shadow',Path(__file__).resolve().with_name('repair_execution_router_v2.py'))
EPS=1e-9; FIXED=1916869; v38=birth.v38; v80=birth.v80

class ExistingParentRouterHandoffShadow(birth.ExpandFillRepairResponsibilityBirth):
    def __init__(self,*a,**kw):
        self.routerHandoffShadow=[]
        super().__init__(*a,**kw)
    def _resolution_active(self):
        return any(p.get('terminal')=='ALREADY_OWNED' for p in getattr(self,'expandFillBirthPending',[]))
    def _maybe_hard_active(self,t):
        rp=getattr(self,'repairParent',None)
        should_log=self._resolution_active() and isinstance(rp,dict)
        ev=None
        if should_log:
            pid=int(rp.get('id')); side=rp.get('side'); born=int(rp.get('bornAt') or -1)
            churn=[x for x in getattr(self,'repairChurn',[]) if int(x.get('parentId') or -1)==pid]
            base=float(getattr(self,'armFillBase',{}).get(pid,self._parent_actual_fill(pid)))
            now=float(self._parent_actual_fill(pid)); progress=now>base+EPS
            pay=self._current_payoffs(); qv=birth.base.v1.quotes(self.book) if hasattr(birth.base,'v1') else None
            ask=None; legal=None
            if qv and side in qv and qv[side].get('ask') is not None:
                ask=float(qv[side]['ask']); legal=(1.0/ask if ask>EPS else math.inf)
            if hasattr(self,'_manager_debt_for_parent'):
                debt=float(self._manager_debt_for_parent(pid,float(pay['gap'])))
            else:
                debt=max(0.0,float(pay['gap']))
            factual=born in getattr(self,'v89OverflowBirthClocks',set())
            common=dict(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,parent_id=pid,parent_side=side,armed=pid in getattr(self,'_armedParents',set()),churn_count=len(churn),payment_progress_since_arm=progress,active_already_owned=pid in getattr(self,'activeByParent',{}),hard_confirmed=pid in getattr(self,'hardConfirmed',set()),floor=float(pay['floor']),manager_debt=debt,live_ask=ask,legal_physical_qty=legal)
            pol=getattr(self,'repairExecutionRouter',None) or router.RecursiveCompositeRepairExecutionRouterV2()
            fd=pol.evaluate(router.RepairExecutionContextV2(overflow_born_parent=factual,**common))
            cd=pol.evaluate(router.RepairExecutionContextV2(overflow_born_parent=True,**common))
            ev={'t':int(t),'parentId':pid,'side':side,'bornAt':born,'factualOverflowBorn':factual,'armed':common['armed'],'churnCount':len(churn),'paymentProgress':progress,'activeAlreadyOwned':common['active_already_owned'],'hardConfirmed':common['hard_confirmed'],'floor':common['floor'],'managerDebt':debt,'liveAsk':ask,'legalPhysicalQty':legal,'factualRouterReason':fd.reason,'factualRouterAllow':bool(fd.allow_active_handoff),'counterfactualOverflowOnlyReason':cd.reason,'counterfactualOverflowOnlyAllow':bool(cd.allow_active_handoff)}
        out=super()._maybe_hard_active(t)
        if ev is not None:
            ev['frozenActualPathReturned']=bool(out)
            prev=self.routerHandoffShadow[-1] if self.routerHandoffShadow else None
            if prev is None or any(prev.get(k)!=ev.get(k) for k in ['factualOverflowBorn','armed','churnCount','paymentProgress','activeAlreadyOwned','hardConfirmed','factualRouterReason','counterfactualOverflowOnlyReason','frozenActualPathReturned']):
                self.routerHandoffShadow.append(ev)
        return out
    def run_candidate(self,models,winner):
        r=super().run_candidate(models,winner)
        r['routerHandoffShadow']=self.routerHandoffShadow[:240]
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    if a.market_id!=FIXED: raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='v83_router_handoff_shadow_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'V83_EXISTING_PARENT_ROUTER_HANDOFF_SHADOW','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{FIXED}.json.xz'
        c=ExistingParentRouterHandoffShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        try: rr=c.run_candidate(models,cr['winner'])
        finally: c.close()
        ss=birth.base.front.safety(rr); cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        rows=rr.get('routerHandoffShadow',[]); cf=[x for x in rows if (not x.get('factualOverflowBorn')) and x.get('counterfactualOverflowOnlyAllow')]
        factual_ordinary=[x for x in rows if x.get('factualRouterReason')=='ORDINARY_PARENT_INHERIT_LEGACY_EXECUTION']
        safety_zero=all(float(v)<=EPS for v in ss.values())
        if rows and factual_ordinary and cf:
            decision='LOCALIZE_RESPONSIBILITY_TO_EXECUTION_FAMILY_HANDOFF'
        elif rows:
            decision='LOCALIZE_OTHER_ROUTER_BLOCKER'
        else:
            decision='NO_POST_RESOLUTION_HARD_ACTIVE_CLOCK_DIAGNOSE_CALL_PATH'
        out={'version':'ETH_V83_EXISTING_PARENT_ROUTER_RESPONSIBILITY_HANDOFF_SHADOW_1916869','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'decision':decision,'gates':{'resolutionAlreadyOwned':any(e.get('terminal')=='ALREADY_OWNED' for e in rr.get('expandFillResponsibilityEvents',[])),'shadowRowsObserved':len(rows)>0,'allocationConservation':cons,'safetyZero':safety_zero},'safety':ss,'routerShadowRows':rows,'ordinaryParentRows':len(factual_ordinary),'counterfactualOverflowOnlyAllows':len(cf),'candidateSummary':birth.slim(rr),'birthEvents':rr.get('expandFillResponsibilityEvents',[]),'boundary':['behavior-inert shadow','counterfactual overflow flag has no action authority','all frozen action behavior preserved','no numeric tuning','realistic HFT only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'rows':len(rows),'ordinaryRows':len(factual_ordinary),'cfAllows':len(cf),'gates':out['gates'],'safety':ss},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
