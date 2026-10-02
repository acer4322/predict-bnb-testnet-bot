from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke

# Diagnostic oracle: for each ordinal PASSIVE_MAINTAIN quote opportunity, skip exactly that
# one opportunity; all later Frozen-R2 decisions remain authoritative. No winner/future input
# is used by the branch itself; terminal PnL is used offline only to value the branch.
def skip_nth_policy(n:int):
    state={'k':0,'skipped':False}
    def fn(ctx):
        if str(ctx.get('desiredAction'))!='PASSIVE_MAINTAIN': return None
        state['k']+=1
        if state['k']==n:
            state['skipped']=True
            return 'wait'
        return None
    fn.state=state
    return fn

def run(mid:int,max_children:int):
    base=run_smoke(mid,passive_mode='offset0',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'})
    bp=float(base['actualExecution']['realizedPnl'])
    rows=[]
    for n in range(1,max_children+1):
        pol=skip_nth_policy(n)
        r=run_smoke(mid,passive_mode='offset0',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},passive_policy=pol)
        if not pol.state['skipped']: break
        p=float(r['actualExecution']['realizedPnl'])
        rows.append({'childOrdinal':n,'skipPnl':p,'deltaVsSubmit':p-bp,'finalAbsNet':r['actualExecution']['combinedFinalAbsNet'],'violations':r['cycleInvariantViolationCount']})
        print(json.dumps({'marketId':mid,**rows[-1]},ensure_ascii=False),flush=True)
    best=max([{'childOrdinal':0,'skipPnl':bp,'deltaVsSubmit':0.0,'finalAbsNet':base['actualExecution']['combinedFinalAbsNet'],'violations':base['cycleInvariantViolationCount']},*rows],key=lambda x:x['skipPnl'])
    return {'marketId':mid,'baselineSubmitAllPnl':bp,'testedChildren':len(rows),'bestSingleSkip':best,'rows':rows}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--max-children',type=int,default=12);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    out=[run(m,a.max_children) for m in mids]
    print(json.dumps({'version':'R2_MAKER_CHILD_TERMINAL_VALUE_ATLAS_V0','researchOnly':True,'dreamFillAllowed':False,'objective':'terminal realized HFT PnL','markets':out,'aggregate':{'baseline':sum(x['baselineSubmitAllPnl'] for x in out),'singleSkipOracle':sum(x['bestSingleSkip']['skipPnl'] for x in out),'delta':sum(x['bestSingleSkip']['skipPnl']-x['baselineSubmitAllPnl'] for x in out)}},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
