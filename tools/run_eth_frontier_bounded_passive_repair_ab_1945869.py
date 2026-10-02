from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,threading,time,zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9

import tools.run_eth_generation_debt_x_active_rearm_2x2_1945869 as g2
import tools.run_eth_v83_same_parent_parallel_repair_hft_smoke_1916869 as v83sp
from tools.eth_repair_modular.frontier_bounded_passive_repair_placement import (
    FrontierBoundedPassiveRepairContext,FrontierBoundedPassiveRepairPlacementPolicyV1,
)
pe=g2.pe

class FrontierBoundedPassiveRepairHFT(g2.D_Both):
    def __init__(self,*a,**kw):
        self.frontierPlacementPolicy=FrontierBoundedPassiveRepairPlacementPolicyV1();self.frontierPlacementEvents=[];self.frontierPlacementChanges=0
        super().__init__(*a,**kw)
    def _project_floor_after_buy(self,side,price,qty):
        pay=self._current_payoffs();up=float(pay.get('up') or 0.0);down=float(pay.get('down') or 0.0);p=float(price);q=float(qty)
        if side=='UP':up+=(1.0-p)*q;down-=p*q
        elif side=='DOWN':down+=(1.0-p)*q;up-=p*q
        else:return None
        return min(up,down)
    def submit(self,t,side,p,q):
        role=str(getattr(self,'_pendingAuthorizedRole',None) or '').upper();pid=getattr(self,'_pendingParentId',None);lane=str(getattr(self,'_pendingLane',None) or '')
        original=float(p)
        if role=='REPAIR' and pid is not None and 'ACTIVE' not in lane.upper():
            try:qv=g2.prev.fcr.basev1.quotes(self.book)
            except Exception:qv=None
            bid=ask=None
            if qv and side in qv:
                bid=qv[side].get('bid');ask=qv[side].get('ask')
            target_guess=None
            if bid is not None:
                target_guess=math.floor((min(float(bid),float(v83sp.ECON_CEILING))+1e-12)/.01)*.01
            projected=self._project_floor_after_buy(str(side),float(target_guess),float(q)) if target_guess is not None and target_guess>EPS else None
            d=self.frontierPlacementPolicy.evaluate(FrontierBoundedPassiveRepairContext(
                objective_role=role,parent_id=int(pid),side=str(side),proposed_price=original,proposed_qty=float(q),
                live_best_bid=None if bid is None else float(bid),live_best_ask=None if ask is None else float(ask),
                inherited_economic_ceiling=float(v83sp.ECON_CEILING),floor_before=float(self._current_payoffs().get('floor') or 0.0),
                projected_floor_at_target=projected,tick_size=.01))
            ev={'t':int(t),'event':'FRONTIER_BOUNDED_PASSIVE_REPAIR_PLACEMENT','parentId':int(pid),'side':side,'lane':lane,
                'originalPrice':original,'qty':float(q),'liveBid':bid,'liveAsk':ask,'economicCeiling':float(v83sp.ECON_CEILING),
                'targetPrice':float(d.target_price),'projectedFloor':d.projected_floor,'reason':d.reason,'changePrice':bool(d.change_price)}
            self.frontierPlacementEvents.append(ev)
            if d.change_price:
                p=float(d.target_price);self.frontierPlacementChanges+=1
        return super().submit(t,side,p,q)
    def run_first_carrier_relay(self,models,winner):
        r=super().run_first_carrier_relay(models,winner);r.update({'frontierBoundedPassiveRepairPlacementPolicy':self.frontierPlacementPolicy.name,'frontierPlacementChanges':self.frontierPlacementChanges,'frontierPlacementEvents':self.frontierPlacementEvents[:500]});return r

