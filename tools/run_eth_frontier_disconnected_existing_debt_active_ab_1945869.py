from __future__ import annotations
import argparse, collections, json, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9

import tools.run_eth_generation_debt_x_active_rearm_2x2_1945869 as g2
import tools.run_eth_v83_same_parent_parallel_repair_hft_smoke_1916869 as v83sp
from tools.eth_repair_modular.parallel_repair_execution_budget import (
    ParallelRepairExecutionDecision, SameParentAggregateParallelRepairPolicyV1,
)
from tools.eth_repair_modular.frontier_disconnected_existing_debt_active import (
    FrontierDisconnectedExistingDebtActiveContext,
    FrontierDisconnectedExistingDebtActivePolicyV1,
)

pe=g2.pe


class FrontierAwareSharedBudgetAdapter:
    name='frontier_aware_shared_budget_adapter_v1'
    def __init__(self):
        self.base=SameParentAggregateParallelRepairPolicyV1()
        self.salvage=FrontierDisconnectedExistingDebtActivePolicyV1()
        self.live_bid=None
        self.events=[]
    def evaluate(self,ctx):
        d=self.base.evaluate(ctx)
        if d.allow_active_parallel_child or d.reason!='ABOVE_INHERITED_ECONOMIC_CEILING':
            return d
        sd=self.salvage.evaluate(FrontierDisconnectedExistingDebtActiveContext(
            parent_id=ctx.parent_id,parent_side=ctx.parent_side,seconds_left=ctx.seconds_left,
            same_parent_debt=ctx.same_parent_debt,live_bid=self.live_bid,live_ask=ctx.live_ask,
            inherited_economic_ceiling=ctx.economic_ceiling,floor_before=ctx.floor_before,
            floor_after_venue_min_repair=ctx.floor_after_epoch_slice_at_live_ask,
            passive_reserved_qty=ctx.passive_reserved_qty,
            other_same_parent_reserved_qty=ctx.other_same_parent_reserved_qty,
            payment_progress_since_epoch=ctx.payment_progress_since_epoch,
            active_already_owned=ctx.active_already_owned,hard_confirmed=ctx.hard_confirmed,
        ))
        self.events.append({'t':int(ctx.t),'liveBid':self.live_bid,'liveAsk':ctx.live_ask,'ceiling':ctx.economic_ceiling,
                            'debt':ctx.same_parent_debt,'floorBefore':ctx.floor_before,
                            'projectedFloor':sd.projected_floor,'baseReason':d.reason,'salvageReason':sd.reason,
                            'allow':bool(sd.allow_active_existing_debt),'qty':float(sd.physical_qty)})
        if not sd.allow_active_existing_debt:
            return d
        reserved=max(0.0,float(ctx.passive_reserved_qty))+max(0.0,float(ctx.other_same_parent_reserved_qty))
        available=max(0.0,float(ctx.same_parent_debt)-reserved)
        return ParallelRepairExecutionDecision(True,float(sd.physical_qty),sd.projected_floor,sd.reason,
                                               reserved_qty=reserved,unreserved_repair_capacity=available)


class FrontierDisconnectedSalvageHFT(g2.D_Both):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.frontierAwareSharedBudget=FrontierAwareSharedBudgetAdapter()
        self.sharedBudgetPolicy=self.frontierAwareSharedBudget
    def _maybe_hard_active(self,t):
        rp=getattr(self,'repairParent',None)
        bid=None
        if isinstance(rp,dict):
            side=str(rp.get('side'))
            try:qv=g2.prev.fcr.basev1.quotes(self.book)
            except Exception:qv=None
            if qv and side in qv and qv[side].get('bid') is not None: bid=float(qv[side]['bid'])
        self.frontierAwareSharedBudget.live_bid=bid
        return super()._maybe_hard_active(t)
    def run_first_carrier_relay(self,models,winner):
        r=super().run_first_carrier_relay(models,winner)
        r.update({'frontierDisconnectedExistingDebtActivePolicy':self.frontierAwareSharedBudget.salvage.name,
                  'frontierDisconnectedActiveEvents':self.frontierAwareSharedBudget.events[:500],
                  'frontierDisconnectedActiveAllows':sum(bool(x.get('allow')) for x in self.frontierAwareSharedBudget.events)})
        return r


def summarize(sim,r):
    m=g2.summarize(sim,r)
    m['frontierDisconnectedActiveAllows']=int(r.get('frontierDisconnectedActiveAllows') or 0)
    m['frontierDisconnectedActiveEvents']=r.get('frontierDisconnectedActiveEvents',[])[:120]
    return m


