from __future__ import annotations
import argparse,json,os,shutil,sys,tempfile,zipfile
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9
import tools.run_eth_generation_debt_x_active_rearm_2x2_1945869 as g2
import tools.run_eth_frontier_bounded_passive_repair_ab_1945869 as pb
import tools.run_eth_frontier_disconnected_existing_debt_active_ab_1945869 as ac
pe=g2.pe

class D_BothFrontier(pb.FrontierBoundedPassiveRepairHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.frontierAwareSharedBudget=ac.FrontierAwareSharedBudgetAdapter()
        self.sharedBudgetPolicy=self.frontierAwareSharedBudget
    def _maybe_hard_active(self,t):
        rp=getattr(self,'repairParent',None);bid=None
        if isinstance(rp,dict):
            side=str(rp.get('side') or '')
            try:qv=g2.prev.fcr.basev1.quotes(self.book)
            except Exception:qv=None
            if qv and side in qv and qv[side].get('bid') is not None:bid=float(qv[side]['bid'])
        self.frontierAwareSharedBudget.live_bid=bid
        return super()._maybe_hard_active(t)
    def run_first_carrier_relay(self,models,winner):
        r=super().run_first_carrier_relay(models,winner)
        r.update({'frontierDisconnectedExistingDebtActivePolicy':self.frontierAwareSharedBudget.salvage.name,
                  'frontierDisconnectedActiveEvents':self.frontierAwareSharedBudget.events[:500],
                  'frontierDisconnectedActiveAllows':sum(bool(x.get('allow')) for x in self.frontierAwareSharedBudget.events)})
        return r

def sm(sim,r):
    m=g2.summarize(sim,r);m['frontierPlacementChanges']=int(r.get('frontierPlacementChanges') or 0);m['frontierDisconnectedActiveAllows']=int(r.get('frontierDisconnectedActiveAllows') or 0);return m

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix='frontier_2x2_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
        cells=[]
        for label,cls in [('A_CONTROL',g2.D_Both),('B_PASSIVE_FRONTIER',pb.FrontierBoundedPassiveRepairHFT),('C_ACTIVE_SALVAGE',ac.FrontierDisconnectedSalvageHFT),('D_BOTH',D_BothFrontier)]:
            s=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=s.run_first_carrier_relay(models,cr['winner']);m=sm(s,r)
            finally:s.close()
            cells.append({'cell':label,'metrics':m});print(json.dumps({'cell':label,**{k:m[k] for k in ['fills','pnl','floor','rounds','repairPaid','remainingDebt','parallelRepairActiveFillQty','frontierPlacementChanges','frontierDisconnectedActiveAllows','safe']}},ensure_ascii=False),flush=True)
        by={x['cell']:x['metrics'] for x in cells};A=by['A_CONTROL'];B=by['B_PASSIVE_FRONTIER'];C=by['C_ACTIVE_SALVAGE'];D=by['D_BOTH']
        gates={'allCellsSafe':all(x['safe'] and x['allocationConservation'] and x['allocationBounded'] and x['occupancyBounded'] for x in by.values()),'B_exercised':B['frontierPlacementChanges']>0,'C_exercised':C['frontierDisconnectedActiveAllows']>0,'D_exercises_any':D['frontierPlacementChanges']>0 or D['frontierDisconnectedActiveAllows']>0,'D_floorNonWorseThanA':D['floor']>=A['floor']-1e-7,'D_pnlBetterThanA':D['pnl']>A['pnl']+EPS,'D_repairPaidBetterThanA':D['repairPaid']>A['repairPaid']+EPS or D['rounds']>A['rounds']}
        if not gates['allCellsSafe']:decision='REJECT_FRONTIER_INTERACTION_SAFETY'
        elif D['pnl']>=max(B['pnl'],C['pnl'])-1e-7 and D['floor']>=max(B['floor'],C['floor'])-1e-7 and gates['D_repairPaidBetterThanA']:decision='KEEP_COMPOSED_FRONTIER_EXECUTION_FOR_REPLICATION'
        elif max(B['pnl'],C['pnl'])>A['pnl']+EPS:decision='KEEP_BEST_SINGLE_FRONTIER_CAPABILITY_NOT_COMPOSITION'
        else:decision='NO_ECONOMIC_FRONTIER_IMPROVEMENT'
        out={'version':'ETH_FRONTIER_PASSIVE_X_ACTIVE_SALVAGE_2X2_1945869_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'cells':cells,'boundary':['A frozen generation-debt+Active-rearm','B changes only Passive Repair placement price','C changes only existing-debt Active salvage after passive economic frontier disconnect','D composes B+C with shared frozen accounting/occupancy','no qty/debt/role/ownership/new exposure changes','legacy <=180s Active fence retained','no Target runtime input; winner post-hoc only; realistic HFT; no dream fill; no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
