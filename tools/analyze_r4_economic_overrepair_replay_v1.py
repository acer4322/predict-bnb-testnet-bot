from __future__ import annotations
import argparse,json,math,sys
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.run_r4_complexity_pruning_m1_completion_fastpath_v9 import run_overlay

EPS=1e-9
MODE='LITE_NO_M1_COMPLETION_FASTPATH'

def safe(x,d=0.0):
    try:
        v=float(x); return v if math.isfinite(v) else d
    except Exception:return d

def fill_stream(rep):
    xs=[]
    for x in rep.get('makerFillEvents') or []:
        xs.append({'atMs':int(x.get('atMs') or x.get('observedAtMs') or 0),'role':'MAKER','side':str(x.get('side')),'price':safe(x.get('price')),'shares':safe(x.get('deltaShares')),'fee':0.0,'source':x})
    for x in rep.get('takerEvents') or []:
        xs.append({'atMs':int(x.get('atMs') or 0),'role':'TAKER','side':str(x.get('side')),'price':safe(x.get('price')),'shares':safe(x.get('shares')),'fee':safe(x.get('feeUsdt')),'source':x})
    return sorted(xs,key=lambda z:(z['atMs'],0 if z['role']=='MAKER' else 1))

def audit_rep(mid,variant,rep):
    up=dn=upc=dnc=fees=0.0; rows=[]
    for i,e in enumerate(fill_stream(rep)):
        q=e['shares'];p=e['price'];side=e['side'];role=e['role'];fee=e['fee']
        if q<=EPS or side not in {'UP','DOWN'}: continue
        gap=up-dn; abs_gap=abs(gap); gross=up+dn; cost=upc+dnc
        pre_floor=min(up,dn)-cost; pre_upside=max(up,dn)-cost; pre_abs=abs_gap
        weak='DOWN' if gap>EPS else 'UP' if gap<-EPS else None
        strong='UP' if gap>EPS else 'DOWN' if gap<-EPS else None
        semantic='REPAIR' if weak and side==weak else 'ADD' if strong and side==strong else 'FLAT_START'
        strong_sh=up if strong=='UP' else dn if strong=='DOWN' else 0.0
        strong_cost=upc if strong=='UP' else dnc if strong=='DOWN' else 0.0
        strong_avg=(strong_cost/strong_sh) if strong_sh>EPS else None
        pair_cost_proxy=(strong_avg+p) if semantic=='REPAIR' and strong_avg is not None else None
        overshoot=max(0.0,q-abs_gap) if semantic=='REPAIR' else 0.0
        if side=='UP': up+=q;upc+=q*p
        else:dn+=q;dnc+=q*p
        fees+=fee
        post_cost=upc+dnc;post_floor=min(up,dn)-post_cost;post_upside=max(up,dn)-post_cost;post_abs=abs(up-dn)
        dfl=post_floor-pre_floor; dup=post_upside-pre_upside; dabs=post_abs-pre_abs
        floor_gain=max(0.0,dfl);upside_cost=max(0.0,-dup);eff=(floor_gain/upside_cost) if upside_cost>EPS else None
        hard=bool(semantic=='REPAIR' and dfl < -1e-9)
        floor_break=bool(semantic=='REPAIR' and pre_floor>1e-9 and post_floor<=1e-9)
        expensive=bool(semantic=='REPAIR' and pair_cost_proxy is not None and pair_cost_proxy>1.0+1e-9)
        poor=bool(semantic=='REPAIR' and expensive and post_floor<=0.0+1e-9)
        rows.append({'marketId':mid,'variant':variant,'fillIndex':i,'atMs':e['atMs'],'role':role,'side':side,'price':p,'shares':q,'feeUsdt':fee,'semantic':semantic,'preGap':gap,'preAbsNet':pre_abs,'postAbsNet':post_abs,'deltaAbsNet':dabs,'preFloor':pre_floor,'postFloor':post_floor,'deltaFloor':dfl,'preUpside':pre_upside,'postUpside':post_upside,'deltaUpside':dup,'overshootQty':overshoot,'strongAvgCostProxy':strong_avg,'marginalPairCostProxy':pair_cost_proxy,'floorGainPerUpsideCost':eff,'hardOverRepair':hard,'positiveFloorBreak':floor_break,'expensivePairProxy':expensive,'poorRepairProxy':poor})
    fp=(rep.get('studentRollout') or {}).get('finalPortfolio') or {}
    return rows,{'marketId':mid,'variant':variant,'fills':len(rows),'repairFills':sum(r['semantic']=='REPAIR' for r in rows),'makerRepairFills':sum(r['semantic']=='REPAIR' and r['role']=='MAKER' for r in rows),'takerRepairFills':sum(r['semantic']=='REPAIR' and r['role']=='TAKER' for r in rows),'hardOverRepair':sum(r['hardOverRepair'] for r in rows),'positiveFloorBreak':sum(r['positiveFloorBreak'] for r in rows),'expensivePairProxy':sum(r['expensivePairProxy'] for r in rows),'poorRepairProxy':sum(r['poorRepairProxy'] for r in rows),'finalFloor':safe(fp.get('worst_case_floor')),'finalAbsNet':safe(fp.get('combined_abs_net'))}