def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--control-result');ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='frontier_existing_debt_active_ab_'))
    stop=threading.Event();started=time.time()
    def heartbeat():
        while not stop.wait(10):print(json.dumps({'heartbeat':'FRONTIER_DISCONNECTED_ACTIVE_AB','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=heartbeat,daemon=True).start();print(json.dumps({'heartbeat':'FRONTIER_DISCONNECTED_ACTIVE_AB_START','marketId':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
        rows=[]
        cells=[('A_GENERATION_DEBT_REARM',g2.D_Both),('B_FRONTIER_DISCONNECTED_EXISTING_DEBT_ACTIVE',FrontierDisconnectedSalvageHFT)]
        if a.control_result:
            cached=json.load(open(a.control_result,encoding='utf-8'))
            if int(cached.get('marketId') or -1)!=mid:raise ValueError('cached control market mismatch')
            control=next((row.get('metrics') for row in cached.get('cells',[]) if row.get('cell') in ('A_GENERATION_DEBT_REARM','D_BOTH')),None)
            if not isinstance(control,dict):raise ValueError('cached control cell missing')
            rows.append({'cell':'A_GENERATION_DEBT_REARM','metrics':control});cells=cells[1:]
            print(json.dumps({'heartbeat':'FRONTIER_DISCONNECTED_ACTIVE_CONTROL_REUSED','source':a.control_result}),flush=True)
        for label,cls in cells:
            cell_started=time.time();print(json.dumps({'heartbeat':'FRONTIER_DISCONNECTED_ACTIVE_CELL_START','cell':label}),flush=True)
            s=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=s.run_first_carrier_relay(models,cr['winner']);m=summarize(s,r)
            finally:s.close()
            rows.append({'cell':label,'metrics':m});print(json.dumps({'heartbeat':'FRONTIER_DISCONNECTED_ACTIVE_CELL_DONE','elapsedSeconds':round(time.time()-cell_started,1),'cell':label,**{k:v for k,v in m.items() if k not in ('physical','safety','allocationParents','frontierDisconnectedActiveEvents')}},ensure_ascii=False),flush=True)
        A=rows[0]['metrics'];B=rows[1]['metrics']
        gates={
            'controlSafe':bool(A['safe']),
            'candidateSafe':bool(B['safe']),
            'salvagePolicyExercised':B['frontierDisconnectedActiveAllows']>0,
            'additionalRepairPayment':B['repairPaid']>A['repairPaid']+EPS,
            'additionalPhysicalRepair':B['parallelRepairActiveFillQty']>A['parallelRepairActiveFillQty']+EPS or B['fills']>A['fills'],
            'floorImproves':B['floor']>A['floor']+EPS,
            'pnlImproves':B['pnl']>A['pnl']+EPS,
            'allocationConservation':bool(B['allocationConservation']),
            'allocationBounded':bool(B['allocationBounded']),
            'occupancyBounded':bool(B['occupancyBounded']),
        }
        if not B['safe'] or not B['allocationConservation'] or not B['allocationBounded'] or not B['occupancyBounded']:
            decision='REJECT_FRONTIER_EXISTING_DEBT_ACTIVE_SAFETY'
        elif not gates['salvagePolicyExercised']:
            decision='NO_FRONTIER_DISCONNECTED_SALVAGE_REACHABILITY'
        elif gates['floorImproves'] and gates['pnlImproves'] and gates['additionalRepairPayment']:
            decision='KEEP_FRONTIER_DISCONNECTED_EXISTING_DEBT_ACTIVE_FOR_ONE_REPLICATION'
        else:
            decision='FUNCTIONAL_BUT_ECONOMICALLY_INCOMPLETE_FRONTIER_SALVAGE'
        out={'version':'ETH_FRONTIER_DISCONNECTED_EXISTING_DEBT_ACTIVE_AB_1945869_V1','date':'2026-09-05','researchOnly':True,
             'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'cells':rows,'sourceControlResult':a.control_result,
             'boundary':['A is frozen generation-debt + generation Active rearm candidate','cached A control is accepted only from the same market and A_GENERATION_DEBT_REARM or D_BOTH cell','B adds only existing-debt Active salvage after structural passive frontier disconnect','frontier disconnect = current best bid above inherited execution economic ceiling; no copied behind-tick threshold','old Passive/other sibling reservation must be zero before Active','venue-min overflow disabled in salvage V1','exact projected worst-case floor must strictly improve','legacy <=180s Active fence preserved in this test','no new speculative responsibility','winner post-hoc only; no Target runtime input; realistic HFT; no dream fill; no 8781']}
        outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);outp.parent.mkdir(parents=True,exist_ok=True);outp.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'gates':gates,'A':{k:A[k] for k in ['fills','pnl','floor','repairPaid','remainingDebt','parallelRepairActiveFillQty','safe']},'B':{k:B[k] for k in ['fills','pnl','floor','repairPaid','remainingDebt','parallelRepairActiveFillQty','frontierDisconnectedActiveAllows','safe']}},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