def summarise(sim,r):
    m=g2.summarize(sim,r)
    m['frontierPlacementChanges']=int(r.get('frontierPlacementChanges') or 0);m['frontierPlacementEvents']=r.get('frontierPlacementEvents',[])[:160]
    return m

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--control-result');ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix='frontier_bounded_passive_ab_'))
    stop=threading.Event();started=time.time()
    def heartbeat():
        while not stop.wait(10):print(json.dumps({'heartbeat':'FRONTIER_BOUNDED_PASSIVE_AB','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=heartbeat,daemon=True).start();print(json.dumps({'heartbeat':'FRONTIER_BOUNDED_PASSIVE_AB_START','marketId':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz';rows=[]
        cells=[('A_GENERATION_DEBT_REARM',g2.D_Both),('B_FRONTIER_BOUNDED_PASSIVE_REPAIR',FrontierBoundedPassiveRepairHFT)]
        if a.control_result:
            cached=json.load(open(a.control_result,encoding='utf-8'))
            if int(cached.get('marketId') or -1)!=mid:raise ValueError('cached control market mismatch')
            control=next((row.get('metrics') for row in cached.get('cells',[]) if row.get('cell') in ('A_GENERATION_DEBT_REARM','D_BOTH')),None)
            if not isinstance(control,dict):raise ValueError('cached control cell missing')
            rows.append({'cell':'A_GENERATION_DEBT_REARM','metrics':control});cells=cells[1:]
            print(json.dumps({'heartbeat':'FRONTIER_BOUNDED_PASSIVE_CONTROL_REUSED','source':a.control_result}),flush=True)
        for label,cls in cells:
            cell_started=time.time();print(json.dumps({'heartbeat':'FRONTIER_BOUNDED_PASSIVE_CELL_START','cell':label}),flush=True)
            s=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=s.run_first_carrier_relay(models,cr['winner']);m=summarise(s,r)
            finally:s.close()
            rows.append({'cell':label,'metrics':m});print(json.dumps({'heartbeat':'FRONTIER_BOUNDED_PASSIVE_CELL_DONE','elapsedSeconds':round(time.time()-cell_started,1),'cell':label,**{k:v for k,v in m.items() if k not in ('physical','safety','allocationParents','reservationReasons','frontierPlacementEvents')}},ensure_ascii=False),flush=True)
        A=rows[0]['metrics'];B=rows[1]['metrics']
        gates={'controlSafe':A['safe'],'candidateSafe':B['safe'],'placementPolicyExercised':B['frontierPlacementChanges']>0,
               'moreRepairPaymentOrRound':B['repairPaid']>A['repairPaid']+EPS or B['rounds']>A['rounds'],
               'floorNonWorse':B['floor']>=A['floor']-1e-7,'pnlImproves':B['pnl']>A['pnl']+EPS,
               'allocationConservation':B['allocationConservation'],'allocationBounded':B['allocationBounded'],'occupancyBounded':B['occupancyBounded']}
        if not B['safe'] or not B['allocationConservation'] or not B['allocationBounded'] or not B['occupancyBounded']:decision='REJECT_FRONTIER_BOUNDED_PASSIVE_SAFETY'
        elif not gates['placementPolicyExercised']:decision='NO_FRONTIER_BOUNDED_PASSIVE_REACHABILITY'
        elif gates['moreRepairPaymentOrRound'] and gates['floorNonWorse'] and gates['pnlImproves']:decision='KEEP_FRONTIER_BOUNDED_PASSIVE_REPAIR_FOR_ONE_REPLICATION'
        else:decision='FRONTIER_BOUNDED_PASSIVE_FUNCTIONAL_BUT_ECONOMICALLY_INCOMPLETE'
        out={'version':'ETH_FRONTIER_BOUNDED_PASSIVE_REPAIR_AB_1945869_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'cells':rows,'sourceControlResult':a.control_result,
             'boundary':['A frozen generation-debt + generation Active rearm candidate','cached A control is accepted only from the same market and A_GENERATION_DEBT_REARM or D_BOTH cell','B changes only newly submitted Passive Repair child price','qty/debt/role/objective/ownership/Active policy frozen','target price is highest maker-safe grid price <= min(current best bid,inherited economic ceiling)','no copied behind-tick threshold','full-qty exact projected floor must not worsen before price change','<=180/new exposure fences unchanged','winner post-hoc only; no Target runtime input; realistic HFT; no dream fill; no 8781']}
        outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);outp.parent.mkdir(parents=True,exist_ok=True);outp.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'A':{k:A[k] for k in ['fills','pnl','floor','repairPaid','remainingDebt','rounds','safe']},'B':{k:B[k] for k in ['fills','pnl','floor','repairPaid','remainingDebt','rounds','frontierPlacementChanges','safe']}},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
