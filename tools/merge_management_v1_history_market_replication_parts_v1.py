from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--inputs',nargs='+',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    parts=[json.loads(Path(p).read_text(encoding='utf-8')) for p in a.inputs];rows=[]
    for p in parts:rows.extend(p.get('rows') or [])
    mids=[int(r['marketId']) for r in rows]
    if len(mids)!=len(set(mids)):raise RuntimeError('duplicate markets across parts')
    valid=[r for r in rows if r.get('valid')]
    def sums(branch,key):return sum(float(r['terminalDeltaVsMarketDirection'][branch][key]) for r in valid)
    agg={'states':len(rows),'validStates':len(valid),'marketTargetFilledStates':sum(r['targetExecution']['MARKET_DIRECTION']['physicalFillQty']>1e-9 for r in valid),'historyTargetFilledStates':sum(r['targetExecution']['HISTORY_DIRECTION']['physicalFillQty']>1e-9 for r in valid),'bothTargetFilledStates':sum(r['targetExecution']['MARKET_DIRECTION']['physicalFillQty']>1e-9 and r['targetExecution']['HISTORY_DIRECTION']['physicalFillQty']>1e-9 for r in valid),'historyMinusMarket':{'sumDeltaFloor':sums('HISTORY_DIRECTION','floor'),'sumDeltaBest':sums('HISTORY_DIRECTION','best'),'sumDeltaGap':sums('HISTORY_DIRECTION','gap'),'sumDeltaFills':sums('HISTORY_DIRECTION','fills'),'sumDeltaBuyNotional':sums('HISTORY_DIRECTION','buyNotional'),'floorImprovedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['floor']>1e-9 for r in valid),'floorHarmedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['floor']<-1e-9 for r in valid),'bestImprovedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['best']>1e-9 for r in valid),'bestHarmedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['best']<-1e-9 for r in valid)},'holdMinusMarket':{'sumDeltaFloor':sums('HOLD','floor'),'sumDeltaBest':sums('HOLD','best'),'sumDeltaGap':sums('HOLD','gap')}}
    out={'version':'MANAGEMENT_TRAINING_V1_HISTORY_VS_MARKET_DIRECTION_REPLICATION_MERGED_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'stateCount':len(rows),'allCorrectnessPass':len(valid)==len(rows) and all(bool(p.get('allCorrectnessPass')) for p in parts),'aggregate':agg,'rows':sorted(rows,key=lambda r:(int(r['marketId']),int(r['t']))),'sourceParts':a.inputs,'boundary':['merge only; no row filtering or outcome selection','same exact-fork semantics as source parts','consumed first eligible state per market','no NEW24-B/no 8781']}
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'states':len(rows),'allCorrectnessPass':out['allCorrectnessPass'],'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
