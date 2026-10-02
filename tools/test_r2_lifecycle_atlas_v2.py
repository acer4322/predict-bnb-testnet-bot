from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'

def policy(name):
    def fn(ctx):
        a=str(ctx['desiredAction']); side=str(ctx['side']); p=ctx['portfolio'] or {}
        if a=='PASSIVE_REPAIR': return 'offset0'
        if a!='PASSIVE_MAINTAIN': return 'wait'
        gross=float(p.get('maker_gross') or 0.0); net=float(p.get('maker_net') or 0.0)
        if name=='FIRST_FILL_THEN_REPAIR_ONLY':
            return 'wait' if gross>1e-9 else 'offset0'
        if name=='NO_SAME_SIDE_EXPANSION':
            if net>1e-9 and side=='UP': return 'wait'
            if net<-1e-9 and side=='DOWN': return 'wait'
            return 'offset0'
        if name=='POST_FILL_COOLDOWN_3S':
            age=p.get('last_maker_age_ms')
            try: age=float(age)
            except: age=1e12
            return 'wait' if age<3000 else 'offset0'
        raise ValueError(name)
    return fn

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--policy',choices=['FIRST_FILL_THEN_REPAIR_ONLY','NO_SAME_SIDE_EXPANSION','POST_FILL_COOLDOWN_3S']);args=ap.parse_args()
    names=[args.policy] if args.policy else ['FIRST_FILL_THEN_REPAIR_ONLY','NO_SAME_SIDE_EXPANSION','POST_FILL_COOLDOWN_3S']
    rows=[]
    for n in names:
        r=run_smoke(args.market_id,passive_mode='offset0',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},passive_policy=policy(n))
        rows.append({'policy':n,'pnl':r['actualExecution']['realizedPnl'],'floor':r['actualExecution']['finalPortfolio']['worst_case_floor'],'absNet':r['actualExecution']['combinedFinalAbsNet'],'makerFilled':r['actualExecution']['makerFilledShares'],'takerFilled':r['actualExecution']['takerFilledShares'],'violations':r['cycleInvariantViolationCount']})
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    out={'version':'R2_LIFECYCLE_ATLAS_V2_SMOKE','researchOnly':True,'dreamFillAllowed':False,'marketId':args.market_id,'newActionFamilies':names,'rows':rows,'best':max(rows,key=lambda x:float(x['pnl']))}
    path=OUT/f'r2_lifecycle_atlas_v2_market{args.market_id}.json';path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(path),'best':out['best']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
