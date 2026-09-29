from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_cap100_closed_loop_v0 as cl
from src.predict_bot import unified_controller_cap100_shadow_v1 as mod

MID=1513668
SIZES=[3.0,4.0,5.0,6.0,9.0,18.0]

def run_size(sz:float):
    old=mod.SHARES
    mod.SHARES=float(sz)
    try:
        r=cl.run_market(MID,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_mode='hft',diagnostic_only=False)
    finally:
        mod.SHARES=old
    c=r['closedLoop']; led=c['ledger']; rm=c['runMetrics']; dec=r['decisionRows']
    maker_times=[int(x['decisionMs']) for x in dec if str(x.get('executionChoice'))=='MAKER']
    taker_times=[int(x['decisionMs']) for x in dec if str(x.get('executionChoice'))=='TAKER']
    first=int(dec[0]['decisionMs']) if dec else None
    last_maker=max(maker_times) if maker_times else None
    return {
      'childShares':sz,
      'makerPlacements':c['makerPlacements'],
      'makerFillEvents':c['makerFillEvents'],
      'makerFilledShares':c['makerFilledShares'],
      'makerCostUsdt':led['makerCostUsdt'],
      'takerFills':c['takerFills'],
      'takerCostUsdt':led['takerCostUsdt'],
      'makerCapBlocks':rm['makerCapBlocks'],
      'takerCapBlocks':rm['takerCapBlocks'],
      'residualWakes':rm['residualWakes'],
      'unresolvedGuardEntries':rm['unresolvedGuardEntries'],
      'finalAbsNet':c['finalPortfolio']['combined_abs_net'],
      'realizedPnlUsdt':led['realizedPnlUsdt'],
      'lastMakerDecisionOffsetMs':(last_maker-first) if last_maker is not None and first is not None else None,
      'firstTakerDecisionOffsetMs':(min(taker_times)-first) if taker_times and first is not None else None,
    }

def main():
    rows=[]
    for sz in SIZES:
        x=run_size(sz); rows.append(x); print(json.dumps(x,ensure_ascii=False),flush=True)
    out={'version':'CAP100_CHILD_SCALE_LIFECYCLE_1513668_V0','marketId':MID,'purpose':'structural lifecycle sensitivity only; not HFT PnL graduation evidence','rows':rows}
    p=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_child_scale_lifecycle_1513668_v0.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'out':str(p)},ensure_ascii=False))
if __name__=='__main__':main()
