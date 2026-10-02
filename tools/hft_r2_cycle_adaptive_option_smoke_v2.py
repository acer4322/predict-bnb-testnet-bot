from __future__ import annotations
import copy, math, time
from pathlib import Path
from typing import Any
from tools import hft_r2_cycle_preserving_execution_smoke_v1 as smoke
base=smoke.base
EPS=smoke.EPS

def run_adaptive_smoke_v2(market_id:int, min_maintain_steps:int=20, maintain_mode:str='offset2')->dict[str,Any]:
    logic_state={'desiredPortfolioAction':None,'maintainStreak':0}
    original_new_controller=base.new_controller
    original_joblib_load=base.joblib.load
    original_passive_quote=base.r2_passive_quote
    traces=[]
    def traced_new_controller(adapter:Any)->Any:
        controller=original_new_controller(adapter)
        original_step=controller._step
        def traced_step(snapshot:dict[str,Any])->Any:
            at_ms=int(snapshot['sampledAtMs'])
            result=original_step(snapshot)
            decision=copy.deepcopy(controller.last_decision)
            if decision is not None and int(decision.get('decisionMs') or -1)==at_ms:
                desired=decision.get('desiredPortfolioAction')
                if desired=='PASSIVE_MAINTAIN':
                    logic_state['maintainStreak']=int(logic_state['maintainStreak'])+1
                else:
                    logic_state['maintainStreak']=0
                logic_state['desiredPortfolioAction']=desired
            traces.append({'atMs':at_ms,'decision':decision})
            return result
        controller._step=traced_step
        return controller
    def fixed_load(path:Any,*args:Any,**kwargs:Any)->Any:
        if Path(path).resolve()==base.MODEL_PATH.resolve():
            return smoke._fixed_keep_artifact()
        return original_joblib_load(path,*args,**kwargs)
    def quote(book:dict[str,dict[float,float]],side:str,opposite_price:float|None)->float|None:
        if logic_state.get('desiredPortfolioAction')!='PASSIVE_MAINTAIN':
            return None
        if int(logic_state.get('maintainStreak') or 0)<int(min_maintain_steps):
            return None
        book_features=base.mod.outcome_book(book,None)
        if not book_features:
            return None
        offset_ticks=int(maintain_mode[-1])
        bid=float(book_features['up_bid'] if side=='UP' else book_features['down_bid'])
        tick=int(math.floor((bid+1e-9)/base.mod.GRID))-offset_ticks
        tick=max(int(round(base.mod.MIN_PRICE/base.mod.GRID)),tick)
        price=round(tick*base.mod.GRID,2)
        if opposite_price is not None:
            while price+float(opposite_price)>base.mod.MAX_PAIR_PRICE_SUM+EPS:
                tick-=1
                if tick<int(round(base.mod.MIN_PRICE/base.mod.GRID)):
                    return None
                price=round(tick*base.mod.GRID,2)
        return price
    started=time.perf_counter()
    base.new_controller=traced_new_controller; base.joblib.load=fixed_load; base.r2_passive_quote=quote
    try:
        row=base.run_market(int(market_id))
    finally:
        base.new_controller=original_new_controller; base.joblib.load=original_joblib_load; base.r2_passive_quote=original_passive_quote
    decisions=[t['decision'] for t in traces if isinstance(t.get('decision'),dict)]
    return {'marketId':int(market_id),'minMaintainSteps':int(min_maintain_steps),'maintainMode':maintain_mode,'runtimeSeconds':time.perf_counter()-started,'realizedPnl':float(row['actualExecution']['realizedPnl']),'worstCaseFloor':float(row['actualExecution']['finalPortfolio']['worst_case_floor']),'finalAbsTrackingError':float(row['actualExecution']['finalAbsTrackingError']),'combinedFinalAbsNet':float(row['actualExecution']['combinedFinalAbsNet']),'makerFilledShares':float(row['actualExecution']['makerFilledShares']),'takerFilledShares':float(row['actualExecution']['takerFilledShares']),'desiredPortfolioActionCounts':smoke._action_counts(decisions,'desiredPortfolioAction')}
