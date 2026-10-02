from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,threading,time,zipfile,importlib,importlib.util
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9

def load_or_staged(fullname,filename):
    print(f'IMPORT_STAGE {fullname} START',flush=True)
    try:
        m=importlib.import_module(fullname);print(f'IMPORT_STAGE {fullname} PASS project',flush=True);return m
    except ImportError:
        p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(fullname,p)
        if s is None or s.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(s);sys.modules[fullname]=m;s.loader.exec_module(m);print(f'IMPORT_STAGE {fullname} PASS staged',flush=True);return m

load_or_staged('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
load_or_staged('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
load_or_staged('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg=load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py')
try:
    from tools.eth_repair_modular.quota_preserving_carrier_ladder import QuotaPreservingCarrierLadderPolicyV1,CarrierLadderContext
except ImportError:
    q=load_or_staged('tools.eth_repair_modular.quota_preserving_carrier_ladder','quota_preserving_carrier_ladder.py')
    QuotaPreservingCarrierLadderPolicyV1=q.QuotaPreservingCarrierLadderPolicyV1;CarrierLadderContext=q.CarrierLadderContext

pe=pg.pe
v1=pe.v38.v36.v34.v30.v1

class MultiCarrierFeasibilityShadow(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.ladderPolicy=QuotaPreservingCarrierLadderPolicyV1();self.ladderRows=[];self._ladderSeen=set()
    def _phase(self,t):
        start=int(self.capEnd)-300000
        return min(1.0,max(0.0,(int(t)-start)/300000.0))
    def _audit_ladder(self,t):
        rp=getattr(self,'repairParent',None)
        if not isinstance(rp,dict):return
        pid=int(rp.get('id'));side=str(rp.get('side') or '').upper()
        if side not in ('UP','DOWN'):return
        key=(int(t),pid)
        if key in self._ladderSeen:return
        self._ladderSeen.add(key)
        qv=v1.quotes(self.book)
        if not qv:return
        self._sync_parent_occupancy()
        debt=float(self._parent_debt_now(pid));reserved=float(self.parentExecutionOccupancy.parent_reserved(pid))
        bid=float(qv[side]['bid'])
        prices=[bid]
        for off in (0.01,0.02):
            p=round(bid-off,10)
            if p>EPS:prices.append(p)
        d=self.ladderPolicy.evaluate(CarrierLadderContext(parent_id=pid,authoritative_debt=debt,existing_reserved_qty=reserved,candidate_prices=prices,max_new_carriers=3,max_venue_qty=12.0))
        self.ladderRows.append({'t':int(t),'normalizedPhase':self._phase(t),'parentId':pid,'side':side,'authoritativeDebt':debt,'existingReservedQty':reserved,'availableBefore':d.available_before,'candidatePrices':prices,'feasibleCarrierCount':len(d.plans),'totalNewReservedQty':d.total_new_reserved_qty,'totalReservedAfter':d.total_reserved_after,'reason':d.reason,'plans':[x.__dict__ for x in d.plans]})
    def process(self,t):
        out=super().process(t);self._audit_ladder(int(t));return out
    def run_shadow(self,models,winner):
        r=self.run_guard(models,winner);r['multiCarrierFeasibilityRows']=self.ladderRows;return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='AUTO');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='multi_carrier_shadow_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'MULTI_CARRIER_SHADOW','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTI_CARRIER_SHADOW_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);by={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for i,mid in enumerate(mids,1):
            cr=by[mid];sim=pe.make(MultiCarrierFeasibilityShadow,tmp/'tapes'/f'{mid}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=sim.run_shadow(models,cr['winner']);lr=list(sim.ladderRows)
            finally:sim.close()
            ge1=sum(x['feasibleCarrierCount']>=1 for x in lr);ge2=sum(x['feasibleCarrierCount']>=2 for x in lr);ge3=sum(x['feasibleCarrierCount']>=3 for x in lr)
            phases=[x['normalizedPhase'] for x in lr if x['feasibleCarrierCount']>=2]
            row={'marketId':mid,'liveParentClocks':len(lr),'ge1CarrierClocks':ge1,'ge2CarrierClocks':ge2,'ge3CarrierClocks':ge3,'ge2RunsRepeated':ge2>=3,'ge2PhaseMin':min(phases,default=None),'ge2PhaseMedian':sorted(phases)[len(phases)//2] if phases else None,'ge2PhaseMax':max(phases,default=None),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'sampleMulti':[x for x in lr if x['feasibleCarrierCount']>=2][:30]}
            rows.append(row);print(json.dumps({'progress':f'{i}/{len(mids)}','marketId':mid,'liveParentClocks':len(lr),'ge2':ge2,'ge3':ge3,'fills':row['fills'],'rounds':row['rounds']},ensure_ascii=False),flush=True)
        total2=sum(x['ge2CarrierClocks'] for x in rows);total3=sum(x['ge3CarrierClocks'] for x in rows);markets2=sum(x['ge2CarrierClocks']>0 for x in rows);repeated=sum(x['ge2RunsRepeated'] for x in rows)
        summary={'markets':len(rows),'marketsWithGE2CarrierFeasibility':markets2,'marketsWithRepeatedGE2Feasibility':repeated,'ge2CarrierClocks':total2,'ge3CarrierClocks':total3}
        out={'version':'QUOTA_PRESERVING_MULTI_CARRIER_FEASIBILITY_LATEST8_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'behaviorChange':False,'summary':summary,'decision':'SUPPORT_MULTI_CARRIER_BEHAVIOR_SMOKE' if markets2>=2 and repeated>=2 else 'MULTI_CARRIER_NOT_YET_BROADLY_SUPPORTED','rows':rows,'boundary':['behavior inert','candidate price ladder is development geometry only: best bid, bid-0.01, bid-0.02','aggregate proposed unresolved reservation never exceeds authoritative parent debt','no debt creation','no Target runtime input','no dream fill','no 8781']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'summary':summary},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
