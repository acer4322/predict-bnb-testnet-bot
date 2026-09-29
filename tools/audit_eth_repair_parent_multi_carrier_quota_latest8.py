from __future__ import annotations
import argparse,json,os,shutil,sys,tempfile,threading,time,zipfile,importlib,importlib.util
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))

def _load_or_staged(fullname,filename):
    try:return importlib.import_module(fullname)
    except ImportError:
        p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(fullname,p)
        if s is None or s.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(s);sys.modules[fullname]=m;s.loader.exec_module(m);return m
_load_or_staged('tools.eth_repair_modular.parent_execution_occupancy','parent_execution_occupancy.py')
_load_or_staged('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
_load_or_staged('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
_load_or_staged('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
_load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg=_load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py')
try:
    from tools.eth_repair_modular.quota_preserving_carrier_ladder import CarrierLadderContext,QuotaPreservingCarrierLadderPolicyV1
except ImportError:
    p=Path(__file__).with_name('quota_preserving_carrier_ladder.py');s=importlib.util.spec_from_file_location('tools.eth_repair_modular.quota_preserving_carrier_ladder',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);CarrierLadderContext=m.CarrierLadderContext;QuotaPreservingCarrierLadderPolicyV1=m.QuotaPreservingCarrierLadderPolicyV1
EPS=1e-9
pe=pg.pe

class MultiCarrierQuotaShadow(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.multiCarrierPolicy=QuotaPreservingCarrierLadderPolicyV1();self.multiCarrierRows=[];self.multiCarrierSeen=set();self._multiBusy=False
    def _visible_passive_prices(self,side):
        b=getattr(self,'book',None)
        if not isinstance(b,dict): return []
        if side=='UP':
            return [float(p) for p in sorted((b.get('bids') or {}).keys(),reverse=True)[:5]]
        if side=='DOWN':
            return [1.0-float(p) for p in sorted((b.get('asks') or {}).keys())[:5]]
        return []
    def _multi_carrier_check(self,t):
        if self._multiBusy:return
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict):return
        pid=int(rp.get('id'));side=str(rp.get('side') or '').upper()
        if side not in ('UP','DOWN'):return
        key=(int(t),pid,self.n,int(getattr(self,'actualFillEvents',0) or 0))
        if key in self.multiCarrierSeen:return
        self.multiCarrierSeen.add(key);self._multiBusy=True
        try:
            self._sync_parent_occupancy();debt=float(self._parent_debt_now(pid));reserved=float(self.parentExecutionOccupancy.parent_reserved(pid));prices=self._visible_passive_prices(side)
            dec=self.multiCarrierPolicy.evaluate(CarrierLadderContext(pid,debt,reserved,prices,3,12.0))
            self.multiCarrierRows.append({'t':int(t),'parentId':pid,'side':side,'authoritativeDebt':debt,'existingReservedQty':reserved,'availableQty':dec.available_before,'visiblePrices':prices,'plannedCarrierCount':len(dec.plans),'plans':[x.__dict__ for x in dec.plans],'totalNewReservedQty':dec.total_new_reserved_qty,'totalReservedAfter':dec.total_reserved_after,'reason':dec.reason,'multiCarrierFeasible':len(dec.plans)>=2})
        finally:self._multiBusy=False
    def process(self,t):
        r=super().process(t);self._multi_carrier_check(int(t));return r
    def run_shadow(self,models,winner):
        r=self.run_guard(models,winner);r['multiCarrierQuotaRows']=self.multiCarrierRows;return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='AUTO');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='multi_carrier_quota_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'MULTI_CARRIER_QUOTA','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTI_CARRIER_QUOTA_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);by={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for i,mid in enumerate(mids,1):
            cr=by[mid];s=pe.make(MultiCarrierQuotaShadow,tmp/'tapes'/f'{mid}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=s.run_shadow(models,cr['winner']);z=list(s.multiCarrierRows)
            finally:s.close()
            multi=[x for x in z if x['multiCarrierFeasible']];one=[x for x in z if x['plannedCarrierCount']==1];zero=[x for x in z if x['plannedCarrierCount']==0]
            rows.append({'marketId':mid,'eligibleRepairParentClocks':len(z),'multiCarrierFeasibleClocks':len(multi),'oneCarrierOnlyClocks':len(one),'zeroCarrierClocks':len(zero),'multiCarrierRate':len(multi)/len(z) if z else None,'maxPlannedCarriers':max((x['plannedCarrierCount'] for x in z),default=0),'medianDebt':sorted([x['authoritativeDebt'] for x in z])[len(z)//2] if z else None,'medianAvailable':sorted([x['availableQty'] for x in z])[len(z)//2] if z else None,'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'sampleMulti':multi[:20]})
            print(json.dumps({'progress':f'{i}/{len(mids)}','marketId':mid,'eligible':len(z),'multi':len(multi),'rate':rows[-1]['multiCarrierRate'],'maxCarriers':rows[-1]['maxPlannedCarriers']},ensure_ascii=False),flush=True)
        eligible=sum(x['eligibleRepairParentClocks'] for x in rows);multi=sum(x['multiCarrierFeasibleClocks'] for x in rows);mw=sum(x['multiCarrierFeasibleClocks']>0 for x in rows);rate=(multi/eligible if eligible else 0.0);support=bool(mw>=2 and rate>=0.05)
        out={'version':'OUR_REPAIR_PARENT_MULTI_CARRIER_QUOTA_FEASIBILITY_LATEST8_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'behaviorChange':False,'summary':{'markets':len(rows),'eligibleRepairParentClocks':eligible,'multiCarrierFeasibleClocks':multi,'marketsWithMultiCarrierFeasibility':mw,'multiCarrierClockRate':rate},'decision':'KEEP_MULTI_CARRIER_AS_REALISTIC_EXECUTION_DENSITY_SEAM' if support else 'KEEP_MULTI_CARRIER_OPPORTUNISTIC_ONLY_PRIORITIZE_MULTI_CYCLE','rows':rows,'boundary':['strict-past behavior-inert shadow','same authoritative parent debt + current occupancy','no order submit','no new debt','no Target runtime input','no dream fill','no 8781']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