def aggregate(rows,markets):
    rr=[r for r in rows if r['semantic']=='REPAIR']; hard=[r for r in rr if r['hardOverRepair']];exp=[r for r in rr if r['expensivePairProxy']];poor=[r for r in rr if r['poorRepairProxy']]
    def role_counts(z):return dict(Counter(r['role'] for r in z))
    eff=[r['floorGainPerUpsideCost'] for r in rr if r['floorGainPerUpsideCost'] is not None]
    pc=[r['marginalPairCostProxy'] for r in rr if r['marginalPairCostProxy'] is not None]
    return {'markets':len(markets),'fills':len(rows),'repairFills':len(rr),'repairMarkets':len(set(r['marketId'] for r in rr)),'hardOverRepairFills':len(hard),'hardOverRepairMarkets':len(set(r['marketId'] for r in hard)),'positiveFloorBreakFills':sum(r['positiveFloorBreak'] for r in rr),'positiveFloorBreakMarkets':len(set(r['marketId'] for r in rr if r['positiveFloorBreak'])),'expensivePairProxyFills':len(exp),'expensivePairProxyMarkets':len(set(r['marketId'] for r in exp)),'poorRepairProxyFills':len(poor),'poorRepairProxyMarkets':len(set(r['marketId'] for r in poor)),'repairByRole':role_counts(rr),'hardByRole':role_counts(hard),'expensiveByRole':role_counts(exp),'meanRepairDeltaFloor':float(np.mean([r['deltaFloor'] for r in rr])) if rr else None,'medianRepairDeltaFloor':float(np.median([r['deltaFloor'] for r in rr])) if rr else None,'meanRepairDeltaUpside':float(np.mean([r['deltaUpside'] for r in rr])) if rr else None,'meanRepairDeltaAbsNet':float(np.mean([r['deltaAbsNet'] for r in rr])) if rr else None,'medianPairCostProxy':float(np.median(pc)) if pc else None,'p90PairCostProxy':float(np.quantile(pc,.9)) if pc else None,'medianFloorGainPerUpsideCost':float(np.median(eff)) if eff else None,'worstHardEvents':sorted(hard,key=lambda r:r['deltaFloor'])[:10],'highestPairCostEvents':sorted(exp,key=lambda r:r['marginalPairCostProxy'],reverse=True)[:10]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets'
    ids=json.loads(Path(a.ids_json).read_text());allrows={'R3':[],'R4_LITE':[]};markets={'R3':[],'R4_LITE':[]};errs=[]
    for mid in map(int,ids):
        for variant in ['R3','R4_LITE']:
            try:
                rep=r3ctl.run_market(mid,True) if variant=='R3' else run_overlay(mid,MODE,[])
                rows,ms=audit_rep(mid,variant,rep);allrows[variant].extend(rows);markets[variant].append(ms)
                print(json.dumps({'marketId':mid,'variant':variant,'repairFills':ms['repairFills'],'hardOverRepair':ms['hardOverRepair'],'positiveFloorBreak':ms['positiveFloorBreak'],'expensivePairProxy':ms['expensivePairProxy']},ensure_ascii=False),flush=True)
            except Exception as e:
                errs.append({'marketId':mid,'variant':variant,'error':f'{type(e).__name__}:{e}'})
    out={'version':'R4_ECONOMIC_OVERREPAIR_REPLAY_V1','researchOnly':True,'definitions':{'hardOverRepair':'actual repair fill where post-fill economic floor is lower than pre-fill floor','positiveFloorBreak':'repair fill changes floor from >0 to <=0','expensivePairProxy':'repair fill price + current strong-side average cost > 1; proxy only, not FIFO marginal attribution','poorRepairProxy':'expensivePairProxy and post-fill floor <=0','floorGainPerUpsideCost':'max(deltaFloor,0)/max(-deltaUpside,0)'},'R3':{'aggregate':aggregate(allrows['R3'],markets['R3']),'markets':markets['R3'],'events':allrows['R3']},'R4_LITE':{'aggregate':aggregate(allrows['R4_LITE'],markets['R4_LITE']),'markets':markets['R4_LITE'],'events':allrows['R4_LITE']},'errors':errs,'warning':'Descriptive realized-fill anatomy on consumed realistic-HFT cohort. Pair cost is an average-cost proxy; causal action-value requires branch replay.'}
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'R3':out['R3']['aggregate'],'R4_LITE':out['R4_LITE']['aggregate'],'errors':errs},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
